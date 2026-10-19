"""Fixed-endpoint HTTP adapter. No retries, body logging, or redirect following."""

import copy
import json
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass

from .geometry import probability
from .images import png_bytes

MODEL = "jev-1.14.0"
ENDPOINT = "https://api.typesafe.ai/v1/systemone"
PRICE_PER_MILLION_INPUT = 0.042  # Documentation checked 2026-10-19; estimate only.
RESERVED_INPUT_PER_REQUEST = 65_536


class ProviderError(Exception):
    """Only safe, fixed error messages may be exposed to users."""


@dataclass(frozen=True)
class LiveApproval:
    approve_live: bool = False
    max_requests: int = 8
    budget_usd: float = 0.0

    def validate(self):
        if not self.approve_live or os.environ.get("MTG_JEV_ENABLE_LIVE") != "1":
            raise ValueError("Live disabled: approval flag and MTG_JEV_ENABLE_LIVE=1 required")
        if type(self.max_requests) is not int or not 1 <= self.max_requests <= 30:
            raise ValueError("max_requests must be 1..30")
        if isinstance(self.budget_usd, bool) or not isinstance(self.budget_usd, (int, float)):
            raise ValueError("Invalid budget")
        import math
        if not math.isfinite(self.budget_usd) or not 0 < self.budget_usd <= 1:
            raise ValueError("Explicit budget_usd in (0, 1] required")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError("Jev redirect refused")


class JevClient:
    mode = "live"

    def __init__(self, approval):
        # Gate BEFORE touching the API key or creating an HTTP opener.
        approval.validate()
        key = os.environ.get("TYPESAFE_API_KEY", "")
        if not key or key == "YOUR_TYPESAFE_API_KEY" or any(c.isspace() for c in key):
            raise ValueError("A securely supplied TYPESAFE_API_KEY is required")
        self._key = key
        self.approval = approval
        self.calls = 0
        self.input_tokens = 0
        self.opener = urllib.request.build_opener(NoRedirect())

    def ask(self, prepared, excluded):
        reservation = (self.input_tokens + RESERVED_INPUT_PER_REQUEST) * PRICE_PER_MILLION_INPUT / 1e6
        if self.calls >= self.approval.max_requests:
            raise ProviderError("Request limit reached")
        if reservation > self.approval.budget_usd:
            raise ProviderError("Estimated cost reservation exceeds approved budget")
        body, content_type = multipart(prepared, excluded)
        request = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
            "Authorization": f"Bearer {self._key}",
            "TypeSafe-Danger-Zone-Images": "true",
            "Content-Type": content_type,
            "Accept": "application/json",
        })
        self.calls += 1
        # No retries: a timeout may already have incurred provider cost.
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ProviderError("Jev response too large")
            value = json.loads(raw)
            usage = read_usage(value)
            # Count valid usage even if answer validation later fails.
            self.input_tokens += usage["input_tokens"]
            return value
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"Jev HTTP {exc.code}; response body omitted") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ProviderError("Jev connection failed; no automatic retry") from None
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError):
            raise ProviderError("Invalid Jev response; response body omitted") from None


def request_document(excluded, reference):
    target = ("the leftmost not-yet-reported Magic: The Gathering card in `photo`, "
              "ordered by card center x then center y. `excluded_boxes` identifies "
              "already reported individual cards using normalized full-image edges; "
              "a different card may overlap them. Include rotated and partly occluded "
              "cards. Return the tight axis-aligned box of visible card extent. "
              "Do not identify a card's name or enclose multiple cards together")
    return {
        "model": MODEL,
        "state": {"photo": reference, "excluded_boxes": [b.json() for b in excluded]},
        "assets": {reference: {"type": "image", "part": "photo"}},
        "questions": {
            "remaining": {"type": "noul", "instructions": (
                "Is there at least one not-yet-reported Magic: The Gathering card "
                "in `photo`? `excluded_boxes` lists already reported individual cards. "
                "A distinct overlapping card still counts. Do not infer a card from "
                "an empty rectangle, background, logo, or box alone.")},
            "card": {"type": "box", "instructions": target},
        },
    }


def multipart(prepared, excluded):
    reference = str(uuid.uuid4())
    document = json.dumps(request_document(excluded, reference), allow_nan=False).encode()
    boundary = "mtg-jev-" + uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"request\"\r\n"
            "Content-Type: application/json\r\n\r\n").encode() + document
    body += (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; "
             "filename=\"image.png\"\r\nContent-Type: image/png\r\n\r\n").encode()
    body += png_bytes(prepared.image) + f"\r\n--{boundary}--\r\n".encode()
    if len(body) > 32 * 1024 * 1024:
        raise ValueError("Prepared request exceeds 32 MiB")
    return body, f"multipart/form-data; boundary={boundary}"


def read_usage(response):
    if not isinstance(response, dict):
        raise ValueError("Response must be an object")
    usage = response.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("Missing token usage")
    for key in ("input_tokens", "output_tokens"):
        if type(usage.get(key)) is not int or usage[key] < 0:
            raise ValueError("Invalid token usage")
    return {k: usage[k] for k in ("input_tokens", "output_tokens")}


class MockClient:
    """Replays supplied answers. Never examines pixels and never contacts Jev."""
    mode = "mock"

    def __init__(self, responses):
        if not isinstance(responses, list) or len(responses) > 31:
            raise ValueError("Mock script needs a list of at most 31 responses")
        self.responses = copy.deepcopy(responses)
        self.calls = 0

    def ask(self, prepared, excluded):
        if self.calls >= len(self.responses):
            raise ProviderError("Mock script exhausted")
        response = self.responses[self.calls]
        self.calls += 1
        return copy.deepcopy(response)


def mock_script(prepared, boxes):
    responses = [{"model": "mock-script", "usage": {"input_tokens": 0, "output_tokens": 0},
                  "answers": {
                      "remaining": {"type": "noul", "noul": 0.99},
                      "card": {"type": "box", "box": prepared.to_prepared(box).json(),
                               "confidence": 0.99},
                  }} for box in boxes]
    # Deliberately supply an arbitrary box for absence, matching the API's semantics.
    responses.append({"model": "mock-script", "usage": {"input_tokens": 0, "output_tokens": 0},
                      "answers": {"remaining": {"type": "noul", "noul": 0.01},
                                  "card": {"type": "box", "box": {
                                      "left": 0.1, "top": 0.1, "right": 0.9, "bottom": 0.9},
                                           "confidence": 0.9}}})
    return responses
