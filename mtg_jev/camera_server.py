"""Loopback camera snapshots, one provider request at a time; no frame storage."""
import json
import math
import secrets
import threading
import time
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

from .batch import BatchSettings, read_slots, short_ranked_document
from .camera_usage import UsageLedger, POC_LIMIT_USD, POC_SPENT_BEFORE_CAMERA, utc_now
from .geometry import COORDINATES, Box, probability
from .images import prepare
from .provider import (JevClient, LiveApproval, MODEL, ProviderError, read_usage,
                       request_document, RESERVED_INPUT_PER_REQUEST, PRICE_PER_MILLION_INPUT)
from .server import Handler

MAX_CAMERA_FRAME = 4*1024*1024
METHODS = {'single': '単一Box：左端1枚の提案', 'ordinal4': '短ordinal：最大4候補（実験）'}


@dataclass(frozen=True)
class CameraConfig:
    mode: str = 'mock'
    approve_live: bool = False
    max_requests: int = 20
    budget_usd: float = 0.0
    usage_ledger: str = 'camera-usage.json'
    prior_spend_usd: float = POC_SPENT_BEFORE_CAMERA
    min_interval_seconds: float = 2.0

    def validate(self):
        if self.mode not in ('mock', 'live') or type(self.max_requests) is not int or not 1 <= self.max_requests <= 1000:
            raise ValueError('Invalid camera configuration')
        if not math.isfinite(self.min_interval_seconds) or self.min_interval_seconds < 0:
            raise ValueError('Invalid sampling interval')
        if self.mode == 'live':
            LiveApproval(self.approve_live, self.max_requests, self.budget_usd).validate()
            if not math.isfinite(self.prior_spend_usd) or self.prior_spend_usd < POC_SPENT_BEFORE_CAMERA:
                raise ValueError('Invalid prior spend')


class CameraRequestError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


class CameraState:
    def __init__(self, config=CameraConfig(), client_factory=JevClient):
        config.validate()
        self.config, self.client, self.ledger = config, None, None
        self.lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)  # CSRF nonce, never an API credential.
        self.inflight, self.last_started, self.frames = False, -math.inf, 0
        self.halt_reason = None
        self.open_spent, self.initial_calls = 0., 0
        try:
            if config.mode == 'live':
                self.ledger = UsageLedger(config.usage_ledger, config.prior_spend_usd)
                d = self.ledger.data
                if d['pending'] or d['halt_reason']:
                    raise ValueError('Prior request unresolved; reconcile usage before restarting')
                self.open_spent = self.ledger.spent
                self.initial_calls = d['requests_attempted']
                self.session_ceiling = min(POC_LIMIT_USD, self.open_spent+config.budget_usd)
                approval = LiveApproval(True, min(10000, self.initial_calls+config.max_requests),
                                        self.session_ceiling-d['prior_spend_usd'])
                self.client = client_factory(approval)
                self.client.calls, self.client.input_tokens = self.initial_calls, d['input_tokens']
        except Exception:
            self.close()
            raise

    def snapshot(self):
        if self.ledger:
            with self.ledger.mutex:
                spent = self.ledger.spent
                calls = self.ledger.data['requests_attempted']-self.initial_calls
                budget = {'poc_spent_usd': spent, 'poc_limit_usd': POC_LIMIT_USD,
                          'session_spent_usd': spent-self.open_spent,
                          'session_limit_usd': self.config.budget_usd,
                          'session_remaining_usd': max(0, self.session_ceiling-spent),
                          'api_calls': calls, 'requests_remaining': max(0, self.config.max_requests-calls),
                          'halt_reason': self.halt_reason or self.ledger.data['halt_reason']}
        else:
            budget = {'api_calls': 0, 'session_spent_usd': 0, 'session_remaining_usd': None,
                      'poc_spent_usd': POC_SPENT_BEFORE_CAMERA, 'poc_limit_usd': POC_LIMIT_USD,
                      'requests_remaining': None, 'halt_reason': self.halt_reason}
        return {'mode': self.config.mode, 'model': MODEL if self.client else None,
                'methods': METHODS, 'min_interval_ms': int(self.config.min_interval_seconds*1000),
                'overlay_ttl_ms': 1500, 'max_dimension': 1280, 'csrf_token': self.token,
                'inflight': self.inflight, 'frames': self.frames, 'budget': budget}

    def frame(self, data, method='single', min_confidence=.6):
        if method not in METHODS:
            raise CameraRequestError(400, 'invalid_method')
        probability(min_confidence)
        if min_confidence <= 0:
            raise CameraRequestError(400, 'invalid_confidence_threshold')
        if not self.lock.acquire(blocking=False):
            raise CameraRequestError(409, 'inference_in_progress')
        start = time.perf_counter()
        try:
            if self.halt_reason:
                raise CameraRequestError(503, self.halt_reason)
            if start-self.last_started < self.config.min_interval_seconds:
                raise CameraRequestError(429, 'sampling_interval')
            prepared = prepare(data)
            self.inflight, self.last_started = True, start
            self.frames += 1
            questions = 2 if method == 'single' else 8
            if not self.client:
                # Synthetic protocol test, not vision. Never inspect pixels for "truth".
                n = 1 if method == 'single' else 3
                detections = [{'id': f'mock-{i+1}', 'box': Box(.1+.28*i, .18, .32+.28*i, .78).json(),
                               'confidence': .91-.04*i, 'presence': .99} for i in range(n)]
                detections = [d for d in detections if d['confidence'] >= min_confidence]
                usage, unresolved, stop = {'input_tokens': 0, 'output_tokens': 0}, 0, 'mock_not_vision'
            else:
                reserve = questions*RESERVED_INPUT_PER_REQUEST*PRICE_PER_MILLION_INPUT/1e6
                if self.client.calls-self.initial_calls >= self.config.max_requests:
                    raise CameraRequestError(402, 'request_limit_reached')
                if self.ledger.spent+reserve > self.session_ceiling:
                    raise CameraRequestError(402, 'estimated_budget_reservation')
                self.ledger.begin(questions)
                before = self.client.calls
                try:
                    response = self.client._ask(prepared, [], request_document, 2) if method == 'single' else self.client._ask(
                        prepared, [], lambda unused, ref: short_ranked_document(ref, 4), 8)
                    usage = read_usage(response)
                except (ProviderError, ValueError, TypeError):
                    self.ledger.failed(self.client.calls != before)
                    self.halt_reason = 'provider_stopped_no_retry'
                    raise CameraRequestError(502, self.halt_reason) from None
                self.ledger.finish(usage)  # Account BEFORE validating the model answer.
                if self.ledger.spent > self.session_ceiling:
                    self.ledger.halt('estimated_budget_exceeded')
                    self.halt_reason = 'estimated_budget_exceeded'
                    raise CameraRequestError(402, self.halt_reason)
                try:
                    if response.get('model') != MODEL:
                        raise ValueError('Unexpected model')
                    if method == 'single':
                        detections, unresolved, stop = self.single(response, prepared, min_confidence)
                    else:
                        candidates, detections, duplicates, _ = read_slots(response, 4, prepared,
                            BatchSettings(capacity=4, min_box_confidence=min_confidence))
                        unresolved = sum(c['status']=='unresolved' for c in candidates)
                        stop = 'independent_candidates_not_exhaustive'
                except (ValueError, KeyError, TypeError, AttributeError):
                    self.halt_reason = 'invalid_model_answer'
                    raise CameraRequestError(502, self.halt_reason) from None
            return {'mode': self.config.mode, 'evidence': 'live_model_predictions' if self.client else 'mock_script_not_vision',
                    'method': method, 'method_label': METHODS[method], 'model': MODEL if self.client else 'mock-script',
                    'coordinate_system': COORDINATES, 'image': {'width': prepared.original.width, 'height': prepared.original.height},
                    'detections': detections, 'unresolved_candidates': unresolved, 'stop_reason': stop,
                    'usage': usage, 'api_calls': 1 if self.client else 0, 'completed_at': utc_now(),
                    'server_latency_ms': round((time.perf_counter()-start)*1000, 1), 'budget': self.snapshot()['budget']}
        finally:
            self.inflight = False
            self.lock.release()

    @staticmethod
    def single(response, prepared, threshold):
        a = response['answers']
        if a['remaining']['type'] != 'noul':
            raise ValueError('Invalid presence')
        presence = probability(a['remaining']['noul'])
        if presence <= .2:
            return [], 0, 'model_reports_no_card'
        if a['card']['type'] != 'box':
            raise ValueError('Invalid Box')
        box = prepared.to_original(Box.read(a['card']['box']))
        confidence = a['card']['confidence']
        if confidence is not None:
            confidence = probability(confidence)
        if presence < .8 or confidence is None or confidence < threshold:
            return [], 1, 'uncertain_candidate'
        return [{'id': 'card-1', 'box': box.json(), 'confidence': confidence, 'presence': presence}], 0, 'one_proposal_not_exhaustive'

    def close(self):
        if self.ledger:
            self.ledger.close()


class CameraHandler(Handler):
    def respond(self, status, payload, content_type='application/json'):
        if isinstance(payload, dict):
            payload = json.dumps(payload, allow_nan=False).encode()
        self.send_response(status)
        for k, v in {'Content-Type': content_type, 'Content-Length': str(len(payload)),
                     'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                     'Referrer-Policy': 'no-referrer', 'Permissions-Policy': 'camera=(self), microphone=(), geolocation=()',
                     'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"}.items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass  # A stopped browser may disconnect; provider usage was still recorded.

    def do_GET(self):
        if not self.local_request():
            self.respond(403, {'error': 'loopback_origin_required'}); return
        path = urlsplit(self.path).path
        assets = {'/': ('camera.html', 'text/html; charset=utf-8'),
                  '/camera.js': ('camera.js', 'text/javascript; charset=utf-8'),
                  '/camera-core.js': ('camera-core.js', 'text/javascript; charset=utf-8')}
        if path in assets:
            name, mime = assets[path]
            self.respond(200, files('mtg_jev').joinpath(name).read_bytes(), mime)
        elif path == '/api/camera/config':
            self.respond(200, self.server.camera.snapshot())
        else:
            self.respond(404, {'error': 'not_found'})

    def do_POST(self):
        if not self.local_request() or not secrets.compare_digest(self.headers.get('X-Camera-Token', ''), self.server.camera.token):
            self.respond(403, {'error': 'local_camera_token_required'}); return
        url = urlsplit(self.path)
        if url.path != '/api/camera/frame':
            self.respond(404, {'error': 'not_found'}); return
        try:
            if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type', '').split(';')[0] not in ('image/jpeg', 'image/png'):
                raise ValueError('Invalid frame encoding')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_CAMERA_FRAME:
                self.respond(413, {'error': 'frame_size_limit'}); return
            self.connection.settimeout(10)
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError('Incomplete frame')
            query = parse_qs(url.query)
            if set(query)-{'method', 'confidence'}:
                raise ValueError('Unknown parameter')
            result = self.server.camera.frame(data, query.get('method', ['single'])[0],
                                              float(query.get('confidence', ['0.6'])[0]))
            self.respond(200, result)
        except CameraRequestError as exc:
            self.respond(exc.status, {'error': exc.code, 'budget': self.server.camera.snapshot()['budget']})
        except (ValueError, OSError, KeyError, TypeError):
            self.respond(400, {'error': 'invalid_frame_or_request'})


class CameraServer(ThreadingHTTPServer):
    def server_close(self):
        super().server_close()
        if hasattr(self, 'camera'):
            self.camera.close()


def make_camera_server(port=8766, config=CameraConfig(), client_factory=JevClient):
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('Invalid loopback port')
    state = CameraState(config, client_factory)
    try:
        server = CameraServer(('127.0.0.1', port), CameraHandler)
        server.camera = state
        return server
    except Exception:
        state.close()
        raise


def serve_camera(port=8766, config=CameraConfig()):
    with make_camera_server(port, config) as server:
        print(f'Camera UI ({config.mode}): http://127.0.0.1:{server.server_port}')
        print('Camera starts only from the browser button. Frames are not saved.')
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
