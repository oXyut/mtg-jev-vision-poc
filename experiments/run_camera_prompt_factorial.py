"""Issue #23: fixed 2x2 wording experiment. Default is an API-free plan.

Use an output directory outside version control. Inputs, responses, predictions
and metadata are private. No truth argument exists; score in a separate process.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mtg_jev.camera_server import AccountedFrameClient, CameraConfig, CameraState
from mtg_jev.images import prepare
from mtg_jev.masked_search import MaskSettings, detect_masked
from mtg_jev.prompt_factorial import BASE_COMMIT, CONDITIONS, factories, instructions, schedule
from mtg_jev.provider import JevClient, MODEL, PRICE_PER_MILLION_INPUT, read_usage


def write_json(path, value):
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
    temporary.replace(path)


def remaining_plan(protocol, runs, http, summary):
    """Resume only an exact completed prefix with every HTTP outcome known."""
    if summary['pending'] or summary['failed_or_uncertain'] or summary['budget']['halt_reason']:
        raise ValueError('Unresolved usage/provider state; cannot continue')
    if any(h['status'] != 'response_with_usage' for h in http):
        raise ValueError('Unknown HTTP outcome; cannot continue')
    prefix = [{k: r[k] for k in ('repeat', 'scene', 'condition')} for r in runs]
    if prefix != protocol['schedule'][:len(runs)] or sum(r['http'] for r in runs) != len(http):
        raise ValueError('Completed prefix or HTTP total differs')
    spent = sum(h['usage']['input_tokens'] for h in http)*PRICE_PER_MILLION_INPUT/1e6
    return protocol['phase_http_cap']-len(http), protocol['phase_estimated_usd_cap']-spent


def run(args):
    # These limits are the pre-registered upper bounds, not a general benchmark CLI.
    if not 0 < args.budget_usd <= .05 or not 1 <= args.max_requests <= 384:
        raise ValueError('Limit exceeds Issue #23 scope')
    out = Path(args.output).resolve()
    continuing = (out/'live-started.json').exists()
    if continuing and not args.continue_planned:
        raise ValueError('Run already started; inspect outcome, never replay')
    out.mkdir(parents=True, exist_ok=True)
    images = {}
    manifest = {}
    for scene, path in [('scene-A', args.scene_a), ('scene-B', args.scene_b)]:
        data = Path(path).read_bytes()
        image = prepare(data)
        if image.original.size != (1280, 960) or image.original.getexif():
            raise ValueError('Requires fixed 1280x960 EXIF-free camera JPEGs')
        images[scene] = image
        manifest[scene] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                           'width': image.original.width, 'height': image.original.height}
    protocol = {'issue': 23, 'base_commit': BASE_COMMIT, 'model': MODEL,
                'max_cards': 16, 'frame_http_cap': 64, 'phase_http_cap': args.max_requests,
                'phase_estimated_usd_cap': args.budget_usd, 'prior_known_cloud_usd': args.prior_spend_usd,
                'mac_usage_included': False, 'repeats': args.repeats, 'inputs': manifest,
                'schedule': schedule(args.repeats), 'profiles': {c: instructions(c) for c in CONDITIONS},
                'provider_seed_temperature': 'unset for every condition; adapter exposes neither',
                'recenter': True, 'search_caption': 'short', 'verify_uncertain_proposals': True,
                'mask_settings': vars(MaskSettings()), 'ground_truth_access': False}
    if continuing:
        if json.loads((out/'protocol.json').read_text()) != protocol:
            raise ValueError('Frozen protocol differs; cannot continue')
    else:
        write_json(out/'protocol.json', protocol)
    if not args.approve_live:
        print(json.dumps({'plan_only': True, 'frame_runs': len(protocol['schedule']),
                          'phase_http_cap': args.max_requests, 'phase_usd_cap': args.budget_usd}))
        return

    def audit(event, values):
        if event == 'open' and values and isinstance(values[0], (str, bytes)):
            name = str(values[0])
            if '/scoring/' in name or '.truth' in name:
                raise RuntimeError('Truth access forbidden during inference')
    sys.addaudithook(audit)
    current, http, results = {}, [], []
    remaining_requests, remaining_budget = args.max_requests, args.budget_usd
    if continuing:
        http = json.loads((out/'http.json').read_text())
        results = json.loads((out/'runs.json').read_text())
        previous = json.loads((out/'summary.json').read_text())
        remaining_requests, remaining_budget = remaining_plan(protocol, results, http, previous)
        if len(results) >= len(protocol['schedule']) or remaining_requests <= 0 or remaining_budget <= 0:
            raise ValueError('No planned work or budget remains')
        for r in results:
            if not (out/f"r{r['repeat']}-{r['scene']}-{r['condition']}.json").exists():
                raise ValueError('Completed prediction missing')

    class Logged(JevClient):
        def _ask(self, image, excluded, factory, questions=1):
            attempt = len(http)+1
            entry = {'attempt': attempt, **current['run'], 'questions': questions, 'status': 'pending'}
            http.append(entry); write_json(out/'http.json', http)
            started = time.perf_counter()
            try:
                response = super()._ask(image, excluded, factory, questions)
            except Exception:
                entry.update(status='failed_or_uncertain_no_retry', elapsed_ms=round((time.perf_counter()-started)*1000, 3))
                write_json(out/'http.json', http)
                raise
            entry.update(status='response_with_usage', usage=read_usage(response),
                         elapsed_ms=round((time.perf_counter()-started)*1000, 3))
            write_json(out/f'http-{attempt:03d}.json', {k: response[k] for k in ('model', 'usage', 'answers')})
            write_json(out/'http.json', http)
            # Raw search Boxes include even a low-presence response. No gating,
            # no deduplication, no change to the algorithm's accepted candidates.
            if factory is current['search_factory']:
                answer = response.get('answers', {}).get('card', {})
                if answer.get('type') == 'box':
                    from mtg_jev.geometry import Box
                    try:
                        box = image.to_original(Box.read(answer['box']))
                    except (ValueError, TypeError, KeyError):
                        entry['raw_box_invalid'] = True
                        write_json(out/'http.json', http)
                    else:
                        current['raw_search'].append({'box': box.json(), 'confidence': answer.get('confidence'),
                            'presence': response['answers'].get('present', {}).get('noul'), 'http': attempt})
            return response

    state = CameraState(CameraConfig(mode='live', approve_live=True, max_requests=remaining_requests,
        budget_usd=remaining_budget, usage_ledger=str(out/'usage.json'), prior_spend_usd=args.prior_spend_usd,
        min_interval_seconds=0, max_cards=16, frame_max_requests=64), Logged)
    if continuing:
        expected_tokens = {k: sum(h['usage'][k] for h in http) for k in ('input_tokens', 'output_tokens')}
        if state.initial_calls != len(http) or any(state.ledger.data[k] != v for k, v in expected_tokens.items()):
            state.close()
            raise ValueError('Durable ledger differs from saved HTTP records')
        from mtg_jev.camera_usage import utc_now
        name = out/'continuations.json'
        history = json.loads(name.read_text()) if name.exists() else []
        history.append({'at': utc_now(), 'completed_prefix': len(results), 'http_before': len(http),
                        'remaining_requests': remaining_requests, 'remaining_budget_usd': remaining_budget,
                        'reason': 'known-usage malformed model box; next planned run, no replay'})
        write_json(name, history)
    else:
        write_json(out/'live-started.json', protocol)
    try:
        for run_spec in protocol['schedule'][len(results):]:
            search, crop = factories(run_spec['condition'])
            current.update(run=run_spec, search_factory=search, raw_search=[])
            client = AccountedFrameClient(state, threading.Event())
            started = time.perf_counter()
            result = detect_masked(images[run_spec['scene']], client, MaskSettings(), recenter=True,
                search_caption='short', search_document=search, crop_document=crop, verify_uncertain_proposals=True)
            result.update(run_spec)
            result['raw_search'] = current['raw_search']
            result['raw_crop'] = [{'box': a['box'], 'confidence': a['confidence'], 'presence': a['presence'],
                'single': a['single'], 'round': c['round'], 'recenter': a['recenter'], 'margin': a['margin']}
                for c in result['candidates'] for a in c['local_attempts']]
            result['stop_reason'] = client.stop_reason or result['stop_reason']
            result['api_calls'] = client.calls-client.initial_calls
            result['usage'] = client.usage  # Includes already sent requests on a stop.
            result['estimated_cost_usd'] = client.usage['input_tokens']*PRICE_PER_MILLION_INPUT/1e6
            result['pipeline_elapsed_ms'] = round((time.perf_counter()-started)*1000, 3)
            name = f"r{run_spec['repeat']}-{run_spec['scene']}-{run_spec['condition']}"
            write_json(out/f'{name}.json', result)
            row = {**run_spec, 'accepted': len(result['detections']), 'http': result['api_calls'],
                   'seconds': result['pipeline_elapsed_ms']/1000, 'estimated_cost_usd': result['estimated_cost_usd'],
                   'stop_reason': result['stop_reason'], 'status': result['status']}
            results.append(row); write_json(out/'runs.json', results)
            print(json.dumps(row), flush=True)
            if state.halt_reason or client.stop_reason:
                break  # Stop the phase; no implicit request or retry.
    finally:
        write_json(out/'summary.json', {'frame_runs': len(results), 'planned_frame_runs': len(protocol['schedule']),
            'http': len(http), 'usage': {k: sum(h.get('usage', {}).get(k, 0) for h in http)
                                       for k in ('input_tokens', 'output_tokens')},
            'budget': state.snapshot()['budget'], 'mac_usage_included': False,
            'phase_estimated_spend_usd': sum(h.get('usage', {}).get('input_tokens', 0) for h in http)*PRICE_PER_MILLION_INPUT/1e6,
            'pending': sum(h['status'] == 'pending' for h in http),
            'failed_or_uncertain': sum(h['status'] != 'response_with_usage' for h in http)})
        state.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene-a', required=True)
    parser.add_argument('--scene-b', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--prior-spend-usd', type=float, required=True)
    parser.add_argument('--repeats', type=int, default=4)
    parser.add_argument('--max-requests', type=int, default=384)
    parser.add_argument('--budget-usd', type=float, default=.05)
    parser.add_argument('--approve-live', action='store_true')
    parser.add_argument('--continue-planned', action='store_true', help='Continue only unexecuted runs after known-usage model answer failure')
    run(parser.parse_args())
