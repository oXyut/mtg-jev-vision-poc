"""Offline-only 4x4 ownership planner and contract checks. No HTTP or key access.

One original image, sixteen non-overlapping half-open cores, four independent
questions per core. Context is an observation aid, never an ownership boundary.
This builds a hypothesis to test; it does not prove visual object identity.
"""

import math
from dataclasses import dataclass

from .geometry import Box, iou, probability
from .provider import MODEL


@dataclass(frozen=True)
class Core:
    row: int
    column: int

    def __post_init__(self):
        if any(type(v) is not int or not 0 <= v < 4 for v in (self.row, self.column)):
            raise ValueError('Core indices must be 0..3')

    @property
    def key(self):
        return f'r{self.row}c{self.column}'

    @property
    def box(self):
        return Box(self.column/4, self.row/4, (self.column+1)/4, (self.row+1)/4)

    @property
    def context(self):
        # A quarter-core halo. Ownership cores themselves do not overlap.
        b = self.box
        return Box(max(0, b.left-1/16), max(0, b.top-1/16),
                   min(1, b.right+1/16), min(1, b.bottom+1/16))


CORES = tuple(Core(r, c) for r in range(4) for c in range(4))


def validate_size(width, height):
    if any(type(v) is not int or not 280 <= v <= 1568 for v in (width, height)):
        raise ValueError('Plan requires metadata-free, oriented image dimensions in 280..1568')


def owner_of_point(x, y):
    """Exact half-open ownership. A point on an internal line goes right/down."""
    probability(x)
    probability(y)
    if x == 1 or y == 1:
        return None
    return CORES[math.floor(y*4)*4 + math.floor(x*4)]


def ownership_of_box(box, size, *, point_source, uncertainty_pixels=2):
    validate_size(*size)
    if (isinstance(uncertainty_pixels, bool) or not isinstance(uncertainty_pixels, (int, float)) or
            not math.isfinite(uncertainty_pixels) or uncertainty_pixels < 0):
        raise ValueError('Invalid center uncertainty')
    if point_source != 'original_visible_aabb':
        return {'status': 'unresolved', 'reason': 'center_not_from_original_visible_aabb', 'core': None}
    x, y = (box.left+box.right)/2, (box.top+box.bottom)/2
    owner = owner_of_point(x, y)
    near_line = any(abs(x-k/4)*size[0] <= uncertainty_pixels or
                    abs(y-k/4)*size[1] <= uncertainty_pixels for k in range(1, 4))
    if uncertainty_pixels > 0 and near_line:
        return {'status': 'unresolved', 'reason': 'ownership_boundary_uncertain', 'core': None}
    return {'status': 'resolved', 'reason': 'half_open_core', 'core': owner.key if owner else None,
            'center': [x, y]}


def core_document(width, height, reference):
    """Construct only JSON. No image loading, credentials, or transport exist here."""
    validate_size(width, height)
    if not isinstance(reference, str) or not reference:
        raise ValueError('An opaque image reference is required')
    questions = {}
    for core in CORES:
        b, context = core.box, core.context
        scope = (
            f'The photo is the ORIGINAL full image ({width}x{height}). '
            f'Ownership core: {b.left:.4f} <= center_x < {b.right:.4f}, '
            f'{b.top:.4f} <= center_y < {b.bottom:.4f}, in full-image normalized coordinates. '
            'Target ONLY physical Magic: The Gathering cards whose ORIGINAL visible-card '
            'AABB center belongs to this half-open core. Define each AABB from ALL visible '
            'printed-card fragments in the ORIGINAL image, excluding blue sleeves, dice, '
            'and background; do not infer hidden edges. Never clip the AABB to this core '
            'or context and never redefine its center from a crop fragment. '
            f'Observation context is x=[{context.left:.4f},{context.right:.4f}], '
            f'y=[{context.top:.4f},{context.bottom:.4f}]; its overlap gives no ownership. '
            'Use the full image when needed to establish the original visible extent. '
            'Unknown extent, center uncertainty within 2 pixels of an internal core line, '
            'or distinct cards with indistinguishable centers make ownership unresolved. ')
        questions[core.key+'_present'] = {'type': 'noul', 'instructions': scope+
                                         'Is at least one of these owned target cards visibly present?'}
        questions[core.key+'_count'] = {'type': 'choice', 'instructions': scope+
                                       'How many of these owned target cards are present?',
                                       'criteria': {'none': 'No owned target card',
                                                    'one': 'Exactly one owned target card',
                                                    'several': 'More than one owned target card'}}
        questions[core.key+'_box'] = {'type': 'box', 'instructions': scope+
                                     'Locate the single owned target card and return its entire '
                                     'original visible-card AABB in FULL-IMAGE coordinates. '
                                     'The application will ignore this box unless the other '
                                     'independent questions establish exactly one unambiguous owner.'}
        questions[core.key+'_resolved'] = {'type': 'noul', 'instructions': scope+
                                          'Can ownership of all target cards for this core '
                                          'be determined unambiguously under these rules, '
                                          'including when there are none?'}
    return {'model': MODEL, 'state': {'photo': reference},
            'assets': {reference: {'type': 'image', 'part': 'photo'}}, 'questions': questions}


def core_plan(width, height, reference='OPAQUE_IMAGE_REFERENCE'):
    return {'experiment': 'fixed_4x4_owned_core_proposal', 'execution_enabled': False,
            'image': {'width': width, 'height': height},
            'document': core_document(width, height, reference),
            'counts': {'planned_http_requests': 1, 'questions_per_request': 64,
                       'ownership_regions': 16, 'actual_http_requests': 0},
            'cores': [{'id': c.key, 'ownership': c.box.json(), 'context': c.context.json()} for c in CORES],
            'provider_limits_verified': False,
            'note': '64-question acceptance, calibration, cost and ownership semantics require an authorized live test'}


def evaluate_core_response(plan, response):
    """Offline interpretation of supplied answers; never executes the plan.

    Counts and ownership are independently checked. All uncertain observations
    remain candidates. Overlap alone never merges identities or promotes scores.
    Any unresolved core stops the proposed two-image experiment before its next
    request. `complete` refers only to these contract checks, not scene accuracy.
    """
    doc = plan['document']
    # Dimensions are explicit plan metadata, never parsed from instructions/truth.
    width, height = plan['image']['width'], plan['image']['height']
    validate_size(width, height)
    size = (width, height)
    candidates, unresolved, violations = [], {}, []
    try:
        answers = response['answers']
        if set(answers) != set(doc['questions']):
            raise ValueError('Question set mismatch')
        for core in CORES:
            key = core.key
            present, resolved = answers[key+'_present'], answers[key+'_resolved']
            count, answer = answers[key+'_count'], answers[key+'_box']
            if present['type'] != 'noul' or resolved['type'] != 'noul' or count['type'] != 'choice':
                raise ValueError('Wrong answer type')
            p, sure, conf = probability(present['noul']), probability(resolved['noul']), probability(count['confidence'])
            label, probs = count['choice'], count['probabilities']
            if label not in ('none', 'one', 'several') or set(probs) != {'none', 'one', 'several'}:
                raise ValueError('Wrong count criteria')
            for v in probs.values():
                probability(v)
            if abs(sum(probs.values())-1) > .02 or probs[label]+.001 < max(probs.values()):
                raise ValueError('Invalid count probabilities')
            if sure < .8:
                unresolved[key] = 'ownership_not_resolved'
                continue
            if label == 'none' and conf >= .8 and p <= .2:
                continue  # Box can be arbitrary and is deliberately ignored.
            if label != 'one' or conf < .8 or p < .8:
                unresolved[key] = 'count_presence_unresolved_or_multiple'
                continue
            if answer['type'] != 'box':
                raise ValueError('Wrong box type')
            if answer['confidence'] is None or probability(answer['confidence']) < .6:
                unresolved[key] = 'box_confidence_unresolved'
                continue
            box = Box.read(answer['box'])
            ownership = ownership_of_box(box, size, point_source='original_visible_aabb')
            candidate = {'core': key, 'box': box.json(), 'confidence': answer['confidence'],
                         'status': 'candidate', 'unresolved_reasons': [], 'ownership': ownership}
            candidates.append(candidate)
            if ownership['status'] != 'resolved':
                unresolved[key] = ownership['reason']
            elif ownership['core'] != key:
                unresolved[key] = 'owned_box_outside_queried_core'
                violations.append(key)
        for i, c in enumerate(candidates):
            b = Box.read(c['box'])
            for other in candidates[i+1:]:
                d = Box.read(other['box'])
                dx = ((b.left+b.right)-(d.left+d.right))/2*width
                dy = ((b.top+b.bottom)-(d.top+d.bottom))/2*height
                reason = ('center_collision_unresolved' if math.hypot(dx, dy) <= 2 else
                          'identity_unverified_overlap' if iou(b, d) > 0 else None)
                if reason:
                    unresolved[c['core']] = unresolved[other['core']] = reason
        for c in candidates:
            if c['core'] in unresolved:
                c['status'] = 'unresolved'
                c['unresolved_reasons'] = [unresolved[c['core']]]
        detections = [c for c in candidates if c['status'] == 'candidate']
        for c in detections:
            c['status'] = 'model_claimed_owner'
        return {'status': 'incomplete' if unresolved else 'complete',
                'evidence': 'offline_contract_check_not_live_recognition',
                'detections': detections, 'candidates': candidates,
                'unresolved_cores': unresolved, 'ownership_contract_violations': violations,
                'stop_before_next_request': bool(unresolved),
                'counts': {'http_requests': 0, 'answers_checked': len(answers),
                           'ownership_regions': 16, 'unresolved_regions': len(unresolved)}}
    except (ValueError, KeyError, TypeError, AttributeError):
        return {'status': 'error', 'reason': 'invalid_supplied_response',
                'evidence': 'offline_contract_check_not_live_recognition',
                'candidates': candidates, 'detections': [], 'unresolved_cores': {c.key: 'invalid_response' for c in CORES},
                'stop_before_next_request': True, 'counts': {'http_requests': 0}}
