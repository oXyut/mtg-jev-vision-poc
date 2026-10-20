"""All active camera instructions. Edit here; /api/camera/prompts shows these.

Box is one proposal per question. Multi-card enumeration is an application
experiment, not a provider guarantee. Historical static experiments stay separate.
"""
import hashlib
import json

from .provider import MODEL

PROFILE_VERSION = 'camera-v2'
SINGLE_PRESENT = 'Is there a physical Magic: The Gathering card visibly present in photo?'
SINGLE_BOX = 'The visible printed area of the leftmost physical Magic: The Gathering card in photo.'
SEARCH_PRESENT = 'Is any Magic: The Gathering card printing visible outside the gray masked areas in photo?'
SEARCH_BOX = 'The visible printed area of the leftmost Magic: The Gathering card outside the gray masked areas in photo.'
LOCAL_PRESENT = 'Is a Magic: The Gathering card visibly present in this crop?'
LOCAL_SINGLE = 'Does this crop primarily show one identifiable Magic: The Gathering card? Ignore tiny neighboring edge fragments.'
LOCAL_BOX = 'The visible printed rectangle of the Magic: The Gathering card nearest the center of this crop, excluding sleeve and hidden parts.'
ORDINAL_PRESENT = 'Are at least {rank} distinct Magic: The Gathering cards visible in photo, including partly occluded cards?'
ORDINAL_BOX = 'The individual Magic: The Gathering card at position {rank}, counting cards from left to right in photo. Include rotated and partly occluded cards.'


def document(reference, questions):
    return {'model': MODEL, 'state': {'photo': reference},
            'assets': {reference: {'type': 'image', 'part': 'photo'}}, 'questions': questions}


def pair(presence, box, reference):
    return document(reference, {'present': {'type': 'noul', 'instructions': presence},
                                'card': {'type': 'box', 'instructions': box}})


def single_document(unused, reference):
    return pair(SINGLE_PRESENT, SINGLE_BOX, reference)


def search_document(unused, reference):
    # Gray pixels are actual previous model predictions, not an empty exclusion list.
    return pair(SEARCH_PRESENT, SEARCH_BOX, reference)


def crop_document(unused, reference):
    value = pair(LOCAL_PRESENT, LOCAL_BOX, reference)
    value['questions']['single'] = {'type': 'noul', 'instructions': LOCAL_SINGLE}
    return value


def ordinal_document(reference, capacity):
    if type(capacity) is not int or not 1 <= capacity <= 16:
        raise ValueError('Camera capacity must be 1..16')
    questions = {}
    for rank in range(1, capacity+1):
        questions[f'slot_{rank:02d}_present'] = {'type': 'noul', 'instructions': ORDINAL_PRESENT.format(rank=rank)}
        questions[f'slot_{rank:02d}_box'] = {'type': 'box', 'instructions': ORDINAL_BOX.format(rank=rank)}
    return document(reference, questions)


def prompt_profile(method, capacity=16, frame_max_requests=64):
    if method not in ('multi', 'single', 'ordinal', 'ordinal4'):
        raise ValueError('Unknown camera method')
    if type(capacity) is not int or not 1 <= capacity <= 16:
        raise ValueError('Invalid capacity')
    ref = '<camera-frame>'
    if method == 'multi':
        docs = {'masked_search': search_document([], ref), 'original_crop': crop_document([], ref)}
    elif method == 'single':
        docs = {'frame': single_document([], ref)}
    else:
        docs = {'frame': ordinal_document(ref, 4 if method == 'ordinal4' else capacity)}
    profile = {'profile_version': PROFILE_VERSION, 'model': MODEL, 'method': method,
               'max_cards': 1 if method == 'single' else 4 if method == 'ordinal4' else capacity,
               'frame_max_api_calls': 1 if method != 'multi' else frame_max_requests,
               'documents': docs, 'enumeration_guaranteed': False}
    if method == 'multi':
        profile['search_presence_policy'] = 'p<=.20 stops; .20<p<.80 verifies a proposal in original crops; final local adoption thresholds unchanged'
    profile['sha256'] = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
    return profile
