#!/usr/bin/env python3
"""Render the README showcase image (``docs/images/firaplexcode-sample.png``).

Pillow alone cannot shape OpenType ligatures (no bundled HarfBuzz/raqm), so
rendering is a two-step dance:

1. ``uharfbuzz`` shapes each text segment with ``calt`` enabled and reports
   glyph IDs + pen advances.
2. Pillow rasterises the glyphs one by one. Because ligature glyphs
   (``equal_start.seq`` and friends) have no Unicode codepoint, we first build
   an in-memory copy of each style whose cmap additionally maps every glyph ID
   to a Plane-14 PUA codepoint; a shaped glyph ID then becomes "draw this PUA
   character at this pen position".

Font files are taken from ``build/stage2/NerdFont`` when present (icons
patched into italics), falling back to ``build/stage1/standard`` so the image
can be produced without FontForge.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import uharfbuzz as hb
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
from PIL import Image, ImageDraw, ImageFont

from _common import ROOT, STAGE1_DIR, STAGE2_DIR, load_config, make_logger, variant_compact_suffix

log = make_logger("image")

SCALE = 2  # render at 2x for retina displays
FONT_SIZE = 20 * SCALE
LINE_HEIGHT_UNITS = 2400  # hhea ascent 1800 + descent 600 (aligned in Stage 1)

# Plane-14 PUA base for the gid -> codepoint remap. Plane 14 keeps us clear of
# the real Nerd Font icon ranges in planes 0/15.
PUA_BASE = 0xE0000

BG = "#0d1117"
BORDER = "#30363d"
TITLEBAR = "#161b22"
COLORS = {
    "text": "#e6edf3",
    "comment": "#8b949e",
    "keyword": "#ff7b72",
    "string": "#a5d6ff",
    "number": "#79c0ff",
    "dim": "#6e7681",
}

ICON_ROW = [0xE61E, 0xE70C, 0xE718, 0xF02D, 0xF179, 0xF17A, 0xF268, 0xF308, 0xF41B, 0xF469]

# Sample lines: lists of (text, style, color) segments. Ligature-bearing text
# stays inside a single segment - shaping happens per segment, so a ligature
# split across two segments would not form.
LINES: list[list[tuple[str, str, str]]] = [
    [("// FiraCode ligatures + IBM Plex Mono italics + Nerd Font icons", "Italic", "comment")],
    [
        ("import", "Bold", "keyword"),
        (" { fetchUser } ", "Regular", "text"),
        ("from", "Bold", "keyword"),
        (' "./api"', "Regular", "string"),
        (";", "Regular", "text"),
    ],
    [],
    [
        ("const", "Bold", "keyword"),
        (" user = ", "Regular", "text"),
        ("await", "Bold", "keyword"),
        (" fetchUser(id", "Regular", "text"),
        (" ?? ", "Regular", "keyword"),
        ("null", "Regular", "number"),
        (");", "Regular", "text"),
    ],
    [
        ("return", "Bold", "keyword"),
        (" a ", "Regular", "text"),
        ("!==", "Regular", "keyword"),
        (" b ", "Regular", "text"),
        ("&&", "Regular", "keyword"),
        (" xs ", "Regular", "text"),
        ("<=", "Regular", "keyword"),
        (" 9", "Regular", "number"),
        (";", "Regular", "text"),
    ],
    [],
    [("-> => == === != !== >= <= && || ?? |> ++ <!--", "Regular", "text")],
    [],
    [("Regular      ", "Regular", "dim"), ("The quick brown fox 0123 <=>", "Regular", "text")],
    [("Bold         ", "Regular", "dim"), ("The quick brown fox 0123 <=>", "Bold", "text")],
    [("Italic       ", "Regular", "dim"), ("The quick brown fox 0123 <=>", "Italic", "text")],
    [("Bold Italic  ", "Regular", "dim"), ("The quick brown fox 0123 <=>", "BoldItalic", "text")],
    [],
    [(" ".join(chr(c) for c in ICON_ROW), "Regular", "string")],
]

STYLE_FILES = {
    "Regular": "{base}-Regular.ttf",
    "Bold": "{base}-Bold.ttf",
    "Italic": "{base}-Italic.ttf",
    "BoldItalic": "{base}-BoldItalic.ttf",
}


def find_fonts() -> dict[str, Path]:
    """Locate the four style TTFs, preferring Stage 2 output over Stage 1.

    Directory and file names are derived from config.json (standard variant)
    via the same ``variant_compact_suffix`` convention the build stages use.
    """
    cfg = load_config()
    family_compact = cfg["family_name"].replace(" ", "")
    standard = next(v for v in cfg["variants"] if v["id"] == "standard")
    suffix_compact = variant_compact_suffix(standard["suffix"])
    candidates = [
        (STAGE2_DIR / suffix_compact, family_compact + suffix_compact),
        (STAGE1_DIR / "standard", family_compact),
    ]
    for directory, base in candidates:
        fonts = {style: directory / STYLE_FILES[style].format(base=base) for style in STYLE_FILES}
        if all(p.is_file() for p in fonts.values()):
            return fonts
    searched = ", ".join(str(d) for d, _ in candidates)
    raise FileNotFoundError(f"no complete 4-style set found under: {searched}. Run Stage 1 first.")


def remap_glyphs_to_pua(font_path: Path) -> bytes:
    """Return the font's bytes with an added cmap: U+E0000+gid -> each glyph.

    Ligature glyphs have no codepoint of their own; this remap lets Pillow
    draw any glyph ID as a plain character after HarfBuzz shaping.
    """
    font = TTFont(str(font_path))
    subtable = next(
        (t for t in font["cmap"].tables if (t.platformID, t.platEncID, t.language) == (3, 10, 0)),
        None,
    )
    if subtable is None:
        # The Plex-derived italics ship BMP-only subtables; a fresh (3,10)
        # subtable must carry the FULL existing repertoire, not just the
        # remap - HarfBuzz/FreeType prefer (3,10) over (3,1), so anything
        # missing here becomes .notdef.
        subtable = CmapSubtable.newSubtable(12)
        subtable.platformID = 3
        subtable.platEncID = 10
        subtable.language = 0
        subtable.cmap = dict(font.getBestCmap())
        font["cmap"].tables.append(subtable)
    subtable.ensureDecompiled()
    subtable.cmap.update({PUA_BASE + gid: name for gid, name in enumerate(font.getGlyphOrder())})
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()


class StyleRenderer:
    """Shapes and draws one style of the family."""

    def __init__(self, font_path: Path):
        data = remap_glyphs_to_pua(font_path)
        self.hb_face = hb.Face(data)
        self.hb_font = hb.Font(self.hb_face)
        self.pil_font = ImageFont.truetype(io.BytesIO(data), FONT_SIZE)
        self.upm = self.hb_face.upem

    def units_to_px(self, units: float) -> float:
        return units * FONT_SIZE / self.upm

    def draw_segment(
        self, draw: ImageDraw.ImageDraw, x: float, top: float, text: str, fill: str
    ) -> float:
        """Shape and draw ``text`` at pen position ``x``; return the new pen x."""
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.hb_font, buf, {"calt": True})
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions, strict=True):
            ch = chr(PUA_BASE + info.codepoint)
            draw.text(
                (x + self.units_to_px(pos.x_offset), top + self.units_to_px(pos.y_offset)),
                ch,
                font=self.pil_font,
                fill=fill,
                anchor="la",
            )
            x += self.units_to_px(pos.x_advance)
        return x


def render(fonts: dict[str, Path], out_path: Path) -> None:
    renderers = {style: StyleRenderer(p) for style, p in fonts.items()}
    cell_px = renderers["Regular"].units_to_px(1200)
    line_px = renderers["Regular"].units_to_px(LINE_HEIGHT_UNITS)

    pad_x = 24 * SCALE
    pad_y = 16 * SCALE
    titlebar_h = 26 * SCALE
    max_chars = max(sum(len(text) for text, _, _ in line) for line in LINES if line)
    width = int(pad_x * 2 + (max_chars + 1) * cell_px)
    height = int(pad_y * 2 + titlebar_h + len(LINES) * line_px)

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        (0, 0, width - 1, height - 1), radius=10 * SCALE, fill=BG, outline=BORDER
    )
    draw.rectangle((1, 1, width - 2, titlebar_h), fill=TITLEBAR)
    for i, dot in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        r = 5 * SCALE
        cx = pad_x + i * 2.6 * r
        cy = titlebar_h / 2
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=dot)
    draw.line((0, titlebar_h, width, titlebar_h), fill=BORDER)

    y = titlebar_h + pad_y
    for line in LINES:
        x = pad_x
        for text, style, color in line:
            x = renderers[style].draw_segment(draw, x, y, text, COLORS[color])
        y += line_px

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    log(f"-> {out_path.relative_to(ROOT)} ({width}x{height})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(ROOT / "docs" / "images" / "firaplexcode-sample.png"),
        help="Output PNG path.",
    )
    args = parser.parse_args()

    fonts = find_fonts()
    log(f"using fonts from {fonts['Regular'].parent.relative_to(ROOT)}")
    render(fonts, Path(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
