import contextlib
import io
import json
import os
import unittest
from email import policy
from email.parser import BytesParser
from unittest.mock import patch

from PIL import Image

from mtg_jev.batch import (BatchSettings, count_document, detect_batch, ranked_document, short_ranked_document)
from mtg_jev.geometry import Box
from mtg_jev.images import png_bytes, prepare
from mtg_jev.provider import JevClient, LiveApproval, ProviderError, multipart


def count_answer(n, confidence=.99, presence=None, capacity=16):
    label = str(n)
    labels = [str(k) for k in range(capacity+1)]+['over_capacity']
    probabilities = {k: (confidence if k == label else (1-confidence)/(len(labels)-1)) for k in labels}
    return {'model': 'mock-script', 'usage': {'input_tokens': 0, 'output_tokens': 0},
            'answers': {'present': {'type': 'noul', 'noul': (0 if n == 0 else .99) if presence is None else presence},
                        'count': {'type': 'choice', 'choice': label, 'confidence': confidence, 'probabilities': probabilities}}}


def slots_answer(boxes):
    answers = {}
    for rank, box in enumerate(boxes, 1):
        answers[f'slot_{rank:02d}_present'] = {'type': 'noul', 'noul': .99}
        answers[f'slot_{rank:02d}_box'] = {'type': 'box', 'box': box.json(), 'confidence': .9}
    return {'model': 'mock-script', 'usage': {'input_tokens': 0, 'output_tokens': 0}, 'answers': answers}


class Script:
    mode = 'mock'

    def __init__(self, count, slots=None):
        self.count, self.slots, self.calls, self.batch_count = count, slots, 0, None

    def ask_count(self, prepared, capacity):
        self.calls += 1
        return self.count

    def ask_ranked(self, prepared, count):
        self.calls += 1
        self.batch_count = count
        return self.slots


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.prepared = prepare(png_bytes(Image.new('RGB', (800, 600))))
        self.boxes = [Box(.05, .1, .2, .7), Box(.4, .1, .6, .7), Box(.8, .1, .95, .7)]

    def test_two_calls_use_model_predicted_count_not_capacity(self):
        client = Script(count_answer(3), slots_answer(self.boxes))
        result = detect_batch(self.prepared, client)
        self.assertEqual((client.calls, client.batch_count), (2, 3))
        self.assertEqual(result['estimated_count'], 3)
        self.assertEqual(result['question_count'], 2+2*3)
        self.assertEqual(len(result['detections']), 3)
        self.assertEqual(result['api_calls'], 0)
        self.assertEqual(result['status'], 'complete')

    def test_uncertain_count_and_over_capacity_never_generate_boxes(self):
        for count in (count_answer(3, confidence=.5), count_answer('over_capacity')):
            client = Script(count)
            result = detect_batch(self.prepared, client)
            self.assertEqual(client.calls, 1)
            self.assertIsNone(client.batch_count)
            self.assertEqual(result['status'], 'incomplete')

    def test_zero_count_and_presence_contradiction_are_separate(self):
        result = detect_batch(self.prepared, Script(count_answer(0)))
        self.assertEqual(result['stop_reason'], 'model_count_zero')
        result = detect_batch(self.prepared, Script(count_answer(0, presence=.99)))
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['stop_reason'], 'count_presence_inconsistent')

    def test_presence_rejection_ignores_unneeded_box_and_null_is_unresolved(self):
        slots = slots_answer(self.boxes)
        slots['answers']['slot_02_present']['noul'] = 0
        slots['answers']['slot_02_box']['box'] = ['not', 'a', 'box']
        slots['answers']['slot_03_box']['confidence'] = None
        result = detect_batch(self.prepared, Script(count_answer(3), slots))
        self.assertEqual([c['status'] for c in result['candidates']], ['accepted', 'rejected', 'unresolved'])
        self.assertEqual(len(result['detections']), 1)
        self.assertIsNone(result['candidates'][2]['confidence'])

    def test_possible_duplicates_remain_unresolved_without_union_or_suppression(self):
        result = detect_batch(self.prepared, Script(count_answer(2), slots_answer([self.boxes[0]]*2)))
        self.assertEqual(result['detections'], [])
        self.assertEqual(len(result['candidates']), 2)
        self.assertEqual(len(result['possible_duplicate_pairs']), 1)
        self.assertTrue(all(c['status'] == 'unresolved' for c in result['candidates']))

    def test_order_inconsistency_is_diagnostic_without_truth_reordering(self):
        reverse = list(reversed(self.boxes))
        result = detect_batch(self.prepared, Script(count_answer(3), slots_answer(reverse)))
        self.assertEqual([d['box'] for d in result['detections']], [b.json() for b in reverse])
        self.assertEqual(result['stop_reason'], 'rank_order_inconsistent')
        self.assertEqual(len(result['rank_order_violations']), 3)

    def test_one_image_multiple_box_questions_have_distinct_inline_targets(self):
        body, content_type = multipart(self.prepared, [], lambda unused, ref: ranked_document(ref, 3))
        msg = BytesParser(policy=policy.default).parsebytes(
            f'Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n'.encode()+body)
        parts = list(msg.iter_parts())
        doc = json.loads(parts[0].get_payload(decode=True))
        self.assertEqual(len(parts), 2)
        self.assertEqual(len(doc['assets']), 1)
        self.assertEqual(sum(q['type'] == 'box' for q in doc['questions'].values()), 3)
        for rank in range(1, 4):
            self.assertIn(f'1-based rank {rank}', doc['questions'][f'slot_{rank:02d}_box']['instructions'])
        self.assertEqual(set(doc['state']), {'photo'})

    def test_no_box_array_type_is_assumed(self):
        slots = slots_answer(self.boxes[:1])
        slots['answers']['slot_01_box']['box'] = [self.boxes[0].json(), self.boxes[1].json()]
        self.assertEqual(detect_batch(self.prepared, Script(count_answer(1), slots))['status'], 'error')

    def test_speculative_capacity_always_has_per_slot_existence(self):
        doc = short_ranked_document('opaque', 16)
        self.assertEqual(len(doc['questions']), 32)
        self.assertEqual(len(doc['assets']), 1)
        for rank in range(1,17):
            self.assertIn(f'at least {rank}', doc['questions'][f'slot_{rank:02d}_present']['instructions'])
            self.assertIn(f'position {rank}', doc['questions'][f'slot_{rank:02d}_box']['instructions'])

    def test_new_approval_ceiling_and_question_scaled_reservation(self):
        with patch.dict(os.environ, {'MTG_JEV_ENABLE_LIVE': '1', 'TYPESAFE_API_KEY': 'unit-test-placeholder'}):
            LiveApproval(True, 100, 5).validate()
            with self.assertRaises(ValueError): LiveApproval(True, 100, 5.01).validate()
            class Forbidden:
                def open(self, *args, **kwargs): raise AssertionError('Budget should block network')
            with patch('urllib.request.build_opener', return_value=Forbidden()):
                client = JevClient(LiveApproval(True, 100, .005))
                with self.assertRaises(ProviderError): client.ask_ranked(self.prepared, 3)
                self.assertEqual(client.calls, 0)


if __name__ == '__main__':
    unittest.main()
