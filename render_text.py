#!/usr/bin/env python3
"""render_text.py: Linux/CI-friendly twin of render_text.swift (same JSON-on-stdin interface), using Pillow.

Input (stdin): JSON array of {out, words, highlight, size, width, height, scale}; see render_text.swift.
Font: set FONT_PATH, else Impact (macOS), else DejaVu Sans Bold (Linux: apt install fonts-dejavu-core).
"""

import json
import os
import sys
from pathlib import Path
from typing import List

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_CANDIDATES: List[str] = [
    os.environ.get("FONT_PATH", ""),
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
YELLOW = (255, 214, 26, 255)
WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size)


def render(item: dict) -> None:
    width, height = item["width"], item["height"]
    words: List[str] = item["words"]
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if words:
        font_size = int(item["size"])
        measure = ImageDraw.Draw(canvas)

        def line_width(size: int) -> float:
            font = load_font(size)
            return sum(measure.textlength(word, font=font) for word in words) + size * 0.26 * (len(words) - 1)

        while line_width(font_size) > width * 0.92 and font_size > 20:
            font_size -= 4
        font = load_font(font_size)
        stroke = max(3, round(font_size * 0.07))

        shadow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        text_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shadow_draw, text_draw = ImageDraw.Draw(shadow_layer), ImageDraw.Draw(text_layer)
        cursor_x = (width - line_width(font_size)) / 2
        text_top = height / 2
        for index, word in enumerate(words):
            is_yellow = item["highlight"] == -2 or index == item["highlight"]
            shadow_draw.text((cursor_x + 2, text_top + 6), word, font=font, anchor="lm",
                             fill=(0, 0, 0, 170), stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
            text_draw.text((cursor_x, text_top), word, font=font, anchor="lm",
                           fill=YELLOW if is_yellow else WHITE, stroke_width=stroke, stroke_fill=BLACK)
            cursor_x += measure.textlength(word, font=font) + font_size * 0.26
        canvas = Image.alpha_composite(canvas, shadow_layer.filter(ImageFilter.GaussianBlur(6)))
        canvas = Image.alpha_composite(canvas, text_layer)

        scale = float(item["scale"])
        if abs(scale - 1.0) > 0.001:  # pop-in: scale around the canvas center
            scaled = canvas.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.LANCZOS)
            canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            canvas.paste(scaled, ((width - scaled.width) // 2, (height - scaled.height) // 2))
    canvas.save(item["out"])


if __name__ == "__main__":
    items = json.load(sys.stdin)
    for entry in items:
        render(entry)
    print(f"rendered {len(items)} images")
