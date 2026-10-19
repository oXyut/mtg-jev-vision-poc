import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from mtg_jev.geometry import COORDINATES, Box
from mtg_jev.identification import (Catalog, TesseractOCR, evaluate_identifications,
                                     identify_document, normalize, parse_tsv)
from mtg_jev.identification_demo import generate_identification_demo
from mtg_jev.images import png_bytes, prepare


def catalog_document():
    return {'coverage': 'reference',
            'provenance': {'source': 'self-authored fictional fixtures', 'license': 'self-authored',
                           'retrieved_at': '2026-10-19', 'rights_confirmed': True},
            'cards': [{'id': 'amber', 'name': 'Amber Harbor'},
                      {'id': 'quiet', 'name': 'Quiet Orchard'},
                      {'id': 'azure', 'name': 'Azure Workshop'}]}


def observation(text, confidence=95):
    return {'text': text, 'confidence': confidence, 'rotation': 0}


class CatalogTests(unittest.TestCase):
    def test_exact_unique_high_quality(self):
        result = Catalog(catalog_document()).decide([observation('AMBER HARBOR')])
        self.assertEqual(result['status'], 'identified')
        self.assertEqual(result['card_id'], 'amber')
        self.assertIn('not_probability', result['score_type'])

    def test_ocr_typo_is_candidate_but_never_accepted(self):
        result = Catalog(catalog_document()).decide([observation('Amber Harb0r')])
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['candidates'][0]['id'], 'amber')

    def test_target_missing_from_candidate_catalog_is_unknown(self):
        document = catalog_document()
        document['cards'] = [{'id': 'near', 'name': 'Amber Harbors'}]
        result = Catalog(document).decide([observation('Amber Harbor')])
        self.assertEqual(result['status'], 'unknown')
        self.assertIsNone(result['card_id'])
        self.assertEqual(result['candidates'][0]['id'], 'near')

    def test_partial_catalog_exact_match_is_candidate_only(self):
        document = catalog_document()
        document['coverage'] = 'partial'
        result = Catalog(document).decide([observation('Amber Harbor')])
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['reason'], 'partial_catalog_candidate_only')

    def test_display_top_k_does_not_hide_alias_collision(self):
        document = catalog_document()
        document['cards'][1]['aliases'] = ['Amber Harbor']
        result = Catalog(document).decide([observation('Amber Harbor')], top_k=1)
        self.assertEqual(len(result['candidates']), 1)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['reason'], 'ambiguous_exact_match')

    def test_conflicting_rotation_observations_are_unknown(self):
        observations = [observation('Amber Harbor'), {**observation('Quiet Orchard'), 'rotation': 180}]
        self.assertEqual(Catalog(catalog_document()).decide(observations)['status'], 'unknown')

    def test_low_quality_short_and_empty_are_unknown(self):
        for observations in ([], [observation('Amber Harbor', 59)], [observation('Amber Harbor', 70)],
                             [observation('abc')]):
            with self.subTest(observations=observations):
                self.assertEqual(Catalog(catalog_document()).decide(observations)['status'], 'unknown')

    def test_engine_failure_never_promotes_candidates(self):
        result = Catalog(catalog_document()).decide([observation('Amber Harbor')], ocr_status='ocr_incomplete')
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['reason'], 'ocr_incomplete')

    def test_unicode_aliases_and_normalization(self):
        document = catalog_document()
        document['cards'][0]['aliases'] = ['琥珀の港']
        self.assertEqual(Catalog(document).decide([observation('琥珀の港')])['card_id'], 'amber')
        self.assertEqual(normalize('Ａｍｂｅｒ—Harbor!'), 'amberharbor')

    def test_provenance_and_unique_ids_required(self):
        for mutate in (lambda d: d['provenance'].update(rights_confirmed=False),
                       lambda d: d['cards'].append(d['cards'][0]),
                       lambda d: d.update(coverage='complete-guaranteed')):
            document = catalog_document()
            mutate(document)
            with self.assertRaises(ValueError):
                Catalog(document)


class OCRTests(unittest.TestCase):
    def test_tsv_groups_lines_and_uses_minimum_word_quality(self):
        tsv = ('level\tpage_num\tblock_num\tpar_num\tline_num\tconf\ttext\n'
               '5\t1\t1\t1\t1\t96\tAmber\n5\t1\t1\t1\t1\t82\tHarbor\n'
               '5\t1\t1\t1\t2\tnan\tignored\n')
        self.assertEqual(parse_tsv(tsv, 90), [{'text': 'Amber Harbor', 'confidence': 82, 'rotation': 90}])

    def test_missing_engine_is_explicit_unknown_condition(self):
        with patch('shutil.which', return_value=None):
            observations, status = TesseractOCR().read(Image.new('RGB', (400, 560)))
        self.assertEqual(observations, [])
        self.assertEqual(status, 'ocr_engine_unavailable')

    def test_missing_language_and_invalid_language(self):
        with patch('shutil.which', return_value='/usr/bin/tesseract'), \
             patch('subprocess.run', return_value=SimpleNamespace(stdout=b'Languages:\neng\n', returncode=0)):
            self.assertEqual(TesseractOCR('jpn').status, 'ocr_language_unavailable')
        with self.assertRaises(ValueError):
            TesseractOCR('eng;echo private')

    def test_timeout_preserves_unknown_without_stderr_leak(self):
        with patch('shutil.which', return_value='/usr/bin/tesseract'), \
             patch('subprocess.run', return_value=SimpleNamespace(stdout=b'Languages:\neng\n', returncode=0)):
            engine = TesseractOCR()
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('tesseract', 5)):
            observations, status = engine.read(Image.new('RGB', (400, 560)))
        self.assertEqual((observations, status), ([], 'ocr_incomplete'))

    @unittest.skipUnless(shutil.which('tesseract'), 'Optional local Tesseract not installed')
    def test_real_local_ocr_on_rotated_self_authored_text(self):
        image = Image.new('RGB', (400, 560), 'white')
        ImageDraw.Draw(image).text((24, 30), 'AMBER HARBOR', fill='black', font=ImageFont.load_default(size=34))
        observations, status = TesseractOCR().read(image.rotate(90, expand=True))
        self.assertEqual(status, 'ready')
        result = Catalog(catalog_document()).decide(observations)
        self.assertEqual(result['card_id'], 'amber')


class IdentificationPipelineTests(unittest.TestCase):
    def test_crop_source_and_private_ocr_text_are_not_logged_or_saved(self):
        prepared = prepare(png_bytes(Image.new('RGB', (800, 600))))
        document = {'coordinate_system': COORDINATES, 'image': {'width': 800, 'height': 600},
                    'detections': [{'box': Box(.1, .1, .4, .8).json()}]}
        class Stub:
            language = 'stub'
            def read(self, image):
                self.size = image.size
                return [observation('private unrelated background text')], 'ready'
        engine = Stub()
        result = identify_document(prepared, document, Catalog(catalog_document()), engine, 'ground_truth')
        self.assertEqual(engine.size, (240, 420))
        self.assertEqual(result['identification_run']['crop_source'], 'ground_truth')
        self.assertNotIn('private unrelated', json.dumps(result))
        self.assertEqual(result['identification_run']['external_api_calls'], 0)
        self.assertNotIn('identification', document['detections'][0])

    @unittest.skipUnless(shutil.which('tesseract'), 'Optional local Tesseract not installed')
    def test_cli_real_ocr_synthetic_fixture_no_external_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generate_identification_demo(root / 'demo')
            result_path = root / 'result.json'
            run = subprocess.run([sys.executable, '-m', 'mtg_jev', 'identify', str(root / 'demo/photo.png'),
                                  '--detections', str(root / 'demo/detections.json'),
                                  '--catalog', str(root / 'demo/catalog.json'), '--output', str(result_path),
                                  '--crop-source', 'ground_truth'], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = json.loads(result_path.read_text())
            truth = json.loads((root / 'demo/truth.json').read_text())
            metrics = evaluate_identifications(result, truth)
            self.assertEqual(metrics['top1_on_matched_known'], 1)
            self.assertEqual(result['mode'], 'synthetic_geometry')
            self.assertEqual(result['identification_run']['external_api_calls'], 0)


class IdentificationEvaluationTests(unittest.TestCase):
    def documents(self):
        frame = {'coordinate_system': COORDINATES, 'image': {'width': 800, 'height': 600}}
        a, b = Box(.1, .1, .3, .5).json(), Box(.6, .1, .8, .5).json()
        pred = {**frame, 'identification_run': {'crop_source': 'predicted'}, 'detections': [
            {'box': a, 'identification': {'status': 'identified', 'card_id': 'amber',
                                         'candidates': [{'id': 'amber'}]}}]}
        truth = {**frame, 'objects': [{'box': a, 'card_id': 'amber'}, {'box': b, 'card_id': 'quiet'}]}
        return pred, truth

    def test_localization_miss_reduces_end_to_end_not_conditioned_accuracy(self):
        pred, truth = self.documents()
        metrics = evaluate_identifications(pred, truth)
        self.assertEqual(metrics['top1_on_matched_known'], 1)
        self.assertEqual(metrics['end_to_end_name_recall'], .5)
        self.assertEqual(metrics['localization']['fn'], 1)

    def test_accepted_name_on_false_positive_is_wrong(self):
        pred, truth = self.documents()
        pred['detections'].append({'box': Box(.4, .6, .5, .8).json(), 'identification': {
            'status': 'identified', 'card_id': 'quiet', 'candidates': [{'id': 'quiet'}]}})
        metrics = evaluate_identifications(pred, truth)
        self.assertEqual(metrics['misidentification_rate_among_accepted'], .5)
        self.assertEqual(metrics['end_to_end_name_precision'], .5)

    def test_unknown_keeps_candidate_recall_separate(self):
        pred, truth = self.documents()
        pred['detections'][0]['identification'] = {'status': 'unknown', 'card_id': None,
                                                   'candidates': [{'id': 'quiet'}, {'id': 'amber'}]}
        metrics = evaluate_identifications(pred, truth, top_k=2)
        self.assertEqual(metrics['top1_on_matched_known'], 0)
        self.assertEqual(metrics['topk_on_matched_known'], 1)
        self.assertEqual(metrics['unknown_rate'], 1)
        self.assertIsNone(metrics['misidentification_rate_among_accepted'])

    def test_empty_case_has_null_rates(self):
        pred, truth = self.documents()
        pred['detections'], truth['objects'] = [], []
        metrics = evaluate_identifications(pred, truth)
        self.assertIsNone(metrics['unknown_rate'])
        self.assertIsNone(metrics['end_to_end_name_recall'])


if __name__ == '__main__':
    unittest.main()
