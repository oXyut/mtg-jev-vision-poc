import unittest
from unittest.mock import patch

from mtg_jev.geometry import Box, iou
from mtg_jev.ownership import (CORES, core_plan, evaluate_core_response,
                               owner_of_point, ownership_of_box)


def empty_response():
    answers = {}
    for c in CORES:
        answers[c.key+'_present'] = {'type': 'noul', 'noul': 0}
        answers[c.key+'_resolved'] = {'type': 'noul', 'noul': 1}
        answers[c.key+'_count'] = {'type': 'choice', 'choice': 'none', 'confidence': 1,
                                   'probabilities': {'none': 1, 'one': 0, 'several': 0}}
        answers[c.key+'_box'] = {'type': 'box', 'box': 'arbitrary ignored no-target box', 'confidence': None}
    return {'model': 'mock-script', 'answers': answers}


def put_one(response, key, box, confidence=.9):
    response['answers'][key+'_present']['noul'] = 1
    response['answers'][key+'_count'].update(choice='one', probabilities={'none': 0, 'one': 1, 'several': 0})
    response['answers'][key+'_box'].update(box=box.json(), confidence=confidence)


class OwnershipPlannerTests(unittest.TestCase):
    def test_fixed_grid_is_nonoverlapping_and_context_only_overlaps(self):
        self.assertEqual(len(CORES), 16)
        self.assertAlmostEqual(sum((c.box.right-c.box.left)*(c.box.bottom-c.box.top) for c in CORES), 1)
        for i, c in enumerate(CORES):
            for other in CORES[i+1:]:
                self.assertEqual(iou(c.box, other.box), 0)
        self.assertGreater(iou(CORES[0].context, CORES[1].context), 0)

    def test_half_open_boundary_is_unique_but_uncertain_center_is_unresolved(self):
        self.assertEqual(owner_of_point(.25, .125).key, 'r0c1')
        self.assertEqual(owner_of_point(.25, .25).key, 'r1c1')
        self.assertIsNone(owner_of_point(1, .5))
        box = Box(.2, .05, .3, .2)
        exact = ownership_of_box(box, (1536, 1152), point_source='original_visible_aabb', uncertainty_pixels=0)
        self.assertEqual(exact['core'], 'r0c1')
        uncertain = ownership_of_box(box, (1536, 1152), point_source='original_visible_aabb')
        self.assertEqual(uncertain['reason'], 'ownership_boundary_uncertain')

    def test_crop_fragment_center_never_redefines_ownership(self):
        r = ownership_of_box(Box(.05, .05, .2, .2), (1536, 1152), point_source='crop_fragment')
        self.assertEqual(r['status'], 'unresolved')
        self.assertIsNone(r['core'])

    def test_one_image_sixty_four_questions_each_explicitly_scopes_ownership(self):
        plan = core_plan(1536, 1152)
        doc = plan['document']
        self.assertFalse(plan['execution_enabled'])
        self.assertFalse(plan['provider_limits_verified'])
        self.assertEqual(len(doc['assets']), 1)
        self.assertEqual(set(doc['state']), {'photo'})
        self.assertEqual(len(doc['questions']), 64)
        self.assertEqual(plan['counts'], {'planned_http_requests': 1, 'questions_per_request': 64,
                                         'ownership_regions': 16, 'actual_http_requests': 0})
        for c in CORES:
            for suffix in ('present', 'count', 'box', 'resolved'):
                instructions = doc['questions'][c.key+'_'+suffix]['instructions']
                self.assertIn(f'{c.box.left:.4f} <= center_x < {c.box.right:.4f}', instructions)
                self.assertIn(f'{c.box.top:.4f} <= center_y < {c.box.bottom:.4f}', instructions)
                self.assertIn('Never clip the AABB', instructions)
                self.assertIn('Observation context', instructions)
                self.assertIn('ORIGINAL full image', instructions)

    def test_all_empty_contract_discards_boxes_without_http_or_environment_reads(self):
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('No network')), \
             patch('os.environ.get', side_effect=AssertionError('No environment reads')):
            result = evaluate_core_response(core_plan(1536, 1152), empty_response())
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['detections'], [])
        self.assertEqual(result['counts']['http_requests'], 0)
        self.assertEqual(result['counts']['answers_checked'], 64)

    def test_disjoint_owned_cards_do_not_change_boxes_or_confidences(self):
        response = empty_response()
        put_one(response, 'r0c0', Box(.02, .02, .20, .20), .7)
        put_one(response, 'r3c3', Box(.8, .8, .98, .98), .99)
        result = evaluate_core_response(core_plan(1536, 1152), response)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(result['detections']), 2)
        self.assertEqual([d['confidence'] for d in result['detections']], [.7, .99])

    def test_out_of_core_box_stops_next_request_instead_of_reassigning(self):
        response = empty_response()
        put_one(response, 'r0c0', Box(.55, .02, .7, .20))
        result = evaluate_core_response(core_plan(1536, 1152), response)
        self.assertTrue(result['stop_before_next_request'])
        self.assertEqual(result['ownership_contract_violations'], ['r0c0'])
        self.assertEqual(result['detections'], [])
        self.assertEqual(result['candidates'][0]['core'], 'r0c0')

    def test_uncertain_and_null_never_become_empty_or_complete(self):
        for kind in ('uncertain', 'null', 'boundary'):
            response = empty_response()
            put_one(response, 'r0c0', Box(.02, .02, .2, .2))
            if kind == 'uncertain': response['answers']['r0c0_resolved']['noul'] = .5
            if kind == 'null': response['answers']['r0c0_box']['confidence'] = None
            if kind == 'boundary': response['answers']['r0c0_box']['box'] = Box(.2, .02, .3, .2).json()
            result = evaluate_core_response(core_plan(1536, 1152), response)
            self.assertEqual(result['status'], 'incomplete')
            self.assertTrue(result['stop_before_next_request'])

    def test_center_collision_and_containment_keep_all_candidates_unresolved(self):
        response = empty_response()
        put_one(response, 'r0c0', Box(.02, .02, .20, .20), .7)
        put_one(response, 'r0c1', Box(.05, .05, .17, .17), .99)
        result = evaluate_core_response(core_plan(1536, 1152), response)
        self.assertEqual(len(result['candidates']), 2)
        self.assertEqual(result['detections'], [])
        self.assertEqual([d['confidence'] for d in result['candidates']], [.7, .99])
        self.assertTrue(all(d['status'] == 'unresolved' for d in result['candidates']))

    def test_multiple_cards_owned_by_one_core_are_unresolved_not_one_box(self):
        response = empty_response()
        response['answers']['r0c0_present']['noul'] = 1
        response['answers']['r0c0_count'].update(choice='several', probabilities={'none': 0, 'one': 0, 'several': 1})
        result = evaluate_core_response(core_plan(1536, 1152), response)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['detections'], [])

    def test_inconsistent_question_answers_stop_and_missing_answers_error(self):
        response = empty_response()
        response['answers']['r0c0_present']['noul'] = 1
        self.assertEqual(evaluate_core_response(core_plan(1536, 1152), response)['status'], 'incomplete')
        del response['answers']['r0c0_count']
        result = evaluate_core_response(core_plan(1536, 1152), response)
        self.assertEqual(result['status'], 'error')
        self.assertTrue(result['stop_before_next_request'])


if __name__ == '__main__':
    unittest.main()
