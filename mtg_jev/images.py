"""Orient, strip metadata, resize/pad for the API, and preserve coordinates."""

import io
import math
import warnings
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from .geometry import Box

MAX_FILE = 16 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 25_000_000


@dataclass
class Prepared:
    original: Image.Image
    image: Image.Image
    resized: tuple[int, int]
    padding: tuple[int, int]

    def metadata(self):
        return {"oriented_original": list(self.original.size),
                "prepared": list(self.image.size), "resized": list(self.resized),
                "padding_xy": list(self.padding), "exif_applied": True}

    def to_original(self, box):
        pw, ph = self.image.size
        rw, rh = self.resized
        px, py = self.padding
        return Box(max(0, min(1, (box.left * pw - px) / rw)),
                   max(0, min(1, (box.top * ph - py) / rh)),
                   max(0, min(1, (box.right * pw - px) / rw)),
                   max(0, min(1, (box.bottom * ph - py) / rh)))

    def to_prepared(self, box):
        pw, ph = self.image.size
        rw, rh = self.resized
        px, py = self.padding
        return Box((box.left * rw + px) / pw, (box.top * rh + py) / ph,
                   (box.right * rw + px) / pw, (box.bottom * rh + py) / ph)


def prepare(data):
    if not data or len(data) > MAX_FILE:
        raise ValueError("Image must be nonempty and at most 16 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as raw:
                if raw.format not in ("PNG", "JPEG") or getattr(raw, "is_animated", False):
                    raise ValueError("Use a still PNG or JPEG")
                if raw.width * raw.height > Image.MAX_IMAGE_PIXELS:
                    raise ValueError("Decoded image exceeds 25 million pixels")
                oriented = ImageOps.exif_transpose(raw)
                # Copy pixels into a fresh image to remove EXIF, comments and profiles.
                original = Image.new("RGB", oriented.size, "white")
                rgba = oriented.convert("RGBA")
                original.paste(rgba, mask=rgba.getchannel("A"))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ValueError("Invalid or oversized image") from exc
    ratio = min(1.0, 1568 / max(original.size))
    resized = tuple(max(1, round(v * ratio)) for v in original.size)
    scaled = original.resize(resized, Image.Resampling.LANCZOS)
    size = tuple(max(280, v) for v in resized)
    padding = tuple((v - r) // 2 for v, r in zip(size, resized))
    image = Image.new("RGB", size, (32, 36, 44))
    image.paste(scaled, padding)
    return Prepared(original, image, resized, padding)


def png_bytes(image):
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def overlay(prepared, result):
    image = prepared.original.copy()
    image.thumbnail((1568, 1568))
    draw = ImageDraw.Draw(image)
    for detection in result["detections"]:
        coords = Box.read(detection["box"]).pixels(*image.size)
        draw.rectangle(coords, outline="#ff5685", width=3)
        draw.text((coords[0] + 3, coords[1] + 3), detection["id"], fill="white",
                  stroke_width=2, stroke_fill="black")
    draw.rectangle((0, 0, image.width, 22), fill="#151a23")
    draw.text((5, 5), f'{result["mode"].upper()} | {result["status"]} | {result["stop_reason"]}',
              fill="white")
    return image


def crop(prepared, box):
    left, top, right, bottom = box.pixels(*prepared.original.size)
    return prepared.original.crop((math.floor(left), math.floor(top),
                                   math.ceil(right), math.ceil(bottom)))
