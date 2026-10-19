"""Experimental count-gated spatial subdivision, independent of baseline detection.

Only image dimensions and model answers guide exploration. One crop is sent per
request, so Box coordinates always refer to that request's single image.
"""

import math
import time
from collections import deque
from dataclasses import asdict, dataclass

from .geometry import COORDINATES, Box, iou, probability
from .images import png_bytes, prepare
from .provider import MODEL, PRICE_PER_MILLION_INPUT, ProviderError, read_usage


@dataclass(frozen=True)
class PartitionSettings:
    max_requests: int = 13
    max_depth: int = 4
    overlap: float = .25
    count_confidence: float = .8
    presence_yes: float = .8
    presence_no: float = .2
    min_box_confidence: float = .6
    merge_iou: float = .65
    merge_containment: float = .9

    def __post_init__(self):
        if type(self.max_requests) is not int or not 1 <= self.max_requests <= 30:
            raise ValueError('max_requests must be 1..30')
        if type(self.max_depth) is not int or not 0 <= self.max_depth <= 6:
            raise ValueError('max_depth must be 0..6')
        for k in ('count_confidence', 'min_box_confidence', 'merge_iou', 'merge_containment'):
            if probability(getattr(self, k), k) <= 0:
                raise ValueError('Threshold must be positive')
        probability(self.presence_yes)
        probability(self.presence_no)
        if self.presence_no >= self.presence_yes:
            raise ValueError('Invalid presence thresholds')
        if not 0 <= self.overlap < .5:
            raise ValueError('overlap must be in [0, .5)')


def split_region(region, overlap):
    """Integer pixel regions, divided along their long axis with fixed overlap."""
    left, top, right, bottom = region
    axis = 0 if right - left >= bottom - top else 1
    lo, hi = region[axis], region[axis + 2]
    first, second = list(region), list(region)
    first[axis + 2] = math.ceil(lo + (hi - lo) * (1 + overlap) / 2)
    second[axis] = math.floor(lo + (hi - lo) * (1 - overlap) / 2)
    if first == list(region) or second == list(region):
        raise ValueError('Region cannot be subdivided')
    return tuple(first), tuple(second)


def to_global(box, region, size):
    left, top, right, bottom = region
    width, height = size
    return Box((left + box.left * (right - left)) / width,
               (top + box.top * (bottom - top)) / height,
               (left + box.right * (right - left)) / width,
               (top + box.bottom * (bottom - top)) / height)


def containment(a, b):
    intersection = max(0, min(a.right, b.right) - max(a.left, b.left)) * max(
        0, min(a.bottom, b.bottom) - max(a.top, b.top))
    return intersection / min((a.right - a.left) * (a.bottom - a.top),
                              (b.right - b.left) * (b.bottom - b.top))


def merge_candidates(candidates, settings):
    """Union overlapping duplicates. May merge distinct occluded cards: evaluate FP/FN."""
    groups = []
    for candidate in sorted(candidates, key=lambda c: -c['confidence']):
        box = Box.read(candidate['box'])
        matches = [g for g in groups if any(
            iou(box, Box.read(m['box'])) >= settings.merge_iou or
            containment(box, Box.read(m['box'])) >= settings.merge_containment
            for m in g)]
        if matches:
            members = [candidate]
            for g in matches:
                members.extend(g)
                groups.remove(g)
            groups.append(members)
        else:
            groups.append([candidate])
    detections = []
    for group in groups:
        boxes = [Box.read(m['box']) for m in group]
        box = Box(min(b.left for b in boxes), min(b.top for b in boxes),
                  max(b.right for b in boxes), max(b.bottom for b in boxes))
        detections.append({'id': f'card-{len(detections)+1}', 'box': box.json(),
                           'confidence': max(m['confidence'] for m in group),
                           'members': [m['region_id'] for m in group],
                           'identification': {'status': 'unknown', 'candidates': [],
                                              'reason': 'identification_not_run'}})
    return detections


def detect_partition(prepared, client, settings=PartitionSettings()):
    started, initial_calls = time.perf_counter(), client.calls
    queue = deque([('root', (0, 0, *prepared.original.size), 0)])
    candidates, trace, unresolved = [], [], []
    usage = {'input_tokens': 0, 'output_tokens': 0}
    status, stop = 'complete', 'all_regions_resolved'
    try:
        while queue and client.calls - initial_calls < settings.max_requests:
            region_id, region, depth = queue.popleft()
            tile = prepare(png_bytes(prepared.original.crop(region)))
            response = client.ask_partition(tile)
            tokens = read_usage(response)
            for k in usage:
                usage[k] += tokens[k]
            if response.get('model') != (MODEL if client.mode == 'live' else 'mock-script'):
                raise ValueError('Unexpected resolved model')
            answers = response['answers']
            if answers['present']['type'] != 'noul':
                raise ValueError('Invalid presence answer')
            presence = probability(answers['present']['noul'])
            count = answers['count']
            label = count['choice']
            confidence = probability(count['confidence'])
            probs = count['probabilities']
            if count['type'] != 'choice' or label not in ('none', 'one', 'several') or set(probs) != {'none', 'one', 'several'}:
                raise ValueError('Invalid count response')
            for v in probs.values():
                probability(v)
            if abs(sum(probs.values()) - 1) > .02 or probs[label] + .001 < max(probs.values()):
                raise ValueError('Invalid count probabilities')
            record = {'region_id': region_id, 'region_pixels': list(region), 'depth': depth,
                      'choice': label, 'choice_confidence': confidence, 'probabilities': probs,
                      'presence': presence}
            trace.append(record)
            if label == 'none' and confidence >= settings.count_confidence and presence <= settings.presence_no:
                record['action'] = 'no_target_discard_box'
                continue
            if label == 'one' and confidence >= settings.count_confidence and presence >= settings.presence_yes:
                answer = answers['card']
                if answer['type'] != 'box':
                    raise ValueError('Invalid box answer')
                # Missing confidence means unverified, never absence.
                bc = None if answer['confidence'] is None else probability(answer['confidence'])
                record['box_confidence'] = bc
                if bc is not None and bc >= settings.min_box_confidence:
                    box = to_global(tile.to_original(Box.read(answer['box'])), region, prepared.original.size)
                    local = tile.to_original(Box.read(answer['box']))
                    boundary = any(v <= .02 or v >= .98 for v in (local.left, local.top, local.right, local.bottom))
                    candidates.append({'box': box.json(), 'confidence': bc,
                                       'region_id': region_id, 'touches_crop_boundary': boundary})
                    record['action'] = 'accept_one'
                    continue
            if depth < settings.max_depth:
                a, b = split_region(region, settings.overlap)
                queue.extend([(region_id+'L', a, depth+1), (region_id+'R', b, depth+1)])
                record['action'] = 'split'
            else:
                record['action'] = 'unresolved_at_depth_limit'
                unresolved.append(region_id)
        if queue or unresolved:
            status = 'incomplete'
            stop = 'request_limit' if queue else 'unresolved_depth_limit'
    except ProviderError as exc:
        status, stop = 'error', str(exc)
    except (ValueError, KeyError, TypeError, AttributeError):
        status, stop = 'error', 'invalid_provider_response'
    detections = merge_candidates(candidates, settings)
    return {'schema_version': 1, 'mode': client.mode, 'experiment': 'count_gated_partition_v1',
            'evidence': 'live_exploratory_not_baseline' if client.mode == 'live' else 'mock_replay_not_vision',
            'coordinate_system': COORDINATES,
            'image': {'width': prepared.original.width, 'height': prepared.original.height},
            'requested_model': MODEL if client.mode == 'live' else None,
            'settings': asdict(settings), 'detections': detections, 'candidates': candidates,
            'status': status, 'stop_reason': stop, 'trace': trace,
            'pending_regions': [x[0] for x in queue], 'unresolved_regions': unresolved,
            'api_calls': client.calls - initial_calls if client.mode == 'live' else 0,
            'usage': usage, 'estimated_cost_usd': usage['input_tokens'] * PRICE_PER_MILLION_INPUT / 1e6,
            'pipeline_elapsed_ms': round((time.perf_counter()-started)*1000, 3),
            'warnings': ['Experimental count gating and overlap merging do not guarantee exhaustive detection']}
