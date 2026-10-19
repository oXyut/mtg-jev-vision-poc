"""Count from pixels, then fan out distinct ordinal Box questions in one HTTP.

Only image and generic settings enter inference. Count is a model prediction,
never ground truth. Independent slot answers do not enforce a permutation.
"""

import time
from dataclasses import asdict, dataclass

from .geometry import COORDINATES, Box, iou, probability
from .provider import MODEL, PRICE_PER_MILLION_INPUT, ProviderError, read_usage


@dataclass(frozen=True)
class BatchSettings:
    capacity: int = 16
    count_confidence: float = .8
    presence_yes: float = .8
    presence_no: float = .2
    min_box_confidence: float = .6
    duplicate_iou: float = .85

    def __post_init__(self):
        validate_capacity(self.capacity)
        for k in ('count_confidence', 'presence_yes', 'presence_no', 'min_box_confidence', 'duplicate_iou'):
            probability(getattr(self, k), k)
        if self.presence_no >= self.presence_yes or min(self.count_confidence, self.min_box_confidence, self.duplicate_iou) <= 0:
            raise ValueError('Invalid thresholds')


def validate_capacity(capacity):
    if type(capacity) is not int or not 1 <= capacity <= 32:
        raise ValueError('Capacity must be 1..32')


def image_document(reference, questions):
    return {'model': MODEL, 'state': {'photo': reference},
            'assets': {reference: {'type': 'image', 'part': 'photo'}}, 'questions': questions}


def count_document(reference, capacity=16):
    validate_capacity(capacity)
    target = ('Distinct physical Magic: The Gathering cards visibly present in the full photo. '
              'Count each card once, including rotated and partially occluded cards. '
              'A visible fragment can belong to one card; do not count dice, sleeves, '
              'background, or different parts of the same card as separate cards. ')
    return image_document(reference, {
        'present': {'type': 'noul', 'instructions': target+'Is any such card visibly present?'},
        'count': {'type': 'choice', 'instructions': target+'How many such cards are present?',
                  'criteria': {**{str(n): f'Exactly {n} distinct cards' for n in range(capacity+1)},
                               'over_capacity': f'More than {capacity} distinct cards'}},
    })


def ranked_document(reference, count):
    validate_capacity(count)
    questions = {}
    for rank in range(1, count+1):
        target = (
            'Consider every distinct visible Magic: The Gathering card in the ORIGINAL full photo, '
            'including rotated and partly occluded cards. For each card use the tight axis-aligned '
            'rectangle around ALL its visible printed-card fragments, excluding sleeves, dice, '
            'and background. Do not infer hidden edges or enclose multiple cards together. '
            'Sort the individual cards by that rectangle center x from left to right; '
            'break equal center x by center y from top to bottom. '
            f'The target of THIS question is ONLY the card at 1-based rank {rank} in that order. ')
        questions[f'slot_{rank:02d}_present'] = {'type': 'noul', 'instructions': target+
                                               'Does this ranked individual card exist in the photo?'}
        questions[f'slot_{rank:02d}_box'] = {'type': 'box', 'instructions': target+
                                           'Locate ONLY that individual card. Return its visible printed-card '
                                           'rectangle in full-image normalized coordinates.'}
    return image_document(reference, questions)


def short_ranked_document(reference, capacity=16):
    """Separate speculative experiment. Existence gates every generic slot."""
    validate_capacity(capacity)
    questions = {}
    for rank in range(1, capacity+1):
        questions[f'slot_{rank:02d}_present'] = {
            'type': 'noul', 'instructions': f'Are at least {rank} distinct Magic: The Gathering cards visible in the photo, including partly occluded cards?'}
        questions[f'slot_{rank:02d}_box'] = {
            'type': 'box', 'instructions': f'The Magic: The Gathering card at position {rank}, counting the cards from left to right. Include rotated and partly occluded cards. Locate only this individual card.'}
    return image_document(reference, questions)


def read_count(response, settings):
    a = response['answers']
    if set(a) != {'present', 'count'} or a['present']['type'] != 'noul' or a['count']['type'] != 'choice':
        raise ValueError('Invalid count answers')
    presence = probability(a['present']['noul'])
    count = a['count']
    confidence = probability(count['confidence'])
    probabilities = count['probabilities']
    labels = {str(n) for n in range(settings.capacity+1)} | {'over_capacity'}
    label = count['choice']
    if set(probabilities) != labels or label not in labels:
        raise ValueError('Invalid count labels')
    for value in probabilities.values(): probability(value)
    if abs(sum(probabilities.values())-1) > .02 or probabilities[label]+.001 < max(probabilities.values()):
        raise ValueError('Invalid count probabilities')
    observation = {'choice': label, 'confidence': confidence, 'probabilities': probabilities, 'presence': presence}
    if confidence < settings.count_confidence:
        return None, 'uncertain_count', observation
    if label == 'over_capacity':
        return None, 'count_exceeds_capacity', observation
    n = int(label)
    if (n == 0 and presence > settings.presence_no) or (n > 0 and presence < settings.presence_yes):
        return None, 'count_presence_inconsistent', observation
    return n, None, observation


def read_slots(response, count, prepared, settings):
    answers = response['answers']
    expected = set(ranked_document('unused', count)['questions'])
    if set(answers) != expected:
        raise ValueError('Invalid batch answer keys')
    candidates = []
    for rank in range(1, count+1):
        present, answer = answers[f'slot_{rank:02d}_present'], answers[f'slot_{rank:02d}_box']
        if present['type'] != 'noul': raise ValueError('Invalid presence type')
        p = probability(present['noul'])
        c = {'slot': rank, 'presence': p, 'box': None, 'confidence': None, 'reasons': []}
        if p <= settings.presence_no:
            c.update(status='rejected', reasons=['ranked_target_absent'])
        else:
            if answer['type'] != 'box': raise ValueError('Invalid box type')
            box = prepared.to_original(Box.read(answer['box']))
            confidence = None if answer['confidence'] is None else probability(answer['confidence'])
            c.update(box=box.json(), confidence=confidence)
            if p < settings.presence_yes: c['reasons'].append('uncertain_presence')
            if confidence is None or confidence < settings.min_box_confidence: c['reasons'].append('unverified_box')
            c['status'] = 'unresolved' if c['reasons'] else 'accepted'
        candidates.append(c)
    duplicates, order_violations = [], []
    for i, a in enumerate(candidates):
        if a['box'] is None: continue
        aa = Box.read(a['box'])
        for b in candidates[i+1:]:
            if b['box'] is None: continue
            bb = Box.read(b['box'])
            overlap = iou(aa, bb)
            if overlap >= settings.duplicate_iou:
                duplicates.append({'slots': [a['slot'], b['slot']], 'iou': overlap})
                for c in (a, b):
                    c['status'] = 'unresolved'
                    if 'possible_duplicate_identity' not in c['reasons']: c['reasons'].append('possible_duplicate_identity')
            ax, ay = (aa.left+aa.right)/2, (aa.top+aa.bottom)/2
            bx, by = (bb.left+bb.right)/2, (bb.top+bb.bottom)/2
            if ax > bx+2/prepared.original.width or (abs(ax-bx) <= 2/prepared.original.width and ay > by+2/prepared.original.height):
                order_violations.append([a['slot'], b['slot']])
    # Rank consistency is reported separately. Unique, confident geometric
    # predictions stay predictions; we do not relabel/sort them to fit truth.
    detections = [{'id': f'card-{c["slot"]}', 'slot': c['slot'], 'box': c['box'],
                   'confidence': c['confidence'], 'presence': c['presence'],
                   'identification': {'status': 'unknown', 'candidates': [], 'reason': 'not_run'}}
                  for c in candidates if c['status'] == 'accepted']
    return candidates, detections, duplicates, order_violations


def detect_batch(prepared, client, settings=BatchSettings()):
    start, initial_calls = time.perf_counter(), client.calls
    usage, count_observation = {'input_tokens': 0, 'output_tokens': 0}, None
    candidates, detections, duplicates, order_violations = [], [], [], []
    estimated_count, question_count = None, 0
    status, stop = 'incomplete', 'count_not_resolved'
    def consume(response):
        tokens = read_usage(response)
        for k in usage: usage[k] += tokens[k]
        if response.get('model') != (MODEL if client.mode == 'live' else 'mock-script'):
            raise ValueError('Unexpected resolved model')
    try:
        question_count += 2
        response = client.ask_count(prepared, settings.capacity)
        consume(response)
        estimated_count, reason, count_observation = read_count(response, settings)
        if reason:
            stop = reason
        elif estimated_count == 0:
            status, stop = 'complete', 'model_count_zero'
        else:
            question_count += 2*estimated_count
            response = client.ask_ranked(prepared, estimated_count)
            consume(response)
            candidates, detections, duplicates, order_violations = read_slots(response, estimated_count, prepared, settings)
            status = 'complete' if len(detections) == estimated_count and not order_violations else 'incomplete'
            stop = ('rank_order_inconsistent' if order_violations else
                    'unresolved_rank_slots' if status == 'incomplete' else 'all_rank_slots_resolved')
    except ProviderError as exc:
        status, stop = 'error', str(exc)
    except (ValueError, KeyError, TypeError, AttributeError):
        status, stop = 'error', 'invalid_provider_response'
    calls = client.calls-initial_calls
    return {'schema_version': 1, 'mode': client.mode, 'experiment': 'count_then_ranked_box_batch_v1',
            'evidence': 'live_exploratory' if client.mode == 'live' else 'mock_replay_not_vision',
            'coordinate_system': COORDINATES, 'image': {'width': prepared.original.width, 'height': prepared.original.height},
            'preprocessing': prepared.metadata(), 'settings': asdict(settings), 'requested_model': MODEL,
            'estimated_count': estimated_count, 'count_observation': count_observation,
            'candidates': candidates, 'detections': detections, 'possible_duplicate_pairs': duplicates,
            'rank_order_violations': order_violations, 'status': status, 'stop_reason': stop,
            'api_calls': calls if client.mode == 'live' else 0, 'provider_calls': calls, 'question_count': question_count,
            'usage': usage, 'estimated_cost_usd': usage['input_tokens']*PRICE_PER_MILLION_INPUT/1e6,
            'pipeline_elapsed_ms': round((time.perf_counter()-start)*1000, 3),
            'warning': 'A model count and independent ordinal boxes do not guarantee exhaustive unique detection'}
