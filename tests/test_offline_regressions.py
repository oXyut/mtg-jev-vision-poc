"""Self-authored geometry and catalog regressions. No real image/API evidence."""
import unittest
from collections import deque

from PIL import Image

from mtg_jev.geometry import Box, evaluate, iou
from mtg_jev.images import png_bytes, prepare
from mtg_jev.identification import Catalog
from mtg_jev.partition import (PartitionSettings, classify_boundaries, containment, detect_partition,
                               merge_candidates, split_region)


class GeometryOracle:
    mode = 'mock'

    def __init__(self, objects, size=(1536, 1152)):
        self.objects, self.size = objects, size
        self.regions = deque([(0, 0, *size)])
        self.calls = 0

    def ask_partition(self, tile):
        region = self.regions.popleft()
        l, t, r, b = region
        intersections = []
        for obj in self.objects:
            x0, y0, x1, y1 = obj.pixels(*self.size)
            x0, y0, x1, y1 = max(l, x0), max(t, y0), min(r, x1), min(b, y1)
            if x0 < x1 and y0 < y1:
                intersections.append(Box((x0-l)/(r-l), (y0-t)/(b-t),
                                         (x1-l)/(r-l), (y1-t)/(b-t)))
        label = 'none' if not intersections else 'one' if len(intersections) == 1 else 'several'
        if label == 'several':
            self.regions.extend(split_region(region, .25))
        self.calls += 1
        box = tile.to_prepared(intersections[0] if intersections else Box(.1, .1, .9, .9))
        return {'model': 'mock-script', 'usage': {'input_tokens': 0, 'output_tokens': 0},
                'answers': {'present': {'type': 'noul', 'noul': 0 if label == 'none' else 1},
                            'count': {'type': 'choice', 'choice': label, 'confidence': 1,
                                      'probabilities': {k: int(k == label) for k in ('none', 'one', 'several')}},
                            'card': {'type': 'box', 'box': box.json(), 'confidence': 1}}}


def short_catalog():
    return Catalog({'coverage': 'reference', 'provenance': {
        'source': 'generic short-name regression data, not an evaluation-photo catalog',
        'license': 'self-authored test data', 'retrieved_at': '2026-10-19', 'rights_confirmed': True},
        'cards': [{'id': 'basic-a', 'name': 'Plains', 'aliases': ['平地']},
                  {'id': 'basic-b', 'name': 'Mountain', 'aliases': ['山']},
                  {'id': 'fictional', 'name': 'Fictional Quiet Harbor'}]})


class ConfirmedRegressions(unittest.TestCase):
    def test_geometry_oracle_fragments_are_not_four_complete_detections(self):
        objects = [Box(.1, .1, .9, .35), Box(.1, .65, .9, .9)]
        prepared = prepare(png_bytes(Image.new('RGB', (1536, 1152))))
        oracle = GeometryOracle(objects)
        result = detect_partition(prepared, oracle)
        self.assertEqual(oracle.calls, 7)
        fragments = [Box.read(c['box']) for c in result['candidates']]
        self.assertEqual(len(fragments), 4)
        self.assertAlmostEqual(iou(fragments[0], fragments[2]), .3125)
        self.assertAlmostEqual(containment(fragments[0], fragments[2]), 10/21)
        for threshold, expected in [(.5, (2, 2, 0)), (.75, (0, 4, 2))]:
            metrics = evaluate(fragments, objects, threshold)
            self.assertEqual((metrics['tp'], metrics['fp'], metrics['fn']), expected)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['detections'], [])
        self.assertEqual(result['counters']['provider_calls'], 7)
        self.assertEqual(result['counters']['http_requests'], 0)
        self.assertEqual(result['counters']['questions_evaluated'], 21)
        self.assertEqual(result['counters']['regions_visited'], 7)
        self.assertEqual(result['counters']['unresolved_regions'], 4)
        self.assertTrue(all(c['boundaries']['artificial_crop'] for c in result['candidates']))

    def test_containment_is_not_identity_evidence(self):
        candidates = [{'box': Box(.1, .1, .9, .9).json(), 'confidence': .7, 'region_id': 'a'},
                      {'box': Box(.2, .2, .8, .8).json(), 'confidence': .99, 'region_id': 'b'}]
        result = merge_candidates(candidates, PartitionSettings())
        self.assertEqual(len(result), 2)
        self.assertEqual([d['confidence'] for d in result], [.7, .99])
        self.assertEqual([d['box'] for d in result], [c['box'] for c in candidates])
        self.assertTrue(all(d['status'] == 'unresolved' for d in result))

    def test_short_exact_cjk_names_are_not_discarded_by_length(self):
        for text in ('平地', '山'):
            result = short_catalog().decide([{'text': text, 'confidence': 99}])
            self.assertEqual(result['status'], 'identified')

    def test_original_photo_edge_is_not_an_artificial_crop_edge(self):
        tile = prepare(png_bytes(Image.new('RGB', (100, 150))))
        local = Box(0, 0, 1, 1)
        raw = tile.to_prepared(local)
        bounds = classify_boundaries(local, raw, tile, (0, 0, 100, 150), (100, 150))
        self.assertEqual(set(bounds['original_image']), {'left', 'top', 'right', 'bottom'})
        self.assertEqual(bounds['artificial_crop'], [])
        self.assertEqual(bounds['padding'], [])
        raw = Box(0, 0, 1, 1)
        bounds = classify_boundaries(local, raw, tile, (100, 100, 200, 250), (800, 600))
        self.assertEqual(set(bounds['artificial_crop']), {'left', 'top', 'right', 'bottom'})
        self.assertEqual(set(bounds['padding']), {'left', 'top', 'right', 'bottom'})

    def test_transitive_overlap_does_not_merge_or_promote_confidence(self):
        candidates = [{'box': b.json(), 'confidence': c, 'region_id': str(i)} for i, (b, c) in enumerate([
            (Box(.1, .1, .5, .8), .61), (Box(.3, .1, .7, .8), .99), (Box(.5, .1, .9, .8), .7)])]
        result = merge_candidates(candidates, PartitionSettings())
        self.assertEqual([r['confidence'] for r in result], [.61, .99, .7])
        self.assertEqual(len(result), 3)
        self.assertTrue(all(r['status'] == 'unresolved' for r in result))

    def test_short_names_language_quality_unknown_and_conflicting_aliases(self):
        c = short_catalog()
        for text in ('平地', '山'):
            self.assertEqual(c.decide([{'text': text, 'confidence': 99}], language='jpn')['status'], 'identified')
            self.assertEqual(c.decide([{'text': text, 'confidence': 99}], language='eng')['status'], 'unknown')
            self.assertEqual(c.decide([{'text': text, 'confidence': 90}], language='jpn')['status'], 'unknown')
        for typo in ('平池', '岀', '辞書外'):
            self.assertEqual(c.decide([{'text': typo, 'confidence': 99}], language='jpn')['status'], 'unknown')
        # A wrong OCR reading that is another valid title cannot be disproved by
        # lookup alone; contradictory high-quality readings must remain unknown.
        self.assertEqual(c.decide([{'text': '平地', 'confidence': 99}, {'text': '山', 'confidence': 99}], language='jpn')['status'], 'unknown')
        c.aliases['山'].add('fictional')
        self.assertEqual(c.decide([{'text': '山', 'confidence': 99}], top_k=1, language='jpn')['reason'], 'ambiguous_exact_match')

    def test_full_tree_region_demand_is_separate_from_http_and_region_limits(self):
        oracle = GeometryOracle([Box(0, 0, 1, 1), Box(0, 0, 1, 1)])
        prepared = prepare(png_bytes(Image.new('RGB', (1536, 1152))))
        result = detect_partition(prepared, oracle, PartitionSettings(max_requests=13))
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['counters']['full_tree_regions_at_max_depth'], 31)
        self.assertEqual(result['counters']['questions_evaluated'], 39)
        self.assertEqual(result['counters']['pending_regions'], 14)
        self.assertEqual(max(t['depth'] for t in result['trace']), 3)
        oracle = GeometryOracle([Box(0, 0, 1, 1), Box(0, 0, 1, 1)])
        capped = detect_partition(prepared, oracle, PartitionSettings(max_requests=30))
        self.assertEqual(capped['status'], 'incomplete')
        self.assertEqual(capped['counters']['provider_calls'], 30)
        self.assertEqual(capped['counters']['questions_evaluated'], 90)
        self.assertEqual(capped['counters']['unresolved_regions'], 16)
        self.assertEqual(capped['counters']['pending_regions'], 1)
        oracle = GeometryOracle([Box(0, 0, 1, 1), Box(0, 0, 1, 1)])
        result = detect_partition(prepared, oracle, PartitionSettings(max_requests=30, max_regions=7))
        self.assertEqual(result['stop_reason'], 'region_limit')
        self.assertEqual(result['counters']['regions_visited'], 7)


if __name__ == '__main__':
    unittest.main()
