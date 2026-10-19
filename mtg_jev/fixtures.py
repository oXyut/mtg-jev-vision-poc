"""Original geometric fixtures; no MTG artwork, logos, or downloaded images."""

import json
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from .geometry import COORDINATES, Box
from .images import png_bytes, prepare
from .provider import mock_script

SCENARIOS = {
    "none": [],
    "single": [(400, 300, 180, 252, 0)],
    "multiple": [(145, 290, 150, 210, 0), (400, 290, 150, 210, 0), (655, 290, 150, 210, 0)],
    "rotated": [(230, 290, 165, 231, 32), (570, 300, 165, 231, -45)],
    "overlap": [(330, 285, 210, 294, -12), (455, 330, 210, 294, 18)],
    "edge": [(20, 300, 180, 252, 0), (720, 80, 140, 196, 0)],
}


def fixture(scenario):
    if scenario not in SCENARIOS:
        raise ValueError("Unknown synthetic scenario")
    image = Image.new("RGB", (800, 600), (28, 38, 52))
    masks = []
    for index, (cx, cy, width, height, angle) in enumerate(SCENARIOS[scenario]):
        theta = math.radians(angle)
        points = [(round(cx + x * math.cos(theta) - y * math.sin(theta)),
                   round(cy + x * math.sin(theta) + y * math.cos(theta)))
                  for x, y in [(-width / 2, -height / 2), (width / 2, -height / 2),
                               (width / 2, height / 2), (-width / 2, height / 2)]]
        ImageDraw.Draw(image).polygon(points, fill=["#7f93bf", "#c4a475", "#7fbaa6"][index % 3])
        mask = Image.new("L", image.size)
        ImageDraw.Draw(mask).polygon(points, fill=255)
        masks.append(mask)
    cover = Image.new("L", image.size)
    boxes = []
    for mask in reversed(masks):
        extent = ImageChops.subtract(mask, cover).getbbox()
        if extent:
            l, t, r, b = extent
            boxes.append(Box(l / 800, t / 600, r / 800, b / 600))
        cover = ImageChops.lighter(cover, mask)
    boxes.sort(key=lambda b: ((b.left + b.right) / 2, (b.top + b.bottom) / 2))
    return image, boxes


def generate(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for scenario in SCENARIOS:
        image, boxes = fixture(scenario)
        data = png_bytes(image)
        image_path = directory / f"{scenario}.png"
        image_path.write_bytes(data)
        prepared = prepare(data)
        truth = {"schema_version": 1, "coordinate_system": COORDINATES,
                 "image": {"width": 800, "height": 600}, "boxes": [b.json() for b in boxes]}
        script = {"schema_version": 1, "image": truth["image"],
                  "evidence": "scripted_from_synthetic_geometry_not_vision",
                  "responses": mock_script(prepared, boxes)}
        (directory / f"{scenario}.truth.json").write_text(json.dumps(truth, indent=2) + "\n")
        (directory / f"{scenario}.mock.json").write_text(json.dumps(script, indent=2) + "\n")
    (directory / "PROVENANCE.txt").write_text(
        "Original polygons rendered by mtg_jev.fixtures. No MTG artwork or logos.\n"
        "Annotations enclose visible polygon pixels; occluded pixels excluded.\n"
        "Mock answers are generated from known geometry; this is not recognition.\n")
