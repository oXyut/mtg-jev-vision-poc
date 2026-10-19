"""Normalized axis-aligned boxes and thresholded one-to-one matching."""

import math
from dataclasses import asdict, dataclass


def probability(value, label="probability"):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid {label}")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"Invalid {label}")
    return float(value)


@dataclass(frozen=True)
class Box:
    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self):
        for v in (self.left, self.top, self.right, self.bottom):
            probability(v, "box coordinate")
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("Box must have positive area")

    @classmethod
    def read(cls, data):
        if not isinstance(data, dict):
            raise ValueError("Box must be an object")
        try:
            return cls(**{k: data[k] for k in ("left", "top", "right", "bottom")})
        except (KeyError, TypeError) as exc:
            raise ValueError("Box needs left/top/right/bottom") from exc

    def json(self):
        return asdict(self)

    def pixels(self, width, height):
        return [self.left * width, self.top * height,
                self.right * width, self.bottom * height]


def iou(a, b):
    intersection = max(0, min(a.right, b.right) - max(a.left, b.left)) * max(
        0, min(a.bottom, b.bottom) - max(a.top, b.top))
    area_a = (a.right - a.left) * (a.bottom - a.top)
    area_b = (b.right - b.left) * (b.bottom - b.top)
    return intersection / (area_a + area_b - intersection)


def evaluate(predictions, truth, threshold=0.5):
    """Maximum-cardinality matching; not confidence-ranked COCO AP/mAP."""
    probability(threshold, "IoU threshold")
    if threshold <= 0:
        raise ValueError("IoU threshold must be positive")
    edges = {p: sorted((t for t, box in enumerate(truth)
                        if iou(pred, box) >= threshold),
                       key=lambda t: (-iou(pred, truth[t]), t))
             for p, pred in enumerate(predictions)}
    owners = {}

    def assign(p, seen):
        for t in edges[p]:
            if t in seen:
                continue
            seen.add(t)
            if t not in owners or assign(owners[t], seen):
                owners[t] = p
                return True
        return False

    for p in edges:
        assign(p, set())
    matches = [{"prediction": p, "truth": t, "iou": iou(predictions[p], truth[t])}
               for t, p in sorted(owners.items())]
    tp, np, nt = len(matches), len(predictions), len(truth)
    return {
        "iou_threshold": threshold, "matching": "maximum_cardinality_one_to_one",
        "tp": tp, "fp": np - tp, "fn": nt - tp,
        "precision": tp / np if np else None,
        "recall": tp / nt if nt else None,
        "f1": 2 * tp / (np + nt) if np + nt else None,
        "mean_matched_iou": sum(m["iou"] for m in matches) / tp if tp else None,
        "empty_scene_correct": np == nt == 0, "matches": matches,
    }


COORDINATES = "normalized_xyxy_exif_oriented_original"


def evaluate_documents(prediction, truth, threshold=0.5):
    for document in (prediction, truth):
        if document.get("coordinate_system") != COORDINATES:
            raise ValueError("Coordinate system mismatch")
        size = document.get("image", {})
        if any(type(size.get(k)) is not int or size[k] <= 0 for k in ("width", "height")):
            raise ValueError("Missing image dimensions")
    if prediction["image"] != truth["image"]:
        raise ValueError("Image dimensions differ")
    detections = prediction.get("detections")
    boxes = truth.get("boxes")
    if not isinstance(detections, list) or not isinstance(boxes, list):
        raise ValueError("Expected detections and boxes arrays")
    if max(len(detections), len(boxes)) > 200:
        raise ValueError("At most 200 boxes per evaluation")
    result = evaluate([Box.read(d["box"]) for d in detections],
                      [Box.read(b) for b in boxes], threshold)
    result.update(mode=prediction.get("mode", "unknown"),
                  evidence=prediction.get("evidence", "unverified"),
                  detection_status=prediction.get("status", "unknown"))
    return result
