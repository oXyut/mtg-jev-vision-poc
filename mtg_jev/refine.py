"""Coarse model boxes are proposals only; obtain a fresh local prediction."""
import base64
import math
import uuid

from .geometry import Box
from .images import png_bytes, prepare
from .provider import MODEL


def proposal_region(box, size, margin=.15):
    if not 0 <= margin <= 1:
        raise ValueError('Invalid margin')
    width,height=size
    l,t,r,b=box.pixels(width,height)
    mx,my=max(8,(r-l)*margin),max(8,(b-t)*margin)
    return (max(0,math.floor(l-mx)),max(0,math.floor(t-my)),
            min(width,math.ceil(r+mx)),min(height,math.ceil(b+my)))


def proposal_crop(original,box,margin=.15):
    region=proposal_region(box,original.size,margin)
    return prepare(png_bytes(original.crop(region))),region


def same_card_document(second,first_reference):
    second_reference=str(uuid.uuid4())
    return {'model':MODEL,'state':{'first_crop':first_reference,'second_crop':second_reference},
            'assets':{first_reference:{'type':'image','part':'photo'},
                      second_reference:{'type':'image','format':'png',
                                        'data_base64':base64.b64encode(png_bytes(second.image)).decode()}},
            'questions':{
                'single_first':{'type':'noul','instructions':
                    'Does first_crop primarily show one identifiable Magic: The Gathering card? Ignore tiny neighboring edge fragments.'},
                'single_second':{'type':'noul','instructions':
                    'Does second_crop primarily show one identifiable Magic: The Gathering card? Ignore tiny neighboring edge fragments.'},
                'same_instance':{'type':'noul','instructions':
                    'These crops come from overlapping locations in the same source photo. Do they show exactly the SAME individual Magic: The Gathering card, matching visible printing and occlusions? Different cards with the same name or artwork do not qualify.'}}}


def confirmed_duplicate_components(candidates,confirmed_pairs):
    """Only a fully confirmed clique can share one unchanged representative.

    No transitive identity, rectangle union, or maximum-confidence promotion.
    All observations remain in the returned records.
    """
    result=[dict(c,reasons=list(c.get('reasons',[])),duplicate_of=None) for c in candidates]
    adjacency={i:set() for i in range(len(result))}
    for i,j in confirmed_pairs:
        adjacency[i].add(j);adjacency[j].add(i)
    seen=set()
    for i in range(len(result)):
        if i in seen:continue
        group,queue=set(),[i]
        while queue:
            j=queue.pop()
            if j in group:continue
            group.add(j);queue.extend(adjacency[j]-group)
        seen.update(group)
        if len(group)<2:continue
        if not all(b in adjacency[a] for a in group for b in group if a!=b):
            for j in group:result[j]['status']='unresolved';result[j].setdefault('reasons',[]).append('identity_confirmation_not_pairwise_complete')
        else:
            representative=min(group)
            for j in group-{representative}:
                result[j]['status']='confirmed_duplicate';result[j]['duplicate_of']=representative
    return result
