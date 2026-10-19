"""A separate greedy search hypothesis; source images and boxes stay unchanged."""
from dataclasses import dataclass
import math
import time
from PIL import Image, ImageDraw
from .geometry import Box, probability, iou, COORDINATES
from .provider import MODEL, ProviderError, read_usage, PRICE_PER_MILLION_INPUT


@dataclass(frozen=True)
class MaskSettings:
    max_rounds: int = 16
    presence_yes: float = .8
    presence_no: float = .2
    single_card_yes: float = .9
    box_confidence: float = .6
    cross_view_iou: float = .75
    minimum_unmasked_fraction: float = .5
    duplicate_iou: float = .85
    mask_margin_pixels: int = 4

    def __post_init__(self):
        if type(self.max_rounds) is not int or not 1 <= self.max_rounds <= 16:
            raise ValueError('max_rounds must be 1..16')
        for name in ('presence_yes','presence_no','single_card_yes','box_confidence',
                     'cross_view_iou','minimum_unmasked_fraction','duplicate_iou'):
            probability(getattr(self,name))
        if self.presence_no >= self.presence_yes:
            raise ValueError('Invalid presence thresholds')
        if type(self.mask_margin_pixels) is not int or not 0 <= self.mask_margin_pixels <= 8:
            raise ValueError('Invalid mask margin')


def masked_document(unused, reference):
    target='Visible printed Magic: The Gathering cards in the photo. Ignore uniform gray masked rectangles and empty sleeve borders.'
    return {'model':MODEL,'state':{'photo':reference},'assets':{reference:{'type':'image','part':'photo'}},
            'questions':{'present':{'type':'noul','instructions':target+' Is at least one such card still visible?'},
                         'card':{'type':'box','instructions':'The leftmost remaining Magic: The Gathering card with visible printing. Ignore uniform gray masked regions. Locate only its visible printed rectangle.'}}}


def local_document(unused, reference):
    return {'model':MODEL,'state':{'photo':reference},'assets':{reference:{'type':'image','part':'photo'}},
            'questions':{'present':{'type':'noul','instructions':'Is a Magic: The Gathering card visibly present?'},
                         'single':{'type':'noul','instructions':'Does this crop primarily show one identifiable Magic: The Gathering card? Ignore tiny neighboring edge fragments.'},
                         'card':{'type':'box','instructions':'The Magic: The Gathering card nearest the center. Locate its visible printed rectangle, excluding sleeve and hidden parts.'}}}


def masked_ranked_document(unused, reference):
    from .provider import request_document
    document=request_document([],reference)
    document['questions']['present']=document['questions'].pop('remaining')
    for q in document['questions'].values():
        q['instructions'] += ' Ignore the uniform gray masked rectangles; locate exactly one remaining physical card, never a group.'
    return document


def render_search(original, boxes, margin=4):
    image=original.copy()
    mask=Image.new('L',original.size,0)
    draw,mdraw=ImageDraw.Draw(image),ImageDraw.Draw(mask)
    for box in boxes:
        l,t,r,b=box.pixels(*original.size)
        extent=(math.floor(l-margin),math.floor(t-margin),math.ceil(r+margin),math.ceil(b+margin))
        draw.rectangle(extent,fill=(127,127,127));mdraw.rectangle(extent,fill=255)
    return image,mask


def unmasked_fraction(box,mask):
    l,t,r,b=box.pixels(*mask.size)
    region=mask.crop((max(0,math.floor(l)),max(0,math.floor(t)),min(mask.width,math.ceil(r)),min(mask.height,math.ceil(b))))
    area=region.width*region.height
    return (region.histogram()[0]/area) if area else 0


def adoption_reasons(presence,single,confidence,agreement,new_fraction,boundaries,settings=MaskSettings()):
    for value in (presence,single,agreement,new_fraction):probability(value)
    if confidence is not None:probability(confidence)
    reasons=[]
    if presence<settings.presence_yes:reasons.append('local_presence_uncertain')
    if single<settings.single_card_yes:reasons.append('single_card_not_verified')
    if boundaries.get('artificial_crop') or boundaries.get('padding'):reasons.append('crop_extent_unresolved')
    if new_fraction<settings.minimum_unmasked_fraction:reasons.append('insufficient_new_unmasked_content')
    # Independent observations must agree when confidence alone is insufficient.
    if (confidence is None or confidence<settings.box_confidence) and agreement<settings.cross_view_iou:
        reasons.append('neither_confidence_nor_cross_view_agreement')
    return reasons


def detect_masked(prepared,client,settings=MaskSettings(),recenter=False,progress=None):
    """Experimental v1/v2, both incomplete even when the model reports none.

    v2 changes the search caption and permits up to three original-image crops.
    Cross-crop agreement is evidence of stability, not independent correctness.
    """
    from .images import prepare,png_bytes
    from .refine import proposal_crop
    from .partition import classify_boundaries,to_global
    started,initial=time.perf_counter(),client.calls
    candidates,adopted=[],[]
    usage={'input_tokens':0,'output_tokens':0}
    status,stop='incomplete','round_cap_not_exhaustive'

    def ask(tile,factory,nq):
        r=client._ask(tile,[],factory,nq)
        u=read_usage(r)
        for k in usage:usage[k]+=u[k]
        if r['model']!=(MODEL if client.mode=='live' else 'mock-script'):
            raise ValueError('Unexpected model')
        return r['answers']

    def noul(a,key):
        if a[key]['type']!='noul':raise ValueError('Unexpected Noul type')
        return probability(a[key]['noul'])

    def read_box(a):
        if a['card']['type']!='box':raise ValueError('Unexpected Box type')
        conf=a['card']['confidence']
        return Box.read(a['card']['box']),None if conf is None else probability(conf)

    try:
        for ordinal in range(settings.max_rounds):
            search,mask=render_search(prepared.original,adopted,settings.mask_margin_pixels)
            tile=prepare(png_bytes(search))
            a=ask(tile,masked_ranked_document if recenter else masked_document,2)
            presence=noul(a,'present')
            c={'round':ordinal,'search_presence':presence,'status':'unresolved','reasons':[],'local_attempts':[]}
            candidates.append(c)
            if presence<=settings.presence_no:
                c.update(status='rejected',reasons=['model_reports_no_remaining_card'])
                stop='model_reports_none_not_proof_of_exhaustiveness';break
            if presence<settings.presence_yes:
                c['reasons'].append('remaining_presence_uncertain');stop='remaining_presence_uncertain';break
            raw,conf=read_box(a);proposal=tile.to_original(raw)
            c.update(proposal_box=proposal.json(),search_confidence=conf)
            anchor=proposal
            for observation in range(3 if recenter else 1):
                for margin in (.15,.5):
                    tile,region=proposal_crop(prepared.original,anchor,margin)
                    a=ask(tile,local_document,3)
                    local_presence,single=noul(a,'present'),noul(a,'single')
                    raw,confidence=read_box(a);local=tile.to_original(raw)
                    refined=to_global(local,region,prepared.original.size)
                    boundaries=classify_boundaries(local,raw,tile,region,prepared.original.size)
                    agreement,fraction=iou(anchor,refined),unmasked_fraction(refined,mask)
                    reasons=adoption_reasons(local_presence,single,confidence,agreement,fraction,boundaries,settings)
                    c['local_attempts'].append({'recenter':observation,'anchor_box':anchor.json(),
                        'margin':margin,'region_pixels':list(region),'presence':local_presence,
                        'single':single,'box':refined.json(),'confidence':confidence,
                        'boundaries':boundaries,'cross_view_iou':agreement,'reasons':list(reasons)})
                    if not (boundaries['artificial_crop'] or boundaries['padding']):break
                if not reasons or local_presence<settings.presence_yes or fraction<settings.minimum_unmasked_fraction:break
                anchor=refined
            duplicate=[i for i,b in enumerate(adopted) if iou(refined,b)>=settings.duplicate_iou]
            if duplicate:reasons.append('repeat_prediction_identity_unresolved')
            c.update(box=refined.json(),confidence=confidence,presence=local_presence,single=single,
                cross_view_iou=agreement,new_unmasked_fraction=fraction,boundaries=boundaries,
                reasons=reasons,overlapping_prior_ids=duplicate)
            if reasons:stop='local_evidence_unresolved';break
            c['status']='accepted';adopted.append(refined)
            if progress:progress({'round':ordinal+1,'accepted':len(adopted),'http_calls':client.calls-initial})
    except (ProviderError,ValueError,KeyError,TypeError):
        status,stop='error','provider_or_answer_error_no_retry'
    return {'schema_version':1,'mode':client.mode,'experiment':'masked_search_v2' if recenter else 'masked_search_v1',
        'evidence':'live_exploratory_development_fit' if client.mode=='live' else 'mock_replay',
        'coordinate_system':COORDINATES,'image':{'width':prepared.original.width,'height':prepared.original.height},
        'candidates':candidates,'detections':[{'id':f'card-{i+1}','box':c['box'],'confidence':c['confidence']}
            for i,c in enumerate(x for x in candidates if x['status']=='accepted')],
        'status':status,'stop_reason':stop,'api_calls':client.calls-initial,'usage':usage,
        'estimated_cost_usd':usage['input_tokens']*PRICE_PER_MILLION_INPUT/1e6,
        'pipeline_elapsed_ms':round((time.perf_counter()-started)*1000,3)}
