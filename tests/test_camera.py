import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from mtg_jev.camera_server import CameraConfig, CameraRequestError, CameraState, make_camera_server
from mtg_jev.camera_usage import UsageLedger, POC_SPENT_BEFORE_CAMERA
from mtg_jev.fixtures import fixture
from mtg_jev.images import png_bytes
from mtg_jev.provider import MODEL, ProviderError
from mtg_jev.camera_prompts import prompt_profile, single_document, search_document, crop_document
from mtg_jev.geometry import Box
from mtg_jev.images import prepare
from mtg_jev.refine import proposal_crop


class Stub:
    mode = 'live'
    def __init__(self, approval):
        self.approval, self.calls, self.input_tokens = approval, 0, 0
    def _ask(self, image, excluded, factory, questions):
        self.calls += 1; self.input_tokens += 1000
        return {'model': MODEL, 'usage': {'input_tokens': 1000, 'output_tokens': 36},
                'answers': {'remaining': {'type': 'noul', 'noul': .99},
                            'card': {'type': 'box', 'box': {'left': .1, 'top': .1, 'right': .5, 'bottom': .8}, 'confidence': .9}}}


class CameraStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name)/'usage.json')
        self.image = png_bytes(fixture('single')[0])
        self.env = patch.dict(os.environ, {'MTG_JEV_ENABLE_LIVE': '1'})
        self.env.start()
    def tearDown(self):
        self.env.stop(); self.directory.cleanup()
    def config(self, **kwargs):
        return CameraConfig(**{'mode': 'live', 'approve_live': True, 'max_requests': 5,
            'budget_usd': .1, 'usage_ledger': self.path, 'min_interval_seconds': 0, **kwargs})

    def test_mock_never_constructs_live_client_or_writes_ledger(self):
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('API forbidden')):
            state = CameraState(CameraConfig(min_interval_seconds=0), lambda approval: self.fail('Live client forbidden'))
            for method in ('single', 'ordinal4'):
                result = state.frame(self.image, method)
                self.assertEqual(result['api_calls'], 0)
                self.assertEqual(result['evidence'], 'mock_script_not_vision')
                self.assertGreater(len(result['detections']), 0)
            state.close()
        self.assertFalse(Path(self.path).exists())

    def test_single_one_http_accounts_usage_and_persists_across_restart(self):
        state = CameraState(self.config(), Stub)
        result = state.frame(self.image, method='single')
        self.assertEqual(result['api_calls'], 1)
        self.assertEqual(state.client.calls, 1)
        self.assertAlmostEqual(result['budget']['poc_spent_usd'], POC_SPENT_BEFORE_CAMERA+.000042)
        state.close()
        data = json.loads(Path(self.path).read_text())
        self.assertIsNone(data['pending'])
        self.assertNotIn('answers', str(data)); self.assertNotIn('base64', str(data)); self.assertNotIn('key', str(data))
        state = CameraState(self.config(), Stub)
        self.assertEqual(state.client.calls, 1)
        self.assertEqual(state.client.input_tokens, 1000)
        state.frame(self.image, method='single')
        self.assertEqual(state.ledger.data['requests_attempted'], 2)
        state.close()

    def test_unknown_usage_stops_and_restart_cannot_reset_it(self):
        class Failure(Stub):
            def _ask(self, *args):
                self.calls += 1
                raise ProviderError('Safe failure')
        state = CameraState(self.config(), Failure)
        with self.assertRaises(CameraRequestError): state.frame(self.image, method='single')
        self.assertEqual(state.client.calls, 1)
        with self.assertRaises(CameraRequestError): state.frame(self.image, method='single')
        self.assertEqual(state.client.calls, 1)
        state.close()
        with self.assertRaises(ValueError): CameraState(self.config(), Stub)
        self.assertEqual(json.loads(Path(self.path).read_text())['halt_reason'], 'usage_unknown_no_retry')

    def test_durable_pending_request_is_not_silently_replayed(self):
        ledger = UsageLedger(self.path); ledger.begin(2); ledger.close()
        with self.assertRaises(ValueError): CameraState(self.config(), Stub)
        self.assertIsNotNone(json.loads(Path(self.path).read_text())['pending'])

    def test_request_and_budget_limits_precede_provider_calls(self):
        state = CameraState(self.config(max_requests=1), Stub)
        state.frame(self.image, method='single')
        with self.assertRaises(CameraRequestError) as caught: state.frame(self.image, method='single')
        self.assertEqual(caught.exception.code, 'request_limit_reached')
        self.assertEqual(state.client.calls, 1); state.close()
        for kwargs in ({'budget_usd': .001}, {'prior_spend_usd': 4.999}):
            other = str(Path(self.directory.name)/('limit'+str(len(kwargs))+str(kwargs.get('budget_usd'))+'.json'))
            state = CameraState(self.config(usage_ledger=other, **kwargs), Stub)
            with self.assertRaises(CameraRequestError): state.frame(self.image, method='single')
            self.assertEqual(state.client.calls, 0)
            self.assertIsNone(state.ledger.data['pending']); state.close()

    def test_global_ledger_lock_blocks_second_process_and_totals_cannot_be_truncated(self):
        first = UsageLedger(self.path)
        with self.assertRaises(ValueError): UsageLedger(self.path)
        first.begin(2); first.finish({'input_tokens': 1000, 'output_tokens': 36}); first.close()
        value = json.loads(Path(self.path).read_text()); value['input_tokens'] = 0
        Path(self.path).write_text(json.dumps(value))
        with self.assertRaises(ValueError): UsageLedger(self.path)

    def test_backpressure_does_not_queue_a_second_provider_request(self):
        entered, finish = threading.Event(), threading.Event()
        class Slow(Stub):
            def _ask(self, *args):
                entered.set(); finish.wait(timeout=3)
                return super()._ask(*args)
        state = CameraState(self.config(), Slow); results = []
        thread = threading.Thread(target=lambda: results.append(state.frame(self.image, method='single')))
        thread.start(); self.assertTrue(entered.wait(timeout=2))
        self.assertTrue(state.snapshot()['inflight'])
        with self.assertRaises(CameraRequestError) as caught: state.frame(self.image, method='single')
        self.assertEqual(caught.exception.status, 409)
        finish.set(); thread.join(timeout=3)
        self.assertEqual(state.client.calls, 1); self.assertEqual(len(results), 1); state.close()

    def test_presence_confidence_and_invalid_answer_remain_distinct(self):
        class Uncertain(Stub):
            def _ask(self, *args):
                r = super()._ask(*args); r['answers']['card']['confidence'] = .1; return r
        state = CameraState(self.config(), Uncertain)
        r = state.frame(self.image, method='single'); self.assertEqual(r['detections'], [])
        self.assertEqual(r['unresolved_candidates'], 1)
        r = state.frame(self.image, method='single', min_confidence=.05)
        self.assertEqual(r['detections'][0]['confidence'], .1)
        state.close()

    def test_ordinal_candidates_share_one_http_and_absence_discards_arbitrary_box(self):
        outer = self
        class Multi(Stub):
            def _ask(self, image, excluded, factory, questions):
                outer.assertEqual(questions, 8)
                document = factory([], 'image-ref')
                outer.assertEqual(len(document['questions']), 8)
                r = super()._ask(image, excluded, factory, questions)
                answers = {}
                for rank in range(1, 5):
                    answers[f'slot_{rank:02d}_present'] = {'type': 'noul', 'noul': .99 if rank <= 2 else .01}
                    answers[f'slot_{rank:02d}_box'] = {'type': 'box', 'confidence': .9,
                        'box': {'left': .1+.35*(rank-1), 'top': .1, 'right': .3+.35*(rank-1), 'bottom': .8} if rank <= 2 else None}
                r['answers'] = answers; return r
        state = CameraState(self.config(), Multi)
        r = state.frame(self.image, method='ordinal4')
        self.assertEqual(len(r['detections']), 2)
        self.assertEqual(state.client.calls, 1)
        self.assertEqual(state.ledger.data['history'][0]['questions'], 8)
        state.close()

    def test_invalid_answer_retains_known_usage_and_never_returns_provider_text(self):
        class WrongModel(Stub):
            def _ask(self, *args):
                r = super()._ask(*args); r['model'] = 'wrong-model-private-body'; return r
        state = CameraState(self.config(), WrongModel)
        with self.assertRaises(CameraRequestError) as caught: state.frame(self.image, method='single')
        self.assertEqual(caught.exception.code, 'invalid_model_answer')
        self.assertEqual(state.ledger.data['input_tokens'], 1000)
        self.assertIsNone(state.ledger.data['pending'])
        self.assertNotIn('wrong-model-private-body', Path(self.path).read_text())
        state.close()

    def scan_stub(self):
        source = prepare(self.image); target = Box(.1, .1, .3, .4)
        tile, region = proposal_crop(source.original, target)
        l,t,r,b = target.pixels(*source.original.size)
        local = Box((l-region[0])/tile.original.width, (t-region[1])/tile.original.height,
                    (r-region[0])/tile.original.width, (b-region[1])/tile.original.height)
        class Scan(Stub):
            def _ask(self, image, excluded, factory, questions):
                self.calls += 1; self.input_tokens += 1000
                if self.calls == 1:
                    answers = {'present': {'type': 'noul', 'noul': .99},
                               'card': {'type': 'box', 'box': target.json(), 'confidence': .9}}
                elif self.calls == 2:
                    answers = {'present': {'type': 'noul', 'noul': .99}, 'single': {'type': 'noul', 'noul': .95},
                               'card': {'type': 'box', 'box': tile.to_prepared(local).json(), 'confidence': .9}}
                else:
                    answers = {'present': {'type': 'noul', 'noul': .01}}
                return {'model': MODEL, 'usage': {'input_tokens': 1000, 'output_tokens': 36}, 'answers': answers}
        return Scan

    def test_default_is_multi_and_counts_every_stage_http_with_no_eight_card_ceiling(self):
        state = CameraState(self.config(), self.scan_stub())
        result = state.frame(self.image)
        self.assertEqual(result['method'], 'multi')
        self.assertEqual(result['api_calls'], 3)
        self.assertEqual(result['usage']['input_tokens'], 3000)
        self.assertEqual(len(result['detections']), 1)
        self.assertEqual(state.ledger.data['requests_attempted'], 3)
        self.assertGreaterEqual(result['max_cards'], 8)
        self.assertGreaterEqual(result['frame_max_api_calls'], 57)
        self.assertEqual(result['prompt_sha256'], prompt_profile('multi')['sha256'])
        state.close()

    def test_frame_cap_keeps_partial_predictions_without_unknown_usage(self):
        state = CameraState(self.config(frame_max_requests=2), self.scan_stub())
        result = state.frame(self.image)
        self.assertEqual(result['api_calls'], 2)
        self.assertEqual(len(result['detections']), 1)
        self.assertEqual(result['stop_reason'], 'frame_request_limit')
        self.assertTrue(result['auto_pause'])
        self.assertIsNone(state.ledger.data['pending'])
        state.close()

    def test_missing_confidence_stays_missing_when_cross_crop_geometry_supports_it(self):
        base = self.scan_stub()
        class NoConfidence(base):
            def _ask(self, *args):
                r = super()._ask(*args)
                if self.calls == 2: r['answers']['card']['confidence'] = None
                return r
        state = CameraState(self.config(), NoConfidence)
        result = state.frame(self.image)
        self.assertIsNone(result['detections'][0]['confidence'])
        self.assertEqual(result['detections'][0]['support'], 'cross_crop_agreement')
        state.close()

    def test_default_multi_can_return_eight_scripted_targets_without_rank_or_card_hint(self):
        source = prepare(self.image)
        scripted = [Box(.04+.24*col, .05+.5*row, .21+.24*col, .35+.5*row)
                    for row in range(2) for col in range(4)]
        class Eight(Stub):
            index = 0
            def _ask(self, image, excluded, factory, questions):
                self.calls += 1; self.input_tokens += 1000
                a = {'present': {'type': 'noul', 'noul': .01}}
                if self.index < len(scripted):
                    target = scripted[self.index]
                    a['present']['noul'] = .99
                    if questions == 2:
                        b = image.to_prepared(target)
                    else:
                        tile, region = proposal_crop(source.original, target)
                        l,t,r,bottom = target.pixels(*source.original.size)
                        local = Box((l-region[0])/tile.original.width, (t-region[1])/tile.original.height,
                                    (r-region[0])/tile.original.width, (bottom-region[1])/tile.original.height)
                        b = tile.to_prepared(local); a['single'] = {'type': 'noul', 'noul': .99}
                        self.index += 1
                    a['card'] = {'type': 'box', 'box': b.json(), 'confidence': .9}
                return {'model': MODEL, 'usage': {'input_tokens': 1000, 'output_tokens': 36}, 'answers': a}
        state = CameraState(self.config(max_requests=80), Eight)
        result = state.frame(self.image)
        self.assertEqual(len(result['detections']), 8)
        self.assertEqual(result['api_calls'], 17)
        self.assertEqual(state.client.calls, 17)
        state.close()

    def test_stop_during_scan_finishes_one_usage_but_sends_no_further_http(self):
        entered, finish = threading.Event(), threading.Event()
        base = self.scan_stub()
        class SlowScan(base):
            def _ask(self, *args):
                entered.set(); finish.wait(timeout=3); return super()._ask(*args)
        state = CameraState(self.config(), SlowScan); results = []
        thread = threading.Thread(target=lambda: results.append(state.frame(self.image, run_id='cancel-run-0001')))
        thread.start(); self.assertTrue(entered.wait(timeout=2))
        state.cancel('cancel-run-0001'); finish.set(); thread.join(timeout=3)
        self.assertEqual(state.client.calls, 1)
        self.assertEqual(results[0]['stop_reason'], 'scan_cancelled')
        self.assertEqual(results[0]['usage']['input_tokens'], 1000)
        self.assertIsNone(state.ledger.data['pending'])
        with self.assertRaises(CameraRequestError): state.frame(self.image, run_id='cancel-run-0001')
        self.assertEqual(state.client.calls, 1)
        state.close()

    def test_camera_prompts_are_inspectable_and_have_no_empty_unreported_context(self):
        for method in ('single', 'multi', 'ordinal'):
            profile = prompt_profile(method)
            text = json.dumps(profile)
            self.assertNotIn('excluded_boxes', text)
            self.assertNotIn('not-yet-reported', text)
            self.assertEqual(profile['model'], MODEL)
        for factory in (single_document, search_document, crop_document):
            self.assertEqual(set(factory([], 'ref')['state']), {'photo'})


class CameraHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_camera_server(0, CameraConfig(min_interval_seconds=0))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(timeout=3)
    def request(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        c.request(method, path, body, headers or {}); r = c.getresponse()
        value = r.status, r.read(), dict(r.getheaders()); c.close(); return value

    def test_assets_and_config_do_not_start_camera_or_inference(self):
        for path in ('/', '/camera.js', '/camera-core.js'):
            status, value, headers = self.request('GET', path)
            self.assertEqual(status, 200)
            self.assertIn('no-store', headers['Cache-Control'])
            self.assertIn('camera=(self)', headers['Permissions-Policy'])
        status, raw, _ = self.request('GET', '/api/camera/config')
        config = json.loads(raw)
        self.assertEqual(config['mode'], 'mock')
        self.assertEqual(config['default_method'], 'multi')
        self.assertEqual(config['max_cards'], 16)
        self.assertNotIn('TYPESAFE', raw.decode())
        self.assertNotIn('Authorization', raw.decode())
        status, raw, _ = self.request('GET', '/api/camera/prompts?method=multi')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['sha256'], prompt_profile('multi')['sha256'])
        self.assertNotIn('base64', raw.decode())

    def test_frame_origin_csrf_size_and_parameter_guards(self):
        image = png_bytes(fixture('single')[0])
        headers = {'Content-Type': 'image/png', 'X-Camera-Token': self.server.camera.token}
        self.assertEqual(self.request('POST', '/api/camera/frame', image)[0], 403)
        for bad in ({'Origin': 'https://example.invalid'}, {'Host': 'example.invalid'}):
            self.assertEqual(self.request('POST', '/api/camera/frame', image, {**headers, **bad})[0], 403)
        for suffix in ('?method=unknown', '?confidence=nan', '?endpoint=elsewhere'):
            self.assertEqual(self.request('POST', '/api/camera/frame'+suffix, image, headers)[0], 400)
        self.assertEqual(self.request('POST', '/api/camera/frame', b'', {**headers, 'Content-Length': '5000000'})[0], 413)
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('No external API')):
            status, raw, _ = self.request('POST', '/api/camera/frame?method=ordinal4', image, headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['api_calls'], 0)
        for path in ('/.env', '/api/evaluate', '/camera-usage.json'):
            self.assertEqual(self.request('GET', path)[0], 404)


class CameraCLITests(unittest.TestCase):
    def test_default_camera_command_is_mock_and_never_prompts_for_key(self):
        from mtg_jev.cli import main
        with patch('mtg_jev.camera_server.serve_camera') as serve, patch('getpass.getpass') as prompt:
            self.assertEqual(main(['camera']), 0)
            self.assertEqual(serve.call_args.args[1].mode, 'mock')
            self.assertGreaterEqual(serve.call_args.args[1].max_requests, 57)
            prompt.assert_not_called()

    def test_key_prompt_needs_approval_live_mode_and_a_real_local_terminal(self):
        from mtg_jev.cli import main
        cases = [['camera', '--prompt-key'],
                 ['camera', '--mode', 'live', '--budget-usd', '.05', '--prompt-key'],
                 ['camera', '--mode', 'live', '--approve-live', '--budget-usd', '.05', '--prompt-key']]
        with patch.dict(os.environ, {'MTG_JEV_ENABLE_LIVE': '1'}), patch('sys.stdin.isatty', return_value=False), \
             patch('getpass.getpass') as prompt, patch('mtg_jev.camera_server.serve_camera') as serve, redirect_stderr(io.StringIO()):
            for args in cases:
                self.assertEqual(main(args), 2)
            prompt.assert_not_called(); serve.assert_not_called()


if __name__ == '__main__': unittest.main()
