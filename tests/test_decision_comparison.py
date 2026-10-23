"""Synthetic contract/evaluation checks. No measured card recognition."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from mtg_jev.comparison import SCENES, digest, freeze_inputs, make_plan, score_records
from mtg_jev.decisions import MODEL, UnsupportedTask, read_verification, require_task, verification_document
from mtg_jev.geometry import COORDINATES


def jpeg(exif=False):
    image = Image.new('RGB', (1280, 960), '#334455')
    draw = ImageDraw.Draw(image)
    draw.rectangle((128, 96, 384, 384), fill='#eeddcc')
    output = io.BytesIO()
    metadata = Image.Exif()
    if exif:
        metadata[274] = 1
    image.save(output, 'JPEG', exif=metadata)
    return output.getvalue()


def box(left=.1, top=.1, right=.3, bottom=.4):
    return dict(left=left, top=top, right=right, bottom=bottom)


def truth():
    return {'coordinate_system': COORDINATES, 'image': {'width': 1280, 'height': 960},
            'objects': [{'box': box(), 'card_id': 'synthetic-a'},
                        {'box': box(.6, .6, .8, .9), 'card_id': 'synthetic-b'}]}


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.plan = make_plan(1)
        self.truths = {scene: truth() for scene in SCENES}
        self.plan['inputs'] = {scene: {'sha256': hashlib.sha256(jpeg()).hexdigest(),
                                    'width': 1280, 'height': 960, 'bytes': len(jpeg()),
                                    'truth_sha256': digest(self.truths[scene])} for scene in SCENES}

    def records(self, count=16):
        return [{**spec, 'manifest_sha256': digest(self.plan),
                 'image_sha256': self.plan['inputs'][spec['scene']]['sha256'],
                 'evidence': 'synthetic', 'status': 'complete', 'request_outcome': 'known',
                 'seconds': 0.25, 'http': 0, 'cost_usd': 0, 'identity_status': 'not_run',
                 'detections': [{'box': box()}]}
                for spec in self.plan['schedule'][:count]]

    def test_balanced_plan_is_not_executable_or_authorized(self):
        plan = make_plan()
        self.assertEqual(len(plan['schedule']), 64)
        self.assertEqual(plan['decision_provider'], 'openai')
        self.assertFalse(plan['schedule_executable'])
        self.assertFalse(plan['live_authorized'])
        for repeat in range(1, 5):
            self.assertEqual(sum(r['repeat'] == repeat for r in plan['schedule']), 16)
        for bad in (0, 5, True):
            with self.assertRaises(ValueError): make_plan(bad)

    def test_freeze_original_jpeg_hash_and_reject_exif(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'synthetic.jpg'
            source.write_bytes(jpeg())
            manifest = freeze_inputs(make_plan(1), {s: source for s in SCENES}, self.truths)
            self.assertEqual(manifest['inputs']['scene-A']['sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
            source.write_bytes(jpeg(exif=True))
            with self.assertRaises(ValueError):
                freeze_inputs(make_plan(1), {s: source for s in SCENES}, self.truths)

    def test_unique_matches_duplicates_false_positives_and_thresholds(self):
        records = self.records(1)
        records[0]['detections'] = [{'box': box()}, {'box': box()}, {'box': box(.6, .6, .8, .8)}]
        scores = score_records(self.plan, records, self.truths)['rows'][0]['layers']['detections']
        self.assertEqual((scores['0.5']['tp'], scores['0.5']['fp'], scores['0.5']['fn']), (2, 1, 0))
        self.assertEqual((scores['0.75']['tp'], scores['0.75']['fp'], scores['0.75']['fn']), (1, 2, 1))
        self.assertEqual(scores['0.5']['duplicate_truth_predictions'], 1)
        self.assertEqual(scores['0.5']['geometric_duplicate_pairs'], 1)

    def test_error_partial_frame_is_kept_and_unknown_cost_is_not_zero(self):
        records = self.records(3)
        records[-1].update(status='error', request_outcome='uncertain', http=1, cost_usd=None,
                           invalid_boxes={'raw_crop': 1})
        result = score_records(self.plan, records, self.truths)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['error_frames'], 1)
        self.assertEqual(result['unresolved_requests'], 1)
        self.assertEqual(result['http_total'], 1)
        self.assertIsNone(result['cost_usd_total'])
        self.assertEqual(result['unknown_cost_runs'], 1)
        self.assertEqual(result['rows'][-1]['layers']['detections']['0.5']['tp'], 1)
        self.assertEqual(result['paired'], [])

    def test_reject_rerun_omission_truth_or_manifest_drift_and_mixed_evidence(self):
        variants = []
        records = self.records(3)
        variants.append(records[1:])
        for key, value in [('condition', 'generic_explicit'), ('manifest_sha256', 'bad'),
                           ('image_sha256', 'bad'), ('repeat', True), ('seconds', float('nan'))]:
            changed = copy.deepcopy(records)
            changed[0][key] = value
            variants.append(changed)
        changed = copy.deepcopy(records)
        changed[1]['evidence'] = 'live'
        variants.append(changed)
        changed = copy.deepcopy(records)
        changed[0].update(status='error', request_outcome='pending', cost_usd=None)
        variants.append(changed)
        for changed in variants:
            with self.subTest(changed=variants.index(changed)):
                with self.assertRaises(ValueError): score_records(self.plan, changed, self.truths)
        truths = copy.deepcopy(self.truths)
        truths['scene-A']['objects'].pop()
        with self.assertRaises(ValueError): score_records(self.plan, records, truths)

    def test_identity_counts_localization_false_positive_and_unknown(self):
        records = self.records(1)
        records[0].update(identity_status='complete', detections=[
            {'box': box(), 'identification': {'status': 'identified', 'card_id': 'synthetic-a'}},
            {'box': box(), 'identification': {'status': 'identified', 'card_id': 'synthetic-a'}},
            {'box': box(.6, .6, .8, .9), 'identification': {'status': 'unknown', 'card_id': None}}])
        result = score_records(self.plan, records, self.truths)['rows'][0]['identity']['0.5']
        self.assertEqual(result['correct_accepted_count'], 1)
        self.assertEqual(result['wrong_accepted_count'], 1)
        self.assertEqual(result['end_to_end_name_recall'], .5)
        self.assertEqual(result['end_to_end_name_precision'], .5)
        self.assertAlmostEqual(result['unknown_rate'], 1/3)
        records[0]['identity_status'] = 'not_run'
        self.assertEqual(score_records(self.plan, records, self.truths)['rows'][0]['identity']['0.5'], {'status': 'not_run'})

    def test_public_output_whitelist_and_balanced_pairing(self):
        records = self.records()
        records[0]['private_raw_response'] = 'PRIVATE_SENTINEL'
        records[0]['detections'][0]['name'] = 'PRIVATE_SENTINEL'
        result = score_records(self.plan, records, self.truths)
        self.assertEqual(result['complete_repeats'], [1])
        self.assertEqual(len(result['paired']), 8)
        serialized = json.dumps(result)
        for private in ('PRIVATE_SENTINEL', 'synthetic-a', 'sha256', 'left', 'matches'):
            self.assertNotIn(private, serialized)
        self.assertEqual(result['recognition_claim'], 'no_live_recognition_evidence')
        self.assertTrue(all(row['balanced_runs'] == 1 for row in result['aggregate']))

    def test_no_decisions_live_bbox_or_missing_equals_zero(self):
        records = self.records(2)
        for record in records:
            record.update(evidence='live', http=1)
        with self.assertRaisesRegex(ValueError, 'no bbox'):
            score_records(self.plan, records, self.truths)
        result = score_records(self.plan, [], self.truths)
        self.assertEqual(result['status'], 'not_run')
        self.assertIsNone(result['cost_usd_total'])

    def test_cli_offline_plan_refuses_overwrite_and_safe_errors(self):
        script = Path(__file__).resolve().parents[1]/'experiments/compare_static_providers.py'
        with tempfile.TemporaryDirectory() as folder:
            output = str(Path(folder)/'plan.json')
            command = [sys.executable, str(script), 'plan', '--output', output]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn('"api_calls_this_command": 0', first.stdout)
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(second.returncode, 2)
            self.assertNotIn(output, second.stderr)
            image_path, truth_path = Path(folder)/'input.jpg', Path(folder)/'truth.json'
            image_path.write_bytes(jpeg())
            truth_path.write_text(json.dumps(truth()))
            manifest_path = Path(folder)/'frozen.json'
            frozen = subprocess.run([sys.executable, str(script), 'plan', '--repeats', '1',
                '--scene-a', str(image_path), '--scene-b', str(image_path),
                '--truth-a', str(truth_path), '--truth-b', str(truth_path),
                '--output', str(manifest_path)], capture_output=True, text=True)
            self.assertEqual(frozen.returncode, 0, frozen.stderr)
            self.plan = json.loads(manifest_path.read_text())
            records_path, scores_path = Path(folder)/'records.json', Path(folder)/'scores.json'
            records_path.write_text(json.dumps(self.records()))
            scored = subprocess.run([sys.executable, str(script), 'score', '--manifest', str(manifest_path),
                '--records', str(records_path), '--truth-a', str(truth_path), '--truth-b', str(truth_path),
                '--output', str(scores_path)], capture_output=True, text=True)
            self.assertEqual(scored.returncode, 0, scored.stderr)
            scores = json.loads(scores_path.read_text())
            self.assertEqual(scores['executed_runs'], 16)
            self.assertEqual(scores['recognition_claim'], 'no_live_recognition_evidence')


class DecisionsContractTests(unittest.TestCase):
    def response(self, answers):
        return {'model': MODEL, 'answers': answers, 'usage': {'input_tokens': 100,
                'output_tokens': 0, 'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0}}}

    def test_native_image_bytes_and_prompts_no_http_or_credentials(self):
        data = jpeg()
        with (patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network forbidden')),
              patch('os.environ.get', side_effect=AssertionError('credential lookup forbidden'))):
            document = verification_document(data, 'generic_explicit')
            parsed = read_verification(self.response([{'name': 'present', 'type': 'predicate', 'probability': .9}]))
        part = document['input'][0]['content'][0]
        self.assertEqual(part['type'], 'input_image')
        self.assertEqual(base64.b64decode(part['image_url'].split(',')[1]), data)
        self.assertEqual(part['detail'], 'original')
        self.assertIn('trading card', document['questions'][0]['instructions'])
        self.assertIn('partly occluded', document['questions'][0]['instructions'])
        self.assertEqual(document['prompt_cache_options'], {'mode': 'explicit'})
        self.assertEqual(parsed['answers']['present']['probability'], .9)

    def test_crop_refusal_and_invalid_or_jev_shapes(self):
        doc = verification_document(jpeg(), 'mtg_plain', 'crop_verification')
        self.assertEqual([q['name'] for q in doc['questions']], ['present', 'single'])
        response = self.response([{'name': 'present', 'type': 'predicate', 'probability': .9},
                                  {'name': 'single', 'type': 'refusal'}])
        parsed = read_verification(response, 'crop_verification')
        self.assertEqual(parsed['answers']['single'], {'status': 'refused', 'probability': None})
        for changed in ({'answers': {}}, {**response, 'model': 'other'},
                        self.response([{'name': 'present', 'type': 'box', 'box': box()}]),
                        self.response([{'name': 'present', 'type': 'predicate', 'probability': True}])):
            with self.assertRaisesRegex(ValueError, 'contents omitted'): read_verification(changed)
        response['usage']['input_tokens_details']['cached_tokens'] = 101
        with self.assertRaises(ValueError): read_verification(response, 'crop_verification')

    def test_unsupported_tasks_rejected_before_reading_images(self):
        for task in ('bbox', 'open_identity', 'choice_grid'):
            with self.assertRaises(UnsupportedTask): require_task(task)
            with self.assertRaises(UnsupportedTask): verification_document(b'', 'mtg_plain', task)


if __name__ == '__main__':
    unittest.main()
