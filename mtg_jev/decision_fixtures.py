"""Self-authored synthetic generic-card smoke fixtures. No MTG artwork/claims.

These are deliberately easy illustrations, not photographs or a recognition
benchmark. They may test a real API's image handling; they never test real MTG
performance, localization, identity, robustness, or a matched Jev comparison.
"""
import hashlib
import io
import json
from decimal import Decimal

from PIL import Image, ImageDraw, ImageFont

from .decisions_budget import LONG_CONTEXT_THRESHOLD, PRICING_ID, private_path, usd


def synthetic_pair():
    result = []
    for present in (True, False):
        image = Image.new('RGB', (640, 480), '#cad4df')
        draw = ImageDraw.Draw(image)
        if present:
            draw.rounded_rectangle((180, 32, 460, 448), radius=18, fill='#172338')
            draw.rounded_rectangle((192, 44, 448, 436), radius=10, fill='#f2eddf')
            font = ImageFont.load_default(size=19)
            small = ImageFont.load_default(size=16)
            draw.text((207, 60), 'ORBITAL SENTINEL', fill='#172338', font=font)
            draw.rectangle((207, 96, 433, 276), fill='#233e68')
            draw.ellipse((255, 117, 385, 247), fill='#c1dcf2', outline='#e8c65e', width=6)
            draw.polygon([(320, 134), (364, 225), (277, 225)], fill='#526c96')
            draw.text((207, 292), 'Starbound Construct', fill='#172338', font=small)
            draw.line((207, 320, 433, 320), fill='#172338', width=2)
            draw.text((207, 338), 'Shield: Prevent one damage.', fill='#172338', font=small)
            draw.text((207, 375), 'Attack 3   Defense 4', fill='#172338', font=small)
            draw.text((207, 408), 'SYNTHETIC PLAYTEST CARD', fill='#47566a', font=ImageFont.load_default(size=12))
        buffer = io.BytesIO()
        image.save(buffer, 'PNG')
        result.append(buffer.getvalue())
    return tuple(result)


def prepare(folder):
    """Create once outside the checkout. Never overwrite a prior smoke protocol."""
    folder = private_path(folder)
    folder.mkdir(mode=0o700, parents=False, exist_ok=False)
    originals = []
    for name, data in zip(('synthetic-card.png', 'synthetic-empty.png'), synthetic_pair()):
        path = folder / name
        with path.open('xb') as handle:
            handle.write(data)
        originals.append({'path': str(path), 'sha256': hashlib.sha256(data).hexdigest()})
    manifest = {'schema': 1, 'evaluation_kind': 'fixture_smoke', 'images_approved': True,
                'request_count_approved': 2, 'pricing_id': PRICING_ID,
                'standard_decisions_pricing_reviewed': True,
                'originals': originals, 'fixture_truth': [True, False],
                'fixture_provenance': [
                    {'kind': 'synthetic', 'description': 'Self-authored generic trading-card illustration; mtg_jev.decision_fixtures.synthetic_pair; not an MTG card/photo'},
                    {'kind': 'synthetic', 'description': 'Self-authored plain background without a card; mtg_jev.decision_fixtures.synthetic_pair'}],
                'runs': [{'source': i, 'task': 'presence', 'condition': 'generic_plain'} for i in (0, 1)]}
    path = folder / 'approval.json'
    with path.open('x') as handle:
        json.dump(manifest, handle, allow_nan=False, indent=2)
    return path


def summarize(entries, expected=(True, False)):
    """Whitelisted public fixture aggregates; never raw responses or bindings."""
    if len(entries) != 2 or len(expected) != 2 or any(type(x) is not bool for x in expected):
        raise ValueError('Two declared fixture truths required')
    rows = []
    known_cost = held = 0
    unknown_cost = False
    for label, truth, entry in zip(('synthetic_card', 'synthetic_empty'), expected, entries):
        answer = (entry.get('answers') or {}).get('present', {})
        p = answer.get('probability') if answer.get('status') == 'answered' else None
        predicted = p >= .5 if p is not None else None
        usage = entry['usage']
        estimate = None
        if usage is not None:
            estimate = usage['input_tokens'] * (200 if usage['input_tokens'] > LONG_CONTEXT_THRESHOLD else 100)
            known_cost += estimate
        elif entry['http_attempted_upper_bound']:
            unknown_cost = True
        held += int(Decimal(entry['reserved_upper_bound_usd']) * 1_000_000_000)
        rows.append({'fixture': label, 'expected_present': truth, 'status': entry['status'],
                     'probability': p, 'predicted_present_at_0_5': predicted,
                     'correct': predicted == truth if predicted is not None else None,
                     'latency_seconds': entry['seconds'], 'reported_usage': usage,
                     'estimated_standard_cost_usd': usd(estimate) if estimate is not None else None,
                     'accounted_cost_upper_bound_usd': entry['cost_upper_bound_usd']})
    return {'evaluation': 'real_decisions_api_on_synthetic_generic_card_fixtures',
            'original_photo_benchmark': False, 'matched_jev_comparison': False,
            'threshold': 0.5, 'requests_attempted': sum(e['http_attempted_upper_bound'] for e in entries),
            'requests_succeeded': sum(e['status'] == 'complete' for e in entries),
            'correct_answer_count': sum(r['correct'] is True for r in rows),
            'evaluated_answer_count': sum(r['correct'] is not None for r in rows),
            'known_standard_cost_estimate_usd': usd(known_cost),
            'total_standard_cost_estimate_usd': None if unknown_cost else usd(known_cost),
            'reported_billing_usd': None, 'billing_measured': False,
            'uncertain_reservation_usd': usd(held), 'fixtures': rows}
