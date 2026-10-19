import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from mtg_jev.detector import Settings, detect
from mtg_jev.fixtures import SCENARIOS, fixture, generate
from mtg_jev.geometry import COORDINATES, Box, evaluate, evaluate_documents, iou
from mtg_jev.images import crop, overlay, png_bytes, prepare
from mtg_jev.provider import (JevClient, LiveApproval, MockClient, NoRedirect,
                              ProviderError, mock_script, multipart)


class GeometryTests(unittest.TestCase):
    def test_known_iou_and_disjoint(self):
        self.assertAlmostEqual(iou(Box(0, 0, .6, .6), Box(.3, .3, .9, .9)), 1 / 7)
        self.assertEqual(iou(Box(0, 0, .2, .2), Box(.3, .3, .5, .5)), 0)

    def test_reject_invalid_boxes(self):
        for values in [(0, 0, 0, 1), (0, .5, 1, .2), (-1, 0, 1, 1),
                       (0, 0, float('nan'), 1), (False, 0, 1, 1), (0, 0, '1', 1)]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                Box(*values)

    def test_duplicates_are_false_positives(self):
        b = Box(.1, .1, .4, .6)
        result = evaluate([b, b], [b])
        self.assertEqual((result['tp'], result['fp'], result['fn']), (1, 1, 0))
        self.assertEqual(result['precision'], .5)

    def test_matching_finds_maximum_count_in_ambiguous_overlap(self):
        # P0 matches both targets; P1 only T0. Greedy matching would miss a TP.
        result = evaluate([Box(0, 0, .6, 1), Box(0, 0, .4, 1)],
                          [Box(0, 0, .4, 1), Box(.2, 0, .6, 1)], threshold=.5)
        self.assertEqual(result['tp'], 2)

    def test_empty_and_missing_targets(self):
        empty = evaluate([], [])
        self.assertIsNone(empty['precision'])
        self.assertIsNone(empty['recall'])
        self.assertTrue(empty['empty_scene_correct'])
        missing = evaluate([], [Box(0, 0, 1, 1)])
        self.assertEqual(missing['fn'], 1)
        self.assertEqual(missing['recall'], 0)
        self.assertEqual(missing['f1'], 0)

    def test_mismatched_coordinate_frame_is_rejected(self):
        truth = {'coordinate_system': COORDINATES, 'image': {'width': 800, 'height': 600}, 'boxes': []}
        pred = {**truth, 'detections': [], 'image': {'width': 600, 'height': 800}}
        with self.assertRaises(ValueError):
            evaluate_documents(pred, truth)


class ImageTests(unittest.TestCase):
    def test_resize_pad_roundtrip_and_metadata_removed(self):
        image = Image.new('RGB', (2400, 300))
        prepared = prepare(png_bytes(image))
        self.assertEqual(prepared.image.size, (1568, 280))
        b = Box(.1, .2, .9, .8)
        actual = prepared.to_original(prepared.to_prepared(b))
        for k, value in b.json().items():
            self.assertAlmostEqual(actual.json()[k], value)
        self.assertFalse(prepared.image.info)

    def test_exif_rotation_applied_and_stripped(self):
        image = Image.new('RGB', (640, 480), 'red')
        exif = Image.Exif()
        exif[274] = 6
        raw = io.BytesIO()
        image.save(raw, format='JPEG', exif=exif)
        prepared = prepare(raw.getvalue())
        self.assertEqual(prepared.original.size, (480, 640))
        self.assertFalse(prepared.original.getexif())

    def test_small_image_is_padded(self):
        prepared = prepare(png_bytes(Image.new('RGB', (100, 150))))
        self.assertEqual(prepared.image.size, (280, 280))
        self.assertEqual(prepared.resized, (100, 150))

    def test_animated_unsupported_and_damaged_inputs_rejected(self):
        animated = io.BytesIO()
        frames = [Image.new('RGB', (300, 300), c) for c in ('red', 'blue')]
        frames[0].save(animated, format='PNG', save_all=True, append_images=frames[1:])
        for value in (b'', b'not an image', animated.getvalue()):
            with self.subTest(size=len(value)), self.assertRaises(ValueError):
                prepare(value)


class DetectorTests(unittest.TestCase):
    def setUp(self):
        image, self.boxes = fixture('multiple')
        self.prepared = prepare(png_bytes(image))
        self.responses = mock_script(self.prepared, self.boxes)

    def test_all_synthetic_cases_and_overlay(self):
        # A perfect replay is expected by construction, not a model benchmark.
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('Network forbidden')):
            for scenario in SCENARIOS:
                with self.subTest(scenario=scenario):
                    image, boxes = fixture(scenario)
                    prepared = prepare(png_bytes(image))
                    result = detect(prepared, MockClient(mock_script(prepared, boxes)))
                    self.assertEqual(result['mode'], 'mock')
                    self.assertEqual(result['api_calls'], 0)
                    self.assertEqual(result['status'], 'complete')
                    metrics = evaluate([Box.read(d['box']) for d in result['detections']], boxes)
                    self.assertEqual(metrics['tp'], len(boxes))
                    self.assertEqual((metrics['fp'], metrics['fn']), (0, 0))
                    self.assertEqual(overlay(prepared, result).size, image.size)
                    for b in boxes:
                        self.assertGreater(crop(prepared, b).width, 0)

    def test_absence_discards_box_even_if_invalid(self):
        response = copy.deepcopy(self.responses[-1])
        response['answers']['card']['box'] = {'invalid': 'ignored'}
        result = detect(self.prepared, MockClient([response]))
        self.assertEqual(result['detections'], [])
        self.assertEqual(result['stop_reason'], 'no_remaining_target')

    def test_duplicate_stops_incomplete(self):
        result = detect(self.prepared, MockClient([self.responses[0], self.responses[0]]))
        self.assertEqual(len(result['detections']), 1)
        self.assertEqual(result['stop_reason'], 'duplicate_target')
        self.assertEqual(result['status'], 'incomplete')

    def test_card_and_request_limits_do_not_claim_completion(self):
        for settings, reason in [(Settings(max_cards=1), 'card_limit'),
                                 (Settings(max_requests=1), 'request_limit')]:
            result = detect(self.prepared, MockClient(self.responses), settings)
            self.assertEqual(result['stop_reason'], reason)
            self.assertEqual(result['status'], 'incomplete')

    def test_uncertain_presence_and_low_confidence(self):
        for kind, value, reason in [('noul', .5, 'uncertain_presence'),
                                    ('confidence', .1, 'low_box_confidence')]:
            response = copy.deepcopy(self.responses[0])
            if kind == 'noul':
                response['answers']['remaining']['noul'] = value
            else:
                response['answers']['card']['confidence'] = value
            result = detect(self.prepared, MockClient([response]))
            self.assertEqual(result['stop_reason'], reason)
            self.assertEqual(result['detections'], [])

    def test_invalid_answer_preserves_partial_results(self):
        broken = copy.deepcopy(self.responses[1])
        broken['answers']['card']['box']['left'] = 2
        result = detect(self.prepared, MockClient([self.responses[0], broken]))
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['stop_reason'], 'invalid_provider_response')
        self.assertEqual(len(result['detections']), 1)

    def test_invalid_usage_and_model(self):
        for response in ({}, {'usage': {'input_tokens': -1, 'output_tokens': 2}},
                         {**self.responses[0], 'model': 'jev-1.13.0'}):
            result = detect(self.prepared, MockClient([response]))
            self.assertEqual(result['status'], 'error')


class ProviderTests(unittest.TestCase):
    def setUp(self):
        image, boxes = fixture('single')
        self.prepared = prepare(png_bytes(image))
        self.response = mock_script(self.prepared, boxes)[0]
        self.response['model'] = 'jev-1.14.0'
        self.response['usage'] = {'input_tokens': 1000, 'output_tokens': 30}

    def client(self, opener, budget=1, requests=2):
        with patch.dict(os.environ, {'MTG_JEV_ENABLE_LIVE': '1', 'TYPESAFE_API_KEY': 'unit-test-placeholder'}), \
                patch('urllib.request.build_opener', return_value=opener):
            return JevClient(LiveApproval(True, requests, budget))

    def test_gates_precede_key_and_network(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch('urllib.request.build_opener', side_effect=AssertionError('Network forbidden')):
            with self.assertRaises(ValueError):
                JevClient(LiveApproval())
            with self.assertRaises(ValueError):
                JevClient(LiveApproval(True, 2, 1))

    def test_enabled_without_key_is_rejected(self):
        with patch.dict(os.environ, {'MTG_JEV_ENABLE_LIVE': '1'}, clear=True):
            with self.assertRaises(ValueError):
                JevClient(LiveApproval(True, 2, 1))

    def test_multipart_contract(self):
        body, content_type = multipart(self.prepared, [])
        message = BytesParser(policy=policy.default).parsebytes(
            f'Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n'.encode() + body)
        parts = list(message.iter_parts())
        self.assertEqual([p.get_param('name', header='content-disposition') for p in parts], ['request', 'photo'])
        document = json.loads(parts[0].get_payload(decode=True))
        ref = document['state']['photo']
        self.assertIn(ref, document['assets'])
        self.assertEqual(document['assets'][ref]['part'], 'photo')
        self.assertEqual(document['model'], 'jev-1.14.0')
        self.assertEqual(document['questions']['card']['type'], 'box')
        self.assertNotIn('criteria', document['questions']['card'])
        self.assertEqual(parts[1].get_content_type(), 'image/png')
        self.assertEqual(Image.open(io.BytesIO(parts[1].get_payload(decode=True))).size, (800, 600))

    def test_stubbed_live_records_headers_usage_and_limits(self):
        captured = []
        response_data = json.dumps(self.response).encode()

        class Stub:
            def open(self, request, timeout):
                captured.append(request)
                return contextlib.closing(io.BytesIO(response_data))

        client = self.client(Stub(), requests=1)
        result = detect(self.prepared, client, Settings(max_requests=1))
        self.assertEqual(captured[0].get_header('Authorization'), 'Bearer unit-test-placeholder')
        self.assertEqual(captured[0].get_header('Typesafe-danger-zone-images'), 'true')
        self.assertEqual(result['usage']['input_tokens'], 1000)
        self.assertAlmostEqual(result['estimated_cost_usd'], .000042)
        self.assertNotIn('unit-test-placeholder', json.dumps(result))
        with self.assertRaises(ProviderError):
            client.ask(self.prepared, [])
        self.assertEqual(len(captured), 1)

    def test_http_error_body_is_redacted_and_not_retried(self):
        class Stub:
            calls = 0
            def open(self, request, timeout):
                self.calls += 1
                raise urllib.error.HTTPError(request.full_url, 429, 'sensitive body', {}, io.BytesIO(b'private'))
        stub = Stub()
        result = detect(self.prepared, self.client(stub))
        self.assertEqual(stub.calls, 1)
        self.assertEqual(result['api_calls'], 1)
        self.assertEqual(result['stop_reason'], 'Jev HTTP 429; response body omitted')
        self.assertNotIn('sensitive', json.dumps(result))

    def test_timeout_is_not_retried(self):
        class Stub:
            calls = 0
            def open(self, request, timeout):
                self.calls += 1
                raise TimeoutError('private content')
        stub = Stub()
        result = detect(self.prepared, self.client(stub))
        self.assertEqual(stub.calls, 1)
        self.assertEqual(result['status'], 'error')
        self.assertNotIn('private', json.dumps(result))

    def test_budget_reservation_blocks_network(self):
        class Stub:
            def open(self, request, timeout):
                raise AssertionError('Network forbidden')
        result = detect(self.prepared, self.client(Stub(), budget=.00001))
        self.assertEqual(result['api_calls'], 0)
        self.assertEqual(result['status'], 'error')

    def test_redirect_refused(self):
        with self.assertRaises(ProviderError):
            NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example')


class CLITests(unittest.TestCase):
    def test_demo_detect_evaluate_and_gated_live(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generate(root / 'demo')
            def cli(*args):
                return subprocess.run([sys.executable, '-m', 'mtg_jev', *map(str, args)],
                                      text=True, capture_output=True, env={**os.environ, 'MTG_JEV_ENABLE_LIVE': '0'})
            run = cli('detect', root / 'demo/rotated.png', '--mock-script', root / 'demo/rotated.mock.json',
                      '--output', root / 'result', '--save-crops')
            self.assertEqual(run.returncode, 0, run.stderr)
            saved = json.loads((root / 'result/result.json').read_text())
            self.assertEqual(saved['api_calls'], 0)
            self.assertTrue((root / 'result/overlay.png').is_file())
            self.assertTrue((root / 'result/card-1.png').is_file())
            metrics = cli('evaluate', root / 'result/result.json', root / 'demo/rotated.truth.json')
            self.assertEqual(json.loads(metrics.stdout)['recall'], 1)
            blocked = cli('detect', root / 'demo/rotated.png', '--mode', 'live', '--output', root / 'live')
            self.assertEqual(blocked.returncode, 2)
            self.assertFalse((root / 'live').exists())


if __name__ == '__main__':
    unittest.main()
