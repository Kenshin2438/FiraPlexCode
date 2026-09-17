#!/usr/bin/env python3
"""Stage 2 of the FiraPlexCode build pipeline.

Stage 1 produced four TTFs per variant under ``build/stage1/<variant>/``. Of
those, Regular and Bold already came from FiraCode Nerd Font (icons present);
the two italic styles came from IBM Plex Mono and have no icons. This stage
runs the official ``font-patcher`` (FontForge) over only those two files,
generating the variant-correct icon layout, then copies the four files into
``build/stage2/<SuffixCompact>/`` with their final FiraPlexCode names.

Per-variant patcher flags come from ``config.json``
(``variants[*].patcher_flags``):
    * standard : ``--complete``                          (icons at original widths)
    * mono     : ``--complete --mono``                   (force single-cell width)
    * propo    : ``--complete --mono``                   (see patcher flags note)

Common flags:
    ``--makegroups 1`` -- modern RIBBI grouping in Nerd Font naming.
    ``--careful``      -- do not overwrite existing glyphs in the source font.
    ``--no-progressbars``

We do NOT pass ``--has-no-italic`` because by the time we patch, the italic
files already declare themselves italic via fsSelection / macStyle from
Stage 1, and the input font name already encodes ``Italic`` / ``BoldItalic``
which the patcher uses for output naming.

Requires:
    * FontForge installed and on ``PATH`` (``fontforge -version`` works).
    * ``font-patcher`` script. Path can be provided via ``--patcher``,
      env ``FONT_PATCHER``, or auto-detected at the conventional locations.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from fontTools.ttLib import TTFont

from _common import (
    ROOT,
    STAGE1_DIR,
    STAGE2_DIR,
    STYLES,
    load_config,
    make_logger,
    resolve_variants,
    set_name_records,
    variant_compact_suffix,
    variant_ids,
)

log = make_logger("patch")

# ---------------------------------------------------------------------------
# Patcher flags
# ---------------------------------------------------------------------------

# Per-variant flags live in ``config.json`` (``variants[*].patcher_flags``) so
# builds can be tuned without touching code. NOTE: ``--complete`` is mutually
# exclusive with ``--variable-width-glyphs`` in font-patcher (the latter
# implies its own glyph-set selection). See ryanoasis/nerd-fonts
# ``font-patcher --help``.
#
# For propo italic specifically: the italic source (IBM Plex Mono Italic) is
# monospace, not proportional. There is no proportional Plex italic. Using
# ``--variable-width-glyphs`` alone yields only ~1600 cmap entries and drops
# the entire supplementary PUA range (U+F0000-U+FFFFD), making the italic
# visibly icon-poor compared to its Regular sibling. So config patches propo
# italic with the same ``--complete --mono`` flags as the mono variant (Plex
# italic IS monospace), giving it the full ~12k icon set. The Regular/Bold
# halves of propo are still the upstream proportional NF.
COMMON_FLAGS: list[str] = ["--makegroups", "1", "--careful", "--no-progressbars"]


# ---------------------------------------------------------------------------
# font-patcher discovery
# ---------------------------------------------------------------------------


def find_patcher(explicit: str | None) -> Path:
    """Locate the ``font-patcher`` script via CLI/env/well-known paths.

    Resolution order matters: an explicit ``--patcher`` always wins so CI can
    pin a known-good copy; ``FONT_PATCHER`` env is the second escape hatch
    for shells; the candidate list covers the vendored repo copy plus the
    conventional Docker / system install paths.
    """
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
        raise FileNotFoundError(f"--patcher not found: {explicit}")
    env = os.environ.get("FONT_PATCHER")
    if env:
        p = Path(env)
        if p.is_file():
            return p
    candidates = [
        ROOT / "tools" / "font-patcher" / "font-patcher",
        Path("/opt/font-patcher/font-patcher"),
        Path("/usr/local/share/font-patcher/font-patcher"),
        Path.home() / "font-patcher" / "font-patcher",
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(
        "font-patcher not found. Install nerd-fonts patcher and pass "
        "--patcher /path/to/font-patcher, or set FONT_PATCHER env var."
    )


# ---------------------------------------------------------------------------
# patcher invocation
# ---------------------------------------------------------------------------


def run_patcher(
    patcher: Path,
    ttf: Path,
    out_dir: Path,
    variant_flags: list[str],
) -> Path:
    """Patch one TTF and return the produced output file path.

    ``out_dir`` must be empty so we can identify the patcher's single output
    file unambiguously without relying on mtime (some filesystems have coarse
    timestamp resolution that can collide between adjacent invocations).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "fontforge",
        "-quiet",
        "-script",
        str(patcher),
        str(ttf),
        "--outputdir",
        str(out_dir),
        *variant_flags,
        *COMMON_FLAGS,
    ]
    log(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        log(proc.stdout)
        log(proc.stderr)
        raise RuntimeError(f"font-patcher failed for {ttf.name}")

    candidates = list(out_dir.glob("*.ttf"))
    if not candidates:
        raise RuntimeError(f"font-patcher produced no output for {ttf.name}")
    if len(candidates) > 1:
        raise RuntimeError(
            f"font-patcher produced multiple files in {out_dir} "
            f"({[p.name for p in candidates]}); pass an empty directory."
        )
    return candidates[0]


# ---------------------------------------------------------------------------
# name table rewriting (variant-aware)
# ---------------------------------------------------------------------------


def rewrite_variant_family(
    src_ttf: Path,
    dst_ttf: Path,
    full_family: str,
    style_key: str,
) -> None:
    """Rewrite the name table so the family includes the variant suffix.

    The patcher injects its own variant-flavoured name records, but we
    re-stamp our canonical names (matching Stage 1's record set) so all four
    files in a variant share a consistent identity for the OS font picker.

    ``style_key`` is one of ``Regular``/``Bold``/``Italic``/``BoldItalic``;
    the canonical subfamily strings come from the shared ``STYLES`` table so
    Stage 1 and Stage 2 cannot drift.
    """
    style = STYLES[style_key]
    font = TTFont(str(src_ttf))

    full_name = f"{full_family} {style.subfamily}"
    ps_name = f"{full_family.replace(' ', '')}-{style.ps_subfamily}"

    # Update only the records that depend on the family name; nameIDs not
    # listed (e.g. OpenType feature UI labels with nameID >= 256) are kept
    # intact by ``set_name_records``.
    set_name_records(
        font,
        {
            1: full_family,  # Family (legacy)
            3: ps_name,  # Unique ID
            4: full_name,  # Full name
            6: ps_name,  # PostScript name
            16: full_family,  # Typographic family
            17: style.subfamily,  # Typographic subfamily
        },
    )

    dst_ttf.parent.mkdir(parents=True, exist_ok=True)
    font.save(str(dst_ttf))


# ---------------------------------------------------------------------------
# per-variant orchestration
# ---------------------------------------------------------------------------


def patch_variant(
    variant: dict,
    stage1_dir: Path,
    stage2_dir: Path,
    family: str,
    patcher: Path,
) -> None:
    """Produce the four final TTFs for one Nerd Font variant.

    A ``try/finally`` wraps the temp directory so a patcher crash cannot
    leave ``_tmp_patch`` behind on disk between runs.
    """
    variant_id = variant["id"]
    suffix = variant["suffix"]
    patcher_flags = variant["patcher_flags"]
    log(f"=== variant: {variant_id} ({suffix}) ===")
    src_dir = stage1_dir / variant_id
    final_dir = stage2_dir / variant_compact_suffix(suffix)
    final_dir.mkdir(parents=True, exist_ok=True)

    # Compose final family name with variant suffix, e.g. "FiraPlexCode Nerd Font Mono".
    full_family = f"{family} {suffix}"
    family_compact = family.replace(" ", "") + variant_compact_suffix(suffix)

    # Use a fresh per-style subdirectory so we never have to disambiguate
    # between multiple patcher outputs by mtime (which can collide on
    # filesystems with coarse timestamp resolution).
    tmp_root = stage2_dir / "_tmp_patch" / variant_id
    if tmp_root.exists():
        shutil.rmtree(tmp_root)

    try:
        # Regular + Bold: already patched by FiraCode NF upstream. Just
        # rename them to incorporate the variant suffix in family/full names.
        for style in ("Regular", "Bold"):
            src = src_dir / f"{family}-{style}.ttf"
            if not src.exists():
                raise FileNotFoundError(src)
            dst = final_dir / f"{family_compact}-{style}.ttf"
            rewrite_variant_family(src, dst, full_family, style)

        # Italic + BoldItalic: run patcher to inject icons, then rewrite names.
        for style in ("Italic", "BoldItalic"):
            src = src_dir / f"{family}-{style}.ttf"
            if not src.exists():
                raise FileNotFoundError(src)
            style_tmp = tmp_root / style
            style_tmp.mkdir(parents=True, exist_ok=True)
            produced = run_patcher(patcher, src, style_tmp, patcher_flags)
            dst = final_dir / f"{family_compact}-{style}.ttf"
            rewrite_variant_family(produced, dst, full_family, style)
    finally:
        shutil.rmtree(tmp_root.parent, ignore_errors=True)


def main() -> int:
    cfg = load_config()
    ids = variant_ids(cfg)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variant",
        choices=[*ids, "all"],
        default="all",
    )
    parser.add_argument("--patcher", help="Path to nerd-fonts font-patcher script.")
    parser.add_argument(
        "--stage1",
        default=str(STAGE1_DIR),
        help="Stage 1 input directory (output of build_firaplex.py).",
    )
    parser.add_argument(
        "--out",
        default=str(STAGE2_DIR),
        help="Stage 2 output directory.",
    )
    args = parser.parse_args()

    family = cfg["family_name"]

    patcher = find_patcher(args.patcher)
    log(f"using font-patcher: {patcher}")

    stage1_dir = Path(args.stage1)
    stage2_dir = Path(args.out)

    chosen = resolve_variants(cfg, args.variant)

    for variant in chosen:
        patch_variant(
            variant,
            stage1_dir,
            stage2_dir,
            family,
            patcher,
        )

    log("Stage 2 complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
