"""Local context localization with application-side ownership; not global fan-out."""
import math
import time
from dataclasses import asdict, dataclass

from PIL import Image

from .geometry import COORDINATES, Box, iou, probability
from .images import png_bytes, prepare
from .provider import MODEL, PRICE_PER_MILLION_INPUT, ProviderError, read_usage


@dataclass(frozen=True)
class ContextSettings:
    grid: int = 4
    context_size: int = 1024
    presence_yes: float = .8
    presence_no: float = .2
    min_box_confidence: float = .6
    duplicate_iou: float = .85
    boundary_tolerance_pixels: int = 2

    def __post_init__(self):
        if type(self.grid) is not int or not 1 <= self.grid <= 16:
            raise ValueError('Grid must be 1..16')
        if type(self.context_size) is not int or not 280 <= self.context_size <= 1568:
            raise ValueError('Context size must be 280..1568')
        for k in ('presence_yes', 'presence_no', 'min_box_confidence', 'duplicate_iou'):
            probability(getattr(self, k))
        if self.presence_no >= self.presence_yes:
            raise ValueError('Invalid presence thresholds')
        if type(self.boundary_tolerance_pixels) is not int or not 0 <= self.boundary_tolerance_pixels <= 8:
            raise ValueError('Invalid boundary tolerance')


def context_document(unused, reference):
    return {'model': MODEL, 'state': {'photo': reference},
            'assets': {reference: {'type': 'image', 'part': 'photo'}},
            'questions': {
                'present': {'type': 'noul', 'instructions': 'Is any Magic: The Gathering card visibly present in this photo?'},
                'card': {'type': 'box', 'instructions': (
                    'The Magic: The Gathering card nearest to the center of the image. '
                    'Locate only its visible printed card rectangle, excluding the sleeve and hidden parts.')},
            }}


def context_regions(size, settings):
    width, height = size
    for row in range(settings.grid):
        for col in range(settings.grid):
            core = (col*width/settings.grid, row*height/settings.grid,
                    (col+1)*width/settings.grid, (row+1)*height/settings.grid)
            cx, cy = (core[0]+core[2])/2, (core[1]+core[3])/2
            origin = (math.floor(cx-settings.context_size/2), math.floor(cy-settings.context_size/2))
            yield f'r{row}c{col}', core, origin


def make_context(original, origin, side):
    ox, oy = origin
    width, height = original.size
    x0, y0, x1, y1 = max(0, ox), max(0, oy), min(width, ox+side), min(height, oy+side)
    image = Image.new('RGB', (side, side), (32, 36, 44))
    image.paste(original.crop((x0, y0, x1, y1)), (x0-ox, y0-oy))
    return prepare(png_bytes(image))


def project_candidate(box, origin, core, source_size, settings):
    width, height = source_size
    side, tolerance = settings.context_size, settings.boundary_tolerance_pixels
    ox, oy = origin
    local = box.pixels(side, side)
    coords = [local[0]+ox, local[1]+oy, local[2]+ox, local[3]+oy]
    bounds = {'original_image': [], 'artificial_context': [], 'padding': []}
    for i, name in enumerate(('left', 'top', 'right', 'bottom')):
        limit = 0 if i < 2 else width if i == 2 else height
        if abs(coords[i]-limit) <= tolerance:
            bounds['original_image'].append(name)
        if (coords[i] < -tolerance if i < 2 else coords[i] > limit+tolerance):
            bounds['padding'].append(name)
        local_limit = 0 if i < 2 else side
        source_continues = coords[i] > tolerance if i < 2 else coords[i] < limit-tolerance
        if abs(local[i]-local_limit) <= tolerance and source_continues:
            bounds['artificial_context'].append(name)
    clipped = [max(0, min(width if i % 2 == 0 else height, value)) for i, value in enumerate(coords)]
    if clipped[0] >= clipped[2] or clipped[1] >= clipped[3]:
        return None, bounds, 'prediction_in_padding'
    global_box = Box(clipped[0]/width, clipped[1]/height, clipped[2]/width, clipped[3]/height)
    if bounds['padding'] or bounds['artificial_context']:
        # A clipped fragment never supplies a newly invented ownership center.
        return global_box, bounds, 'context_or_padding_boundary_unresolved'
    cx, cy = (clipped[0]+clipped[2])/2, (clipped[1]+clipped[3])/2
    l, t, r, b = core
    if not (l <= cx < r and t <= cy < b):
        return global_box, bounds, 'selected_target_owned_elsewhere'
    if any(abs(cx-line) <= tolerance for line in (l, r) if line not in (0, width)) or any(
            abs(cy-line) <= tolerance for line in (t, b) if line not in (0, height)):
        return global_box, bounds, 'ownership_boundary_uncertain'
    return global_box, bounds, None


def detect_context(prepared, client, settings=ContextSettings(), progress=None):
    start, initial_calls = time.perf_counter(), client.calls
    candidates, usage = [], {'input_tokens': 0, 'output_tokens': 0}
    status, stop = 'incomplete', 'center_proposals_do_not_prove_exhaustiveness'
    try:
        for key, core, origin in context_regions(prepared.original.size, settings):
            tile = make_context(prepared.original, origin, settings.context_size)
            response = client.ask_context(tile)
            u = read_usage(response)
            for k in usage: usage[k] += u[k]
            if response.get('model') != (MODEL if client.mode == 'live' else 'mock-script'):
                raise ValueError('Unexpected model')
            a = response['answers']
            if a['present']['type'] != 'noul': raise ValueError('Wrong presence answer')
            presence = probability(a['present']['noul'])
            c = {'core_id': key, 'core_pixels': list(core), 'context_origin_pixels': list(origin),
                 'presence': presence, 'box': None, 'confidence': None, 'reasons': []}
            if presence <= settings.presence_no:
                c.update(status='rejected', reasons=['no_context_target'])
            else:
                if a['card']['type'] != 'box': raise ValueError('Wrong box type')
                confidence = a['card']['confidence']
                if confidence is not None: confidence = probability(confidence)
                box, bounds, reason = project_candidate(tile.to_original(Box.read(a['card']['box'])), origin,
                                                        core, prepared.original.size, settings)
                c.update(box=box.json() if box else None, confidence=confidence, boundaries=bounds)
                if presence < settings.presence_yes: c['reasons'].append('uncertain_presence')
                if confidence is None or confidence < settings.min_box_confidence: c['reasons'].append('unverified_box')
                if reason: c['reasons'].append(reason)
                c['status'] = ('accepted' if not c['reasons'] else 'rejected'
                               if c['reasons'] == ['selected_target_owned_elsewhere'] else 'unresolved')
            candidates.append(c)
            if progress: progress(len(candidates), candidates)
    except ProviderError as exc:
        status, stop = 'error', str(exc)
    except (ValueError, KeyError, TypeError, AttributeError):
        status, stop = 'error', 'invalid_provider_response'
    duplicates = []
    eligible = [c for c in candidates if c['status'] == 'accepted']
    for i, a in enumerate(eligible):
        for b in eligible[i+1:]:
            overlap = iou(Box.read(a['box']), Box.read(b['box']))
            if overlap >= settings.duplicate_iou:
                duplicates.append({'cores': [a['core_id'], b['core_id']], 'iou': overlap})
                for c in (a, b):
                    c['status'] = 'unresolved'
                    if 'possible_duplicate_identity' not in c['reasons']: c['reasons'].append('possible_duplicate_identity')
    detections = [{'id': 'card-'+c['core_id'], 'box': c['box'], 'confidence': c['confidence'],
                   'presence': c['presence'], 'core_id': c['core_id'],
                   'identification': {'status': 'unknown', 'candidates': [], 'reason': 'not_run'}}
                  for c in candidates if c['status'] == 'accepted']
    calls = client.calls-initial_calls
    return {'schema_version': 1, 'mode': client.mode, 'experiment': 'local_center_context_v1',
            'evidence': 'live_exploratory' if client.mode == 'live' else 'mock_replay_not_vision',
            'coordinate_system': COORDINATES, 'image': {'width': prepared.original.width, 'height': prepared.original.height},
            'settings': asdict(settings), 'candidates': candidates, 'detections': detections,
            'possible_duplicate_pairs': duplicates, 'status': status, 'stop_reason': stop,
            'api_calls': calls if client.mode == 'live' else 0, 'provider_calls': calls, 'question_count': calls*2,
            'visited_cores': len(candidates), 'planned_cores': settings.grid**2,
            'usage': usage, 'estimated_cost_usd': usage['input_tokens']*PRICE_PER_MILLION_INPUT/1e6,
            'pipeline_elapsed_ms': round((time.perf_counter()-start)*1000, 3)}
