"""Bounded multi-object exploration on top of a single-box primitive."""

import time
from dataclasses import asdict, dataclass

from .geometry import COORDINATES, Box, iou, probability
from .provider import MODEL, PRICE_PER_MILLION_INPUT, ProviderError, read_usage


@dataclass(frozen=True)
class Settings:
    max_cards: int = 8
    max_requests: int = 10
    presence_yes: float = 0.8
    presence_no: float = 0.2
    min_box_confidence: float = 0.6
    duplicate_iou: float = 0.85

    def __post_init__(self):
        if type(self.max_cards) is not int or not 1 <= self.max_cards <= 29:
            raise ValueError("max_cards must be 1..29")
        if type(self.max_requests) is not int or not 1 <= self.max_requests <= 30:
            raise ValueError("max_requests must be 1..30")
        for k in ("presence_yes", "presence_no", "min_box_confidence", "duplicate_iou"):
            probability(getattr(self, k), k)
        if self.presence_no >= self.presence_yes or self.duplicate_iou <= 0:
            raise ValueError("Invalid threshold settings")


def detect(prepared, client, settings=Settings()):
    start = time.perf_counter()
    detections, excluded, trace = [], [], []
    usage = {"input_tokens": 0, "output_tokens": 0}
    resolved = set()
    stop, status = "request_limit", "incomplete"
    for index in range(settings.max_requests):
        try:
            response = client.ask(prepared, excluded)
            tokens = read_usage(response)
            for key in usage:
                usage[key] += tokens[key]
            model = response.get("model")
            expected = MODEL if client.mode == "live" else "mock-script"
            if model != expected:
                raise ValueError("Unexpected resolved model")
            resolved.add(model)
            answers = response["answers"]
            presence = answers["remaining"]
            if presence["type"] != "noul":
                raise ValueError("Wrong presence answer type")
            probability_yes = probability(presence["noul"])
            trace.append({"iteration": index + 1, "presence": probability_yes})
            if probability_yes <= settings.presence_no:
                stop, status = "no_remaining_target", "complete"
                break
            if probability_yes < settings.presence_yes:
                stop = "uncertain_presence"
                break
            answer = answers["card"]
            if answer["type"] != "box":
                raise ValueError("Wrong box answer type")
            box = Box.read(answer["box"])
            confidence = probability(answer["confidence"], "box confidence")
            trace[-1]["box_confidence"] = confidence
            if confidence < settings.min_box_confidence:
                stop = "low_box_confidence"
                break
            original_box = prepared.to_original(box)
            if any(iou(box, previous) >= settings.duplicate_iou for previous in excluded):
                stop = "duplicate_target"
                break
            excluded.append(box)
            detections.append({
                "id": f"card-{len(detections) + 1}", "box": original_box.json(),
                "box_pixels": original_box.pixels(*prepared.original.size),
                "confidence": confidence, "presence": probability_yes,
                "identification": {"status": "unknown", "candidates": [],
                                   "reason": "identification_not_implemented"},
            })
            if len(detections) >= settings.max_cards:
                stop = "card_limit"
                break
        except ProviderError as exc:
            status, stop = "error", str(exc)
            break
        except (ValueError, TypeError, KeyError, AttributeError):
            status, stop = "error", "invalid_provider_response"
            break
    is_live = client.mode == "live"
    return {
        "schema_version": 1, "mode": client.mode,
        "evidence": "live_unvalidated" if is_live else "mock_replay_not_vision",
        "coordinate_system": COORDINATES,
        "image": {"width": prepared.original.width, "height": prepared.original.height},
        "preprocessing": prepared.metadata(),
        "requested_model": MODEL if is_live else None, "resolved_models": sorted(resolved),
        "settings": asdict(settings), "detections": detections, "status": status,
        "stop_reason": stop, "trace": trace, "adapter_calls": client.calls,
        "api_calls": client.calls if is_live else 0, "usage": usage,
        "estimated_cost_usd": usage["input_tokens"] * PRICE_PER_MILLION_INPUT / 1e6 if is_live else 0.0,
        "price_usd_per_million_input_tokens": PRICE_PER_MILLION_INPUT if is_live else None,
        "cost_note": "Estimate from documented price; failed calls may incur unreported usage" if is_live else "No API calls",
        "pipeline_elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
        "warnings": (["MOCK: scripted boxes; not image recognition or real MTG accuracy"] if not is_live else
                     ["Experimental model; results require ground-truth evaluation"]),
    }
