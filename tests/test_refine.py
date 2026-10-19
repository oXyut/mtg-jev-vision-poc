import base64
import unittest
from PIL import Image
from mtg_jev.geometry import Box
from mtg_jev.images import png_bytes,prepare
from mtg_jev.refine import proposal_crop,same_card_document,confirmed_duplicate_components


class RefinementTests(unittest.TestCase):
    def test_crop_is_from_proposal_and_api_padding_maps_back(self):
        original=Image.new('RGB',(800,600))
        crop,region=proposal_crop(original,Box(.1,.2,.2,.8))
        self.assertEqual(region,(68,66,172,534))
        self.assertEqual(crop.original.size,(104,468))
        self.assertEqual(crop.image.size,(280,468))
        self.assertFalse(crop.image.info)
        b=Box(.1,.1,.9,.9)
        actual=crop.to_original(crop.to_prepared(b))
        for key,value in b.json().items():self.assertAlmostEqual(actual.json()[key],value)

    def test_two_crop_identity_question_has_one_multipart_and_one_inline_asset(self):
        second=prepare(png_bytes(Image.new('RGB',(400,400))))
        doc=same_card_document(second,'first-ref')
        self.assertEqual(len(doc['assets']),2)
        inline=next(v for k,v in doc['assets'].items() if k!='first-ref')
        self.assertTrue(base64.b64decode(inline['data_base64']).startswith(b'\x89PNG'))
        self.assertEqual(set(doc['questions']),{'single_first','single_second','same_instance'})

    def test_only_pairwise_confirmed_identity_can_choose_unchanged_first(self):
        c=[{'status':'accepted','confidence':.65,'box':Box(.1,.1,.4,.4).json()},
           {'status':'accepted','confidence':.99,'box':Box(.11,.1,.41,.4).json()},
           {'status':'accepted','confidence':.8,'box':Box(.12,.1,.42,.4).json()}]
        output=confirmed_duplicate_components(c,[(0,1),(1,2)])
        self.assertTrue(all(x['status']=='unresolved' for x in output))
        output=confirmed_duplicate_components(c,[(0,1),(0,2),(1,2)])
        self.assertEqual(output[0]['confidence'],.65)
        self.assertEqual(output[0]['box'],c[0]['box'])
        self.assertEqual([x['status'] for x in output],['accepted','confirmed_duplicate','confirmed_duplicate'])
        self.assertEqual([x['status'] for x in c],['accepted']*3)


if __name__=='__main__':unittest.main()
