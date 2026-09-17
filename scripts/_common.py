"""Shared helpers for the FiraPlexCode build pipeline scripts.

Centralises filesystem paths, config loading, logging, the canonical RIBBI
style table, and a few `name` table utilities used by both Stage 1
(`build_firaplex.py`) and Stage 2 (`patch_italics.py`). Keeping these in
one place avoids drift in family naming conventions and makes the per-stage
scripts read like thin orchestration layers over the shared primitives.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Filesystem layout
# ---------------------------------------------------------------------------

# All scripts live in <ROOT>/scripts/, so `parent.parent` resolves to the
# project root regardless of which script imports this module.
ROOT: Path = Path(__file__).resolve().parent.parent
CONFIG_PATH: Path = ROOT / "config.json"
SOURCES_DIR: Path = ROOT / "sources"
CACHE_DIR: Path = ROOT / ".cache"
BUILD_DIR: Path = ROOT / "build"
STAGE1_DIR: Path = BUILD_DIR / "stage1"
STAGE2_DIR: Path = BUILD_DIR / "stage2"
DIST_DIR: Path = ROOT / "dist"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Load and return the parsed `config.json`."""
    return json.loads(path.read_text(encoding="utf-8"))


def variant_ids(cfg: dict[str, Any]) -> list[str]:
    """Ordered list of variant ids from `config.json` (the single source of truth)."""
    return [v["id"] for v in cfg["variants"]]


def resolve_variants(cfg: dict[str, Any], selected: str) -> list[dict[str, Any]]:
    """Resolve a ``--variant`` CLI argument (an id or ``"all"``) to variant entries."""
    variants: list[dict[str, Any]] = cfg["variants"]
    if selected == "all":
        return variants
    return [v for v in variants if v["id"] == selected]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def make_logger(prefix: str) -> Callable[[str], None]:
    """Return a `log(msg)` callable that prefixes stderr lines with `[prefix]`.

    Each script calls `log = make_logger("build")` etc. so its messages remain
    visually distinct in interleaved pipeline output.
    """

    def _log(msg: str) -> None:
        print(f"[{prefix}] {msg}", file=sys.stderr)

    return _log


# ---------------------------------------------------------------------------
# Style table (RIBBI 4-member family)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StyleSpec:
    """Describes one of the four RIBBI styles in the family.

    `subfamily` is the user-facing form ("Bold Italic"). `ps_subfamily` is
    the spaceless PostScript form ("BoldItalic") used in nameID 6.
    """

    subfamily: str
    is_bold: bool
    is_italic: bool
    italic_angle: float

    @property
    def ps_subfamily(self) -> str:
        return self.subfamily.replace(" ", "")


# Ordered to match TTF filename conventions: Regular, Bold, Italic, BoldItalic.
STYLES: dict[str, StyleSpec] = {
    "Regular": StyleSpec("Regular", False, False, 0.0),
    "Bold": StyleSpec("Bold", True, False, 0.0),
    "Italic": StyleSpec("Italic", False, True, -10.0),
    "BoldItalic": StyleSpec("Bold Italic", True, True, -10.0),
}


# ---------------------------------------------------------------------------
# name table platforms
# ---------------------------------------------------------------------------

# (platformID, platEncID, langID) - records we always populate so the font
# is recognised correctly on both modern Windows/Linux and legacy macOS.
#   (3, 1, 0x409): Windows, Unicode BMP, en-US -- required by Windows
#   (1, 0,   0  ): Mac, Roman, English          -- legacy Mac compat
NAME_PLATFORMS: list[tuple[int, int, int]] = [
    (3, 1, 0x409),
    (1, 0, 0),
]


# ---------------------------------------------------------------------------
# name table helpers
# ---------------------------------------------------------------------------


def get_name(font, name_id: int) -> str | None:
    """Read a `name` record, preferring Windows then falling back to Mac.

    Returns the decoded string or `None` when the record is missing on both
    platforms.
    """
    rec = font["name"].getName(name_id, 3, 1, 0x409)
    if rec is None:
        rec = font["name"].getName(name_id, 1, 0, 0)
    return rec.toUnicode() if rec else None


def set_name_records(font, records: dict[int, str]) -> None:
    """Replace the listed `nameID -> string` records on every standard platform.

    Existing entries for each `(nameID, platform, encoding, language)` tuple
    are removed before the new value is written, keeping the rewrite
    deterministic and preventing stale source-font copyright lines or
    inherited PostScript names from leaking through.

    Records in `font["name"]` that are not listed here (in particular
    OpenType feature UI labels with nameID >= 256) are preserved.
    """
    name = font["name"]
    for plat, enc, lang in NAME_PLATFORMS:
        for name_id, value in records.items():
            if not value:
                continue
            name.removeNames(nameID=name_id, platformID=plat, platEncID=enc, langID=lang)
            name.setName(value, name_id, plat, enc, lang)


def style_from_psname(ps: str) -> tuple[bool, bool]:
    """Extract `(is_bold, is_italic)` from a PostScript subfamily token.

    Accepts the trailing portion of a PS name (e.g. `"BoldItalic"` from
    `"FiraPlexCode-BoldItalic"`). Match is case-insensitive.
    """
    s = ps.lower()
    return ("bold" in s, "italic" in s)


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------


def variant_compact_suffix(suffix: str) -> str:
    """Compact form of a variant suffix used in directory and TTF names.

    "Nerd Font Mono" -> "NerdFontMono". The compact form keeps cross-platform
    paths simple (no escaping of spaces) while the spaced form is what we
    embed in the human-readable `name` table records.
    """
    return suffix.replace(" ", "")
