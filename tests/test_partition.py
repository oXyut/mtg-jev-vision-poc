import unittest
from email import policy
from email.parser import BytesParser
import json

from PIL import Image

from mtg_jev.geometry import Box
from mtg_jev.images import prepare, png_bytes
from mtg_jev.partition import (PartitionSettings, detect_partition, merge_candidates,
                               split_region, to_global)
from mtg_jev.provider import multipart, partition_document


def answer(label, box=None, confidence=.9, presence=None):
    probabilities = {k: (.9 if k == label else .05) for k in ('none', 'one', 'several')}
    return {'model': 'mock-script', 'usage': {'input_tokens': 0, 'output_tokens': 0},
            'answers': {'present': {'type': 'noul', 'noul': (.01 if label == 'none' else .99) if presence is None else presence},
                        'count': {'type': 'choice', 'choice': label, 'confidence': .9,
                                  'probabilities': probabilities},
                        'card': {'type': 'box', 'box': box, 'confidence': confidence}}}


class Script:
    mode = 'mock'

    def __init__(self, responses):
        self.responses, self.calls = responses, 0

    def ask_partition(self, tile):
        result = self.responses[self.calls]
        self.calls += 1
        return result


class PartitionTests(unittest.TestCase):
    def setUp(self):
        self.prepared = prepare(png_bytes(Image.new('RGB', (800, 600), 'white')))
        self.box = Box(.1, .1, .9, .9).json()

    def test_none_ignores_arbitrary_invalid_box(self):
        result = detect_partition(self.prepared, Script([answer('none', 'invalid')]))
        self.assertEqual(result['detections'], [])
        self.assertEqual(result['status'], 'complete')

    def test_every_child_is_visited_and_coordinates_map_to_original(self):
        client = Script([answer('several'), answer('one', self.box), answer('one', self.box)])
        result = detect_partition(self.prepared, client)
        self.assertEqual(client.calls, 3)
        self.assertEqual([t['region_id'] for t in result['trace']], ['root', 'rootL', 'rootR'])
        self.assertEqual(len(result['detections']), 2)
        self.assertAlmostEqual(result['detections'][0]['box']['right'], .5625)
        self.assertAlmostEqual(result['detections'][1]['box']['left'], .4375)

    def test_request_and_depth_limits_remain_incomplete(self):
        result = detect_partition(self.prepared, Script([answer('several')]), PartitionSettings(max_requests=1))
        self.assertEqual(result['stop_reason'], 'request_limit')
        self.assertEqual(len(result['pending_regions']), 2)
        result = detect_partition(self.prepared, Script([answer('several')]), PartitionSettings(max_depth=0))
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['unresolved_regions'], ['root'])

    def test_low_and_null_box_confidence_never_mean_absence(self):
        for confidence in (.1, None):
            result = detect_partition(self.prepared, Script([answer('one', self.box, confidence)]), PartitionSettings(max_depth=0))
            self.assertEqual(result['status'], 'incomplete')
            self.assertEqual(result['detections'], [])
            self.assertEqual(result['trace'][0]['box_confidence'], confidence)

    def test_conflicting_presence_and_count_is_not_complete_absence(self):
        result = detect_partition(self.prepared, Script([answer('none', presence=.99)]), PartitionSettings(max_depth=0))
        self.assertEqual(result['status'], 'incomplete')

    def test_invalid_count_probabilities_are_not_accepted(self):
        response = answer('one', self.box)
        response['answers']['count']['probabilities']['one'] = -1
        result = detect_partition(self.prepared, Script([response]))
        self.assertEqual(result['status'], 'error')

    def test_overlap_merge_unions_fragments_and_keeps_disjoint_cards(self):
        candidates = [{'box': Box(.1, .1, .5, .7).json(), 'confidence': .9, 'region_id': 'a'},
                      {'box': Box(.12, .1, .52, .7).json(), 'confidence': .8, 'region_id': 'b'},
                      {'box': Box(.6, .1, .9, .7).json(), 'confidence': .9, 'region_id': 'c'}]
        result = merge_candidates(candidates, PartitionSettings())
        self.assertEqual(len(result), 2)
        merged = next(d for d in result if len(d['members']) == 2)
        self.assertEqual(merged['box'], Box(.1, .1, .52, .7).json())

    def test_padding_round_trip_before_global_mapping(self):
        tile = prepare(png_bytes(Image.new('RGB', (100, 150))))
        local = Box(.1, .2, .9, .8)
        decoded = tile.to_original(tile.to_prepared(local))
        actual = to_global(decoded, (200, 300, 300, 450), (800, 600))
        self.assertAlmostEqual(actual.left, 210/800)
        self.assertAlmostEqual(actual.bottom, 420/600)
        a, b = split_region((0, 0, 800, 600), .25)
        self.assertEqual((a, b), ((0, 0, 500, 600), (300, 0, 800, 600)))

    def test_partition_multipart_uses_one_image_and_no_truth_or_exclusions(self):
        body, content_type = multipart(self.prepared, [], partition_document)
        msg = BytesParser(policy=policy.default).parsebytes(
            f'Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n'.encode()+body)
        parts = list(msg.iter_parts())
        document = json.loads(parts[0].get_payload(decode=True))
        self.assertEqual(len(document['assets']), 1)
        self.assertEqual(set(document['state']), {'photo'})
        self.assertEqual(set(document['questions']['count']['criteria']), {'none', 'one', 'several'})
        self.assertEqual(document['questions']['present']['type'], 'noul')
        self.assertNotIn('excluded_boxes', json.dumps(document))


if __name__ == '__main__':
    unittest.main()
