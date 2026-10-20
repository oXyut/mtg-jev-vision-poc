import unittest
import importlib.util
from pathlib import Path

from mtg_jev import camera_prompts
from mtg_jev.prompt_factorial import BASE, CONDITIONS, ELIGIBILITY, factories, instructions, schedule


class PromptFactorialTests(unittest.TestCase):
    def experiment(self, filename):
        spec = importlib.util.spec_from_file_location(filename, Path(__file__).resolve().parents[1]/'experiments'/filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_baseline_is_the_actual_current_multi_instructions(self):
        search, crop = factories('mtg_plain')
        self.assertEqual(search([], 'image'), camera_prompts.search_document([], 'image'))
        self.assertEqual(crop([], 'image'), camera_prompts.crop_document([], 'image'))

    def test_only_the_registered_two_factors_differ(self):
        for condition in CONDITIONS:
            document = instructions(condition)
            for key, value in document.items():
                if condition.endswith('_explicit'):
                    self.assertTrue(value.endswith(' '+ELIGIBILITY))
                    value = value[:-len(' '+ELIGIBILITY)]
                if condition.startswith('generic_'):
                    self.assertNotIn('Magic: The Gathering', value)
                    value = value.replace('trading card', 'Magic: The Gathering card')
                self.assertEqual(value, BASE[key])
            search, crop = factories(condition)
            for factory, count in [(search, 2), (crop, 3)]:
                doc = factory([], 'opaque')
                self.assertEqual(set(doc['state']), {'photo'})
                self.assertEqual(len(doc['questions']), count)
                self.assertEqual(len(doc['assets']), 1)

    def test_each_condition_gets_equal_repeats_and_each_order_position(self):
        runs = schedule()
        self.assertEqual(len(runs), 32)
        for scene in ('scene-A', 'scene-B'):
            for condition in CONDITIONS:
                subset = [r for r in runs if r['scene'] == scene and r['condition'] == condition]
                self.assertEqual({r['repeat'] for r in subset}, {1, 2, 3, 4})
                positions = []
                for repeat in range(1, 5):
                    order = [r['condition'] for r in runs if r['scene'] == scene and r['repeat'] == repeat]
                    positions.append(order.index(condition))
                self.assertEqual(set(positions), {0, 1, 2, 3})
        self.assertEqual(schedule(), schedule())

    def test_continuation_keeps_original_cost_call_limits_and_rejects_unknown_or_reordered(self):
        module = self.experiment('run_camera_prompt_factorial.py')
        plan = {'schedule': schedule(1), 'phase_http_cap': 384, 'phase_estimated_usd_cap': .05}
        runs = [{**plan['schedule'][0], 'http': 1}]
        http = [{'status': 'response_with_usage', 'usage': {'input_tokens': 1000, 'output_tokens': 30}}]
        summary = {'pending': 0, 'failed_or_uncertain': 0, 'budget': {'halt_reason': None}}
        calls, budget = module.remaining_plan(plan, runs, http, summary)
        self.assertEqual(calls, 383)
        self.assertAlmostEqual(budget, .05-.000042)
        for bad in [{**summary, 'pending': 1}, {**summary, 'failed_or_uncertain': 1}]:
            with self.assertRaises(ValueError): module.remaining_plan(plan, runs, http, bad)
        with self.assertRaises(ValueError): module.remaining_plan(plan, [{**runs[0], 'condition': 'generic_explicit'}], http, summary)

    def test_raw_duplicate_predictions_are_separate_from_unique_truth_and_other_false_positives(self):
        module = self.experiment('score_camera_prompt_factorial.py')
        from mtg_jev.geometry import COORDINATES, Box
        a, b, wrong = Box(.1,.1,.3,.4).json(), Box(.6,.6,.8,.9).json(), Box(.4,.1,.5,.2).json()
        result = {'coordinate_system': COORDINATES, 'image': {'width': 800, 'height': 600}}
        truth = {**result, 'boxes': [a, b]}
        score = module.evaluate_layer([{'box':a},{'box':a},{'box':wrong}], result, truth, .5)
        self.assertEqual((score['tp'], score['fp'], score['fn']), (1, 2, 1))
        self.assertEqual(score['duplicate_truth_predictions'], 1)
        self.assertEqual(score['geometric_unique_groups'], 2)
        self.assertEqual(score['geometric_duplicate_pairs'], 1)


if __name__ == '__main__':
    unittest.main()
