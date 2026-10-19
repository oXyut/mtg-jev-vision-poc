import unittest
from PIL import Image
from mtg_jev.context import ContextSettings, context_regions, make_context, project_candidate, detect_context
from mtg_jev.images import prepare,png_bytes
from mtg_jev.geometry import Box


class ContextGeometryTests(unittest.TestCase):
    def test_padding_keeps_anchor_at_center_without_metadata(self):
        image = Image.new('RGB', (1536, 1152), 'white')
        settings = ContextSettings()
        _, core, origin = next(context_regions(image.size, settings))
        self.assertEqual(core, (0, 0, 384, 288))
        self.assertEqual(origin, (-320, -368))
        tile = make_context(image, origin, 1024)
        self.assertEqual(tile.image.size, (1024, 1024))
        self.assertFalse(tile.image.info)
        # Fully visible self-authored rectangle maps back unchanged.
        b, bounds, reason = project_candidate(Box(420/1024, 468/1024, 620/1024, 668/1024), origin, core, image.size, settings)
        self.assertEqual(b, Box(100/1536, 100/1152, 300/1536, 300/1152))
        self.assertIsNone(reason)

    def test_artificial_clipping_never_redefines_owner_center(self):
        settings = ContextSettings()
        b, bounds, reason = project_candidate(Box(0, .2, .5, .8), (100, 100), (100, 100, 600, 600), (1536, 1152), settings)
        self.assertIn('left', bounds['artificial_context'])
        self.assertEqual(reason, 'context_or_padding_boundary_unresolved')

    def test_photo_boundary_and_padding_are_distinct(self):
        settings = ContextSettings()
        b, bounds, reason = project_candidate(Box(.3125, .4, .5, .6), (-320, 0), (0, 0, 384, 1152), (1536, 1152), settings)
        self.assertIn('left', bounds['original_image'])
        self.assertEqual(bounds['padding'], [])
        self.assertEqual(bounds['artificial_context'], [])
        b, bounds, reason = project_candidate(Box(.1, .4, .5, .6), (-320, 0), (0, 0, 384, 1152), (1536, 1152), settings)
        self.assertIn('left', bounds['padding'])
        self.assertEqual(reason, 'context_or_padding_boundary_unresolved')

    def test_selected_other_core_and_uncertain_boundary_are_not_adopted(self):
        settings = ContextSettings()
        _, _, reason = project_candidate(Box(.2, .2, .4, .4), (0, 0), (500, 500, 1000, 1000), (1536, 1152), settings)
        self.assertEqual(reason, 'selected_target_owned_elsewhere')

    def test_full_visible_object_is_owned_once_without_merging_repeated_inputs(self):
        source=prepare(png_bytes(Image.new('RGB',(800,600))))
        settings=ContextSettings(grid=2)
        regions=list(context_regions(source.original.size,settings))
        class Oracle:
            mode='mock'
            calls=0
            def ask_context(self,tile):
                _,core,origin=regions[self.calls];self.calls+=1
                box=Box((160-origin[0])/1024,(120-origin[1])/1024,
                        (320-origin[0])/1024,(240-origin[1])/1024)
                return {'model':'mock-script','usage':{'input_tokens':0,'output_tokens':0},
                        'answers':{'present':{'type':'noul','noul':1},
                                   'card':{'type':'box','box':box.json(),'confidence':1}}}
        result=detect_context(source,Oracle(),settings)
        self.assertEqual(result['visited_cores'],4)
        self.assertEqual(result['api_calls'],0)
        self.assertEqual(len(result['detections']),1)
        self.assertEqual(result['detections'][0]['box'],Box(.2,.2,.4,.4).json())
        self.assertEqual(sum(c['status']=='rejected' for c in result['candidates']),3)
        _, _, reason = project_candidate(Box(300/1024, .1, 468/1024, .3), (0, 0), (0, 0, 384, 288), (1536, 1152), settings)
        self.assertEqual(reason, 'selected_target_owned_elsewhere')


if __name__ == '__main__': unittest.main()
