import unittest

from mtg_jev import camera_prompts
from mtg_jev.prompt_factorial import BASE, CONDITIONS, ELIGIBILITY, factories, instructions, schedule


class PromptFactorialTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
