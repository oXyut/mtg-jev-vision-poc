"""API-free static-photo comparison. Inputs and normalized records are private.

This is an evaluation interchange format, not a Decision API wire contract.
No credentials, network client, or live-execution switch exists here.
"""
import hashlib
import io
import json
import math
from pathlib import Path
import re
import statistics

from PIL import Image

from .geometry import COORDINATES, Box, evaluate, iou
from .identification import evaluate_identifications
from .prompt_factorial import CONDITIONS, instructions, schedule

BASE_COMMIT = '9746cb5499543f1cc1e5a6767d9dbfeeec1216c5'
SCENES = ('scene-A', 'scene-B')
PROVIDERS = ('jev', 'decision')
EVIDENCE = ('live', 'synthetic', 'mock_replay', 'stubbed_http')
LAYERS = ('detections', 'raw_search', 'raw_crop')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def make_plan(repeats=4):
    runs = []
    for index, run in enumerate(schedule(repeats)):
        # Alternate first provider; retain the existing balanced condition order.
        providers = PROVIDERS if index % 2 == 0 else PROVIDERS[::-1]
        runs.extend({**run, 'provider': provider} for provider in providers)
    return {
        'schema': 1, 'base_commit': BASE_COMMIT, 'coordinate_system': COORDINATES,
        'bbox_rule': 'visible_printed_card_aabb_excluding_sleeve_and_hidden_parts',
        'profiles': {condition: instructions(condition) for condition in CONDITIONS},
        'jev_settings': {'model': 'jev-1.14.0', 'max_cards': 16, 'frame_http_cap': 64,
                         'recenter': True, 'search_caption': 'short',
                         'verify_uncertain_proposals': True,
                         'mask_settings': {'max_rounds': 16, 'presence_yes': .8, 'presence_no': .2,
                             'single_card_yes': .9, 'box_confidence': .6, 'cross_view_iou': .75,
                             'minimum_unmasked_fraction': .5, 'duplicate_iou': .85, 'mask_margin_pixels': 4},
                         'seed_temperature': 'unset; not exposed by Jev adapter'},
        'inputs': None, 'schedule': runs,
        'decision_provider': 'openai', 'live_authorized': False,
        'native_decision_bbox': False,
        'schedule_executable': False,
        'blockers': ['native_bbox_output_unsupported', 'live_budget_unapproved',
                     'private_input_hashes_unverified'],
        'status': 'not_run',
    }


def freeze_inputs(plan, images, truths):
    """Read only explicitly supplied files. Never search for private inputs.

    Images are hashed as supplied, before any decoding; never re-encoded.
    Truth is frozen as canonical JSON. The manifest itself must stay private.
    """
    result = {**plan, 'inputs': {}}
    for scene in SCENES:
        data = Path(images[scene]).read_bytes()
        if not 0 < len(data) <= 16 * 1024 * 1024:
            raise ValueError('Invalid input byte size')
        with Image.open(io.BytesIO(data)) as image:
            if image.format != 'JPEG' or image.size != (1280, 960) or image.getexif():
                raise ValueError('Requires original EXIF-free 1280x960 JPEG input')
            image.verify()
        truth = truths[scene]
        validate_truth(truth)
        result['inputs'][scene] = {
            'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
            'width': 1280, 'height': 960, 'truth_sha256': digest(truth),
        }
    return result


def validate_truth(truth):
    if truth.get('coordinate_system') != COORDINATES or truth.get('image') != {'width': 1280, 'height': 960}:
        raise ValueError('Truth coordinate frame differs')
    objects = truth.get('objects')
    if not isinstance(objects, list) or len(objects) > 30:
        raise ValueError('Truth requires at most 30 objects')
    for obj in objects:
        Box.read(obj['box'])
        if obj.get('card_id') is not None and (not isinstance(obj['card_id'], str) or not obj['card_id']):
            raise ValueError('Invalid truth identity')


def number(value, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('Measurement must be finite and nonnegative')
    if integer and type(value) is not int:
        raise ValueError('HTTP count must be an integer')
    return value


def localization(detections, truth, threshold):
    boxes = [Box.read(d['box']) for d in detections]
    targets = [Box.read(obj['box']) for obj in truth['objects']]
    score = evaluate(boxes, targets, threshold)
    # Whitelist aggregate scalars: never emit coordinates or matching indices.
    result = {key: score[key] for key in ('tp', 'fp', 'fn', 'precision', 'recall', 'f1', 'mean_matched_iou')}
    eligible = sum(any(iou(box, target) >= threshold for target in targets) for box in boxes)
    result['duplicate_truth_predictions'] = eligible - score['tp']
    result['geometric_duplicate_pairs'] = sum(iou(a, b) >= .85 for index, a in enumerate(boxes) for b in boxes[index+1:])
    return result


def identity_score(record, truth, threshold):
    if record.get('identity_status') != 'complete':
        return {'status': 'not_run'}
    if any('card_id' not in obj for obj in truth['objects']):
        raise ValueError('Identification scoring requires explicit truth identities (null for unknown)')
    for detection in record['detections']:
        identity = detection.get('identification', {})
        if identity.get('status') not in ('identified', 'unknown'):
            raise ValueError('Every adopted detection requires an identity outcome')
        selected = identity.get('card_id')
        if identity['status'] == 'identified' and (not isinstance(selected, str) or not selected):
            raise ValueError('Identified requires a nonempty card_id')
        if identity['status'] == 'unknown' and selected is not None:
            raise ValueError('Unknown must not select a card_id')
    prediction = {'coordinate_system': COORDINATES, 'image': truth['image'],
                  'detections': record['detections']}
    score = evaluate_identifications(prediction, truth, threshold)
    return {'status': 'complete', **{key: score[key] for key in (
        'known_truth_count', 'matched_known_count', 'accepted_count', 'wrong_accepted_count',
        'correct_accepted_count', 'top1_on_matched_known', 'unknown_rate',
        'misidentification_rate_among_accepted', 'end_to_end_name_recall', 'end_to_end_name_precision')}}


def score_records(plan, records, truths):
    """Score an exact completed prefix; partial/error frames remain in all rows.

    Caller-provided evidence labels are checked for consistency, not authenticated.
    The output has only fixed labels, counts and numeric aggregates.
    """
    if plan.get('schema') != 1 or plan.get('base_commit') != BASE_COMMIT:
        raise ValueError('Unsupported comparison protocol')
    expected_plan = make_plan(len(plan['schedule']) // 16)
    for key in ('schedule', 'profiles', 'coordinate_system', 'bbox_rule', 'jev_settings'):
        if plan.get(key) != expected_plan[key]:
            raise ValueError('Frozen comparison settings differ')
    if not isinstance(plan.get('inputs'), dict) or set(plan['inputs']) != set(SCENES):
        raise ValueError('Frozen private input manifest required')
    for scene in SCENES:
        validate_truth(truths[scene])
        source = plan['inputs'][scene]
        if any(not isinstance(source.get(key), str) or not re.fullmatch('[0-9a-f]{64}', source[key])
               for key in ('sha256', 'truth_sha256')):
            raise ValueError('Input manifest requires SHA256 digests')
        if (source.get('width'), source.get('height')) != (1280, 960) or type(source.get('bytes')) is not int or not 0 < source['bytes'] <= 16*1024*1024:
            raise ValueError('Input manifest dimensions or bytes differ')
        if digest(truths[scene]) != plan['inputs'][scene]['truth_sha256']:
            raise ValueError('Frozen truth differs')
    if not isinstance(records, list) or len(records) > len(plan['schedule']):
        raise ValueError('Invalid run count')
    evidence = {r.get('evidence') for r in records}
    if not evidence <= set(EVIDENCE) or len(evidence) > 1:
        raise ValueError('Do not mix live, synthetic, mock replay or stubbed HTTP evidence')
    manifest_hash = digest(plan)
    rows = []
    for index, record in enumerate(records):
        spec = plan['schedule'][index]
        if any(type(record.get(key)) is not type(value) or record.get(key) != value for key, value in spec.items()):
            raise ValueError('Runs must be an exact planned prefix; no reruns or omissions')
        if record.get('manifest_sha256') != manifest_hash or record.get('image_sha256') != plan['inputs'][spec['scene']]['sha256']:
            raise ValueError('Run/input binding differs')
        if record['evidence'] == 'live' and spec['provider'] == 'decision':
            raise ValueError('Native Decisions has no bbox output; do not score it as a detector')
        status = record.get('status')
        if status not in ('complete', 'error'):
            raise ValueError('Executed runs require complete or error status')
        if record.get('identity_status') not in ('complete', 'not_run'):
            raise ValueError('Declare identity complete or not_run')
        outcome = record.get('request_outcome')
        if outcome not in ('known', 'pending', 'uncertain') or (outcome != 'known' and status != 'error'):
            raise ValueError('Invalid request outcome')
        # No run after an uncertain outcome: inspect it before any continuation.
        if outcome != 'known' and index != len(records)-1:
            raise ValueError('Run after unresolved request outcome')
        measurements = {key: (None if record.get(key) is None else number(record[key], key == 'http'))
                        for key in ('seconds', 'http', 'cost_usd')}
        if record['evidence'] == 'live' and measurements['http'] == 0:
            raise ValueError('Live run requires an attempted HTTP request')
        if outcome != 'known' and measurements['cost_usd'] is not None:
            raise ValueError('Unresolved request cost must remain unknown')
        layers = {}
        invalid_boxes = {layer: number(record.get('invalid_boxes', {}).get(layer, 0), integer=True) for layer in LAYERS}
        if any(invalid_boxes.values()) and status != 'error':
            raise ValueError('Malformed model boxes require an error frame')
        for layer in LAYERS:
            detections = record.get(layer)
            if detections is None and layer != 'detections':
                layers[layer] = {'status': 'not_run'}
                continue
            if not isinstance(detections, list) or len(detections) > 200:
                raise ValueError('Layer requires at most 200 detections')
            layers[layer] = {str(t): localization(detections, truths[spec['scene']], t) for t in (.5, .75)}
        if len(record['detections']) > 30:
            raise ValueError('Adopted layer exceeds identification capacity')
        identities = {str(t): identity_score(record, truths[spec['scene']], t) for t in (.5, .75)}
        rows.append({**spec, 'status': status, 'request_outcome': outcome,
                     **measurements, 'invalid_boxes': invalid_boxes, 'layers': layers, 'identity': identities})
    # Report complete 16-run blocks separately, keeping every partial row above.
    complete = [r for r in range(1, 5) if sum(row['repeat'] == r for row in rows) == 16]
    lookup = {(r['repeat'], r['scene'], r['condition'], r['provider']): r for r in rows}
    paired = []
    for repeat in complete:
        for scene in SCENES:
            for condition in CONDITIONS:
                a, b = (lookup[repeat, scene, condition, provider] for provider in PROVIDERS)
                paired.append({'repeat': repeat, 'scene': scene, 'condition': condition,
                               'decision_minus_jev_tp': {str(t): b['layers']['detections'][str(t)]['tp'] -
                                                       a['layers']['detections'][str(t)]['tp'] for t in (.5, .75)}})
    def total(key):
        return sum(row[key] for row in rows) if all(row[key] is not None for row in rows) else None
    aggregate = []
    for provider in PROVIDERS:
        for scene in SCENES:
            for condition in CONDITIONS:
                subset = [r for r in rows if (r['provider'], r['scene'], r['condition']) == (provider, scene, condition)]
                if not subset:
                    continue
                measurements = {}
                for key in ('seconds', 'http', 'cost_usd'):
                    values = [r[key] for r in subset if r[key] is not None]
                    measurements[key] = {'known_runs': len(values), 'unknown_runs': len(subset)-len(values),
                                         'mean': statistics.mean(values) if values else None,
                                         'min': min(values) if values else None, 'max': max(values) if values else None}
                aggregate.append({'provider': provider, 'scene': scene, 'condition': condition,
                                  'runs': len(subset), 'balanced_runs': sum(r['repeat'] in complete for r in subset),
                                  'error_frames': sum(r['status'] == 'error' for r in subset),
                                  'per_repeat_tp': {str(t): [r['layers']['detections'][str(t)]['tp'] for r in subset] for t in (.5, .75)},
                                  **measurements})
    return {'schema': 1, 'evidence': next(iter(evidence), 'not_run'),
            'status': 'not_run' if not rows else 'complete' if len(rows) == len(plan['schedule']) else 'incomplete',
            'planned_runs': len(plan['schedule']), 'executed_runs': len(rows),
            'complete_repeats': complete, 'error_frames': sum(row['status'] == 'error' for row in rows),
            'unresolved_requests': sum(row['request_outcome'] != 'known' for row in rows),
            'http_total': total('http') if rows else 0,
            'cost_usd_total': total('cost_usd') if rows else None,
            'unknown_cost_runs': sum(row['cost_usd'] is None for row in rows),
            'rows': rows, 'aggregate': aggregate, 'paired': paired,
            'recognition_claim': 'unvalidated_declared_live_records' if evidence == {'live'} else 'no_live_recognition_evidence'}
