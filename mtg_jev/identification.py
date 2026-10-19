"""Offline OCR → name retrieval → conservative open-set decision.

No card database is bundled or downloaded. Similarity scores are not probabilities.
"""

import copy
import csv
import hashlib
import io
import json
import math
import re
import shutil
import subprocess
import time
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher, get_close_matches

from PIL import Image, ImageOps

from .geometry import COORDINATES, Box, evaluate
from .images import crop, png_bytes


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


class Catalog:
    def __init__(self, document):
        provenance = document.get('provenance', {})
        if provenance.get('rights_confirmed') is not True or any(
            not isinstance(provenance.get(k), str) or not 1 <= len(provenance[k]) <= 500
            for k in ('source', 'license', 'retrieved_at')):
            raise ValueError('Catalog requires declared source/license/date and rights confirmation')
        self.coverage = document.get('coverage')
        if self.coverage not in ('reference', 'partial'):
            raise ValueError('Declare reference or partial catalog coverage')
        records = document.get('cards')
        if not isinstance(records, list) or not 1 <= len(records) <= 50_000:
            raise ValueError('Catalog must contain 1..50000 names')
        self.cards, self.aliases = {}, defaultdict(set)
        for record in records:
            card_id, name = record.get('id'), record.get('name')
            if not isinstance(card_id, str) or not 1 <= len(card_id) <= 128 or card_id in self.cards:
                raise ValueError('Catalog IDs must be unique strings')
            aliases = record.get('aliases', [])
            if not isinstance(aliases, list) or len(aliases) > 16:
                raise ValueError('At most 16 aliases per name')
            for alias in [name, *aliases]:
                if not isinstance(alias, str) or not 1 <= len(alias) <= 256 or not normalize(alias):
                    raise ValueError('Invalid card name/alias')
                self.aliases[normalize(alias)].add(card_id)
            self.cards[card_id] = {'id': card_id, 'name': name}
        self.digest = hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def decide(self, observations, top_k=5, ocr_status='ready', language=None):
        if type(top_k) is not int or not 1 <= top_k <= 10:
            raise ValueError('top_k must be 1..10')
        scores, exact_ids = {}, set()
        language_blocked = False
        for observation in observations:
            text, confidence = observation['text'], observation['confidence']
            if not isinstance(text, str) or len(text) > 512:
                raise ValueError('Invalid OCR observation')
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence):
                raise ValueError('Invalid OCR confidence')
            if not 0 <= confidence <= 100:
                raise ValueError('Invalid OCR confidence')
            key = normalize(text)
            if not key or confidence < 60:
                continue
            exact = self.aliases.get(key, set())
            short = len(key) < 4
            cjk = any('\u3400' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff' for c in key)
            languages = set(language.split('+')) if isinstance(language, str) else set()
            compatible = ((not languages or bool(languages & {'jpn', 'chi_sim', 'chi_tra'})) if cjk
                          else bool(languages & {'eng', 'deu', 'fra', 'spa', 'ita', 'por'}))
            if exact and short and not compatible:
                language_blocked = True
            if confidence >= (95 if short else 80) and (not short or compatible):
                exact_ids.update(exact)
            # Search the complete supplied catalog. Truncation affects display only.
            # Exact lookup precedes length gates. Short fuzzy strings are never
            # promoted, and ambiguous aliases are checked over the whole catalog.
            nearby = get_close_matches(key, self.aliases, n=10, cutoff=.5) if not short else []
            for alias in set(nearby) | ({key} if exact else set()):
                similarity = SequenceMatcher(None, key, alias, autojunk=False).ratio()
                for card_id in self.aliases[alias]:
                    old = scores.get(card_id, (-1, -1))
                    scores[card_id] = max(old, (similarity, confidence))
        ranking = sorted(scores, key=lambda c: (-scores[c][0], -scores[c][1], c))
        candidates = [{**self.cards[c], 'similarity': round(scores[c][0], 6),
                       'ocr_confidence_0_100': scores[c][1]} for c in ranking[:top_k]]
        reason = 'no_reliable_text' if not candidates else 'fuzzy_or_low_quality_match'
        accepted = None
        if ocr_status != 'ready':
            reason = ocr_status
        elif self.coverage == 'partial':
            reason = 'partial_catalog_candidate_only'
        elif len(exact_ids) > 1:
            reason = 'ambiguous_exact_match'
        elif len(exact_ids) == 1:
            accepted = self.cards[next(iter(exact_ids))]
            reason = 'unique_high_quality_exact_title'
        elif language_blocked:
            reason = 'short_title_language_mismatch_or_unknown'
        return {
            'status': 'identified' if accepted else 'unknown',
            'card_id': accepted['id'] if accepted else None,
            'name': accepted['name'] if accepted else None,
            'reason': reason, 'candidates': candidates,
            'score_type': 'string_similarity_and_tesseract_confidence_not_probability',
            'catalog_sha256': self.digest, 'catalog_coverage': self.coverage,
            'ocr_language': language,
            'warning': 'Exact OCR may still be wrong; reference coverage is caller-declared, not verified',
        }


def parse_tsv(data, rotation):
    groups = defaultdict(list)
    for row in csv.DictReader(io.StringIO(data), delimiter='\t'):
        try:
            text = row.get('text', '').strip()
            confidence = float(row['conf'])
            if text and math.isfinite(confidence) and 0 <= confidence <= 100:
                key = tuple(row[k] for k in ('page_num', 'block_num', 'par_num', 'line_num'))
                groups[key].append((text, confidence))
        except (KeyError, ValueError, TypeError):
            continue
    return [{'text': ' '.join(word for word, _ in words)[:512],
             'confidence': min(conf for _, conf in words), 'rotation': rotation}
            for words in list(groups.values())[:3]]


class TesseractOCR:
    def __init__(self, language='eng', timeout=5):
        if not re.fullmatch(r'[a-z0-9_]+(?:\+[a-z0-9_]+)*', language):
            raise ValueError('Invalid OCR language')
        if not 0 < timeout <= 30:
            raise ValueError('OCR timeout must be in (0, 30] seconds')
        self.language, self.timeout = language, timeout
        self.executable = shutil.which('tesseract')
        self.status = 'ready' if self.executable else 'ocr_engine_unavailable'
        if self.executable:
            try:
                completed = subprocess.run([self.executable, '--list-langs'], capture_output=True,
                                           timeout=timeout, check=False)
                supported = set(completed.stdout.decode('utf-8', errors='replace').splitlines()[1:])
                if completed.returncode or not set(language.split('+')) <= supported:
                    self.status = 'ocr_language_unavailable'
            except (OSError, subprocess.TimeoutExpired):
                self.status = 'ocr_engine_unavailable'

    def read(self, image):
        if self.status != 'ready':
            return [], self.status
        if min(image.size) < 30:
            return [], 'crop_too_small'
        observations, status = [], 'ready'
        for angle in (0, 90, 180, 270):
            oriented = image.rotate(angle, expand=True)
            w, h = oriented.size
            band = oriented.crop((int(w * .03), int(h * .02), int(w * .97), max(1, int(h * .30))))
            ratio = min(3, 2000 / max(band.size))
            band = band.resize(tuple(max(1, round(v * ratio)) for v in band.size), Image.Resampling.LANCZOS)
            band = ImageOps.expand(ImageOps.autocontrast(band.convert('L')), border=12, fill=255)
            try:
                result = subprocess.run([self.executable, 'stdin', 'stdout', '-l', self.language,
                                         '--psm', '6', 'tsv'], input=png_bytes(band),
                                        capture_output=True, timeout=self.timeout, check=False)
                if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
                    status = 'ocr_incomplete'
                else:
                    observations.extend(parse_tsv(result.stdout.decode('utf-8', errors='replace'), angle))
            except (OSError, subprocess.TimeoutExpired):
                status = 'ocr_incomplete'
        return observations, status


def identify_document(prepared, document, catalog, engine, crop_source='predicted', top_k=5):
    if document.get('coordinate_system') != COORDINATES or document.get('image') != {
        'width': prepared.original.width, 'height': prepared.original.height}:
        raise ValueError('Detection/image coordinate frame differs')
    detections = document.get('detections')
    if not isinstance(detections, list) or len(detections) > 30 or crop_source not in ('predicted', 'ground_truth'):
        raise ValueError('Invalid detections or crop source')
    result = copy.deepcopy(document)
    started = time.perf_counter()
    for detection in result['detections']:
        observations, status = engine.read(crop(prepared, Box.read(detection['box'])))
        identification = catalog.decide(observations, top_k, status, getattr(engine, 'language', None))
        identification['ocr_observations'] = len(observations)
        # OCR text remains in memory and is deliberately absent from saved results.
        detection['identification'] = identification
    result['identification_run'] = {
        'method': 'local_tesseract_title_ocr_then_catalog_search_exact_gate',
        'evidence': 'local_ocr_unvalidated_for_real_mtg', 'crop_source': crop_source,
        'language': getattr(engine, 'language', 'stub'), 'top_k': top_k,
        'catalog_sha256': catalog.digest, 'external_api_calls': 0,
        'elapsed_ms': round((time.perf_counter() - started) * 1000, 3),
    }
    return result


def evaluate_identifications(prediction, truth, threshold=.5, top_k=5):
    if type(top_k) is not int or not 1 <= top_k <= 10:
        raise ValueError('Invalid top_k')
    for doc in (prediction, truth):
        if doc.get('coordinate_system') != COORDINATES:
            raise ValueError('Coordinate system mismatch')
    if prediction.get('image') != truth.get('image'):
        raise ValueError('Image dimensions differ')
    detections, objects = prediction['detections'], truth['objects']
    if max(len(detections), len(objects)) > 30:
        raise ValueError('At most 30 objects for identification evaluation')
    localization = evaluate([Box.read(d['box']) for d in detections],
                            [Box.read(o['box']) for o in objects], threshold)
    pairs = {m['prediction']: m['truth'] for m in localization['matches']}
    known = sum(o.get('card_id') is not None for o in objects)
    matched_known = sum(objects[t].get('card_id') is not None for t in pairs.values())
    accepted = wrong = correct = topk_correct = unknown = 0
    for p, detection in enumerate(detections):
        identity = detection.get('identification', {})
        selected = identity.get('card_id') if identity.get('status') == 'identified' else None
        expected = objects[pairs[p]].get('card_id') if p in pairs else None
        if selected is None:
            unknown += 1
        else:
            accepted += 1
            if expected is not None and selected == expected:
                correct += 1
            else:
                wrong += 1  # Includes accepted names on localization false positives.
        candidates = identity.get('candidates', [])
        if expected is not None and expected in [c.get('id') for c in candidates[:top_k]]:
            topk_correct += 1
    def ratio(n, d):
        return n / d if d else None
    return {
        'localization': localization, 'known_truth_count': known,
        'matched_known_count': matched_known, 'accepted_count': accepted,
        'wrong_accepted_count': wrong, 'correct_accepted_count': correct,
        'top1_on_matched_known': ratio(correct, matched_known),
        'topk_on_matched_known': ratio(topk_correct, matched_known), 'top_k': top_k,
        'unknown_rate': ratio(unknown, len(detections)),
        'misidentification_rate_among_accepted': ratio(wrong, accepted),
        'end_to_end_name_recall': ratio(correct, known),
        'end_to_end_name_precision': ratio(correct, accepted),
        'crop_source': prediction.get('identification_run', {}).get('crop_source', 'unspecified'),
        'evidence': prediction.get('identification_run', {}).get('evidence', 'unverified'),
    }
