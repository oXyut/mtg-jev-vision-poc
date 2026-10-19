"""A separate greedy search hypothesis; source images and boxes stay unchanged."""
from dataclasses import dataclass
import math
from PIL import Image, ImageDraw
from .geometry import Box, probability
from .provider import MODEL


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
