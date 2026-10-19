import unittest
from PIL import Image
from mtg_jev.geometry import Box
from mtg_jev.masked_search import render_search,unmasked_fraction,adoption_reasons,masked_document,local_document,detect_masked,MaskSettings
from mtg_jev.images import prepare,png_bytes
from mtg_jev.refine import proposal_crop


class MaskedSearchTests(unittest.TestCase):
    def test_mask_uses_only_supplied_predictions_and_preserves_original(self):
        original=Image.new('RGB',(800,600),'white');b=Box(.1,.1,.3,.4)
        image,mask=render_search(original,[b],4)
        self.assertEqual(original.getpixel((100,100)),(255,255,255))
        self.assertEqual(image.getpixel((100,100)),(127,127,127))
        self.assertEqual(unmasked_fraction(b,mask),0)
        self.assertEqual(unmasked_fraction(Box(.6,.6,.9,.9),mask),1)

    def test_low_confidence_can_have_independent_geometric_support_without_score_promotion(self):
        self.assertEqual(adoption_reasons(.99,.95,.2,.8,1,{}),[])
        self.assertIn('neither_confidence_nor_cross_view_agreement',adoption_reasons(.99,.95,.2,.5,1,{}))
        self.assertIn('single_card_not_verified',adoption_reasons(.99,.5,.99,.99,1,{}))

    def test_original_edges_allowed_but_crop_edges_and_repeats_stay_unresolved(self):
        self.assertEqual(adoption_reasons(.99,.95,.8,.9,1,{'original_image':['right']}),[])
        self.assertIn('crop_extent_unresolved',adoption_reasons(.99,.95,.8,.9,1,{'artificial_crop':['right']}))
        self.assertIn('insufficient_new_unmasked_content',adoption_reasons(.99,.95,.8,.9,.1,{}))

    def test_queries_do_not_receive_known_count_or_prior_truth_boxes(self):
        for d in (masked_document([], 'opaque'),local_document([], 'opaque')):
            self.assertEqual(set(d['state']),{'photo'})
            self.assertEqual(len(d['assets']),1)
        self.assertEqual(len(masked_document([], 'opaque')['questions']),2)
        self.assertEqual(len(local_document([], 'opaque')['questions']),3)

    def test_pipeline_preserves_low_score_with_supported_geometry_and_never_claims_exhaustiveness(self):
        source=prepare(png_bytes(Image.new('RGB',(800,600),'white')))
        target=Box(.1,.1,.3,.4)
        tile,region=proposal_crop(source.original,target)
        l,t,r,b=target.pixels(*source.original.size)
        local=Box((l-region[0])/tile.original.width,(t-region[1])/tile.original.height,
                  (r-region[0])/tile.original.width,(b-region[1])/tile.original.height)
        def response(p,box=None,single=None):
            a={'present':{'type':'noul','noul':p}}
            if box:a['card']={'type':'box','box':box.json(),'confidence':.2}
            if single is not None:a['single']={'type':'noul','noul':single}
            return {'model':'mock-script','usage':{'input_tokens':0,'output_tokens':0},'answers':a}
        class Replay:
            mode='mock';calls=0
            def _ask(self,*args):
                r=self.responses[self.calls];self.calls+=1;return r
        client=Replay();client.responses=[response(.99,target),response(.99,tile.to_prepared(local),.95),response(.01)]
        result=detect_masked(source,client)
        self.assertEqual(result['api_calls'],3)
        self.assertEqual(len(result['detections']),1)
        self.assertEqual(result['detections'][0]['confidence'],.2)
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(result['stop_reason'],'model_reports_none_not_proof_of_exhaustiveness')
        self.assertEqual(source.original.getpixel((100,100)),(255,255,255))

    def test_invalid_policy_is_rejected_before_inference(self):
        for kwargs in ({'max_rounds':17},{'presence_no':.9},{'mask_margin_pixels':-1}):
            with self.assertRaises(ValueError):MaskSettings(**kwargs)


if __name__=='__main__':unittest.main()
