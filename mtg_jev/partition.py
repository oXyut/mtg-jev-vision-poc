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
    max_regions: int = 127
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
        if type(self.max_regions) is not int or not 1 <= self.max_regions <= 127:
            raise ValueError('max_regions must be 1..127')
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
    """Legacy name: preserve every observation; geometry cannot establish identity.

    No union, suppression, transitive grouping, or confidence promotion occurs.
    Only candidates with neither artificial clipping nor identity ambiguity can
    become detections. Threshold fields remain in historical settings, unused.
    """
    result = []
    for index, candidate in enumerate(candidates):
        box = Box.read(candidate['box'])
        overlaps = [j for j, other in enumerate(candidates) if j != index and
                    iou(box, Box.read(other['box'])) > 0]
        reasons = []
        if candidate.get('boundaries', {}).get('artificial_crop'):
            reasons.append('artificial_crop_boundary_unresolved')
        if candidate.get('boundaries', {}).get('padding'):
            reasons.append('padding_extent_unresolved')
        if overlaps:
            reasons.append('identity_unverified')
        result.append({**candidate, 'id': f'candidate-{index+1}',
                       'status': 'unresolved' if reasons else 'resolved',
                       'unresolved_reasons': reasons,
                       'possible_same_as': [f'candidate-{j+1}' for j in overlaps]})
    return result


def classify_boundaries(local, raw, tile, region, full_size, tolerance_pixels=2):
    """Separate the physical photo edge, artificial crop cuts, and padding."""
    content = tile.to_prepared(Box(0, 0, 1, 1))
    pad_x, pad_y = tile.padding
    boundaries = {'original_image': [], 'artificial_crop': [], 'padding': []}
    w, h = tile.original.size
    for side, edge, limit, tolerance in (
            ('left', local.left, 0, tolerance_pixels/w),
            ('top', local.top, 0, tolerance_pixels/h),
            ('right', local.right, 1, tolerance_pixels/w),
            ('bottom', local.bottom, 1, tolerance_pixels/h)):
        if abs(edge-limit) <= tolerance:
            position = region[('left', 'top', 'right', 'bottom').index(side)]
            outer = 0 if side in ('left', 'top') else full_size[0 if side == 'right' else 1]
            boundaries['original_image' if position == outer else 'artificial_crop'].append(side)
    for side, padding in (('left', pad_x), ('right', pad_x), ('top', pad_y), ('bottom', pad_y)):
        if padding:
            value, valid = getattr(raw, side), getattr(content, side)
            outside = value < valid if side in ('left', 'top') else value > valid
            if outside:
                boundaries['padding'].append(side)
    return boundaries


def detect_partition(prepared, client, settings=PartitionSettings()):
    started, initial_calls = time.perf_counter(), client.calls
    queue = deque([('root', (0, 0, *prepared.original.size), 0)])
    candidates, trace, unresolved = [], [], []
    regions_visited = 0
    usage = {'input_tokens': 0, 'output_tokens': 0}
    status, stop = 'complete', 'all_regions_resolved'
    try:
        while (queue and client.calls - initial_calls < settings.max_requests and
               regions_visited < settings.max_regions):
            region_id, region, depth = queue.popleft()
            regions_visited += 1
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
                    raw_box = Box.read(answer['box'])
                    local = tile.to_original(raw_box)
                    box = to_global(local, region, prepared.original.size)
                    boundaries = classify_boundaries(local, raw_box, tile, region, prepared.original.size)
                    candidates.append({'box': box.json(), 'confidence': bc,
                                       'region_id': region_id, 'boundaries': boundaries})
                    record['action'] = 'retain_one_candidate'
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
            stop = ('region_limit' if regions_visited >= settings.max_regions else 'request_limit') if queue else 'unresolved_depth_limit'
    except ProviderError as exc:
        status, stop = 'error', str(exc)
        if regions_visited:
            unresolved.append(region_id)
    except (ValueError, KeyError, TypeError, AttributeError):
        status, stop = 'error', 'invalid_provider_response'
        if regions_visited:
            unresolved.append(region_id)
    candidates = merge_candidates(candidates, settings)
    unresolved_candidates = [c for c in candidates if c['status'] == 'unresolved']
    unresolved = sorted(set(unresolved + [c['region_id'] for c in unresolved_candidates]))
    if status == 'complete' and unresolved:
        status, stop = 'incomplete', 'unresolved_boundary_or_identity'
    detections = [{'id': f'card-{i+1}', 'box': c['box'], 'confidence': c['confidence'],
                   'source_candidate': c['id'],
                   'identification': {'status': 'unknown', 'candidates': [], 'reason': 'identification_not_run'}}
                  for i, c in enumerate(c for c in candidates if c['status'] == 'resolved')]
    calls = client.calls - initial_calls
    pending_ids = [x[0] for x in queue]
    return {'schema_version': 2, 'mode': client.mode, 'experiment': 'count_gated_partition_boundary_guard_v2',
            'evidence': 'live_exploratory_not_baseline' if client.mode == 'live' else 'mock_replay_not_vision',
            'coordinate_system': COORDINATES,
            'image': {'width': prepared.original.width, 'height': prepared.original.height},
            'requested_model': MODEL if client.mode == 'live' else None,
            'settings': asdict(settings), 'detections': detections, 'candidates': candidates,
            'status': status, 'stop_reason': stop, 'trace': trace,
            'pending_regions': pending_ids, 'unresolved_regions': unresolved,
            'counters': {'provider_calls': calls,
                         'http_requests': calls if client.mode == 'live' else 0,
                         'questions_evaluated': len(trace)*3,
                         'questions_submitted': calls*3 if client.mode == 'live' else 0,
                         'regions_visited': regions_visited, 'regions_evaluated': len(trace),
                         'pending_regions': len(pending_ids),
                         'unresolved_regions': len(set(pending_ids + unresolved)),
                         'unresolved_candidates': len(unresolved_candidates),
                         'full_tree_regions_at_max_depth': 2**(settings.max_depth+1)-1},
            'api_calls': calls if client.mode == 'live' else 0,
            'usage': usage, 'estimated_cost_usd': usage['input_tokens'] * PRICE_PER_MILLION_INPUT / 1e6,
            'pipeline_elapsed_ms': round((time.perf_counter()-started)*1000, 3),
            'warnings': ['Experimental count gating and overlap merging do not guarantee exhaustive detection']}
