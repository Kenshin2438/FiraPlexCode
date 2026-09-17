#!/usr/bin/env python3
"""Sanity-check produced TTFs.

Walks ``build/stage2/`` (or a provided directory) and verifies for every TTF:
    * File opens with fontTools without errors.
    * name table has nameID 1, 4, 6, 16, 17 populated for (3,1,0x409).
    * Family name matches expected pattern ``FiraPlexCode <suffix>``.
    * fsSelection / macStyle / italicAngle bits agree with style.
    * Ligature machinery is present in ALL four styles (GSUB ``calt`` feature
      plus FiraCode's signature ligature glyphs) - the italic styles get theirs
      grafted in Stage 1, and this check guards against Stage 2's FontForge
      pass dropping them.

Exit code 0 on success, 1 on any failure (details printed to stderr). Used as
a CI gate after Stage 2 so a corrupted build cannot reach packaging.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fontTools.ttLib import TTFont

from _common import (
    ROOT,
    STAGE2_DIR,
    get_name,
    load_config,
    make_logger,
    style_from_psname,
)

log = make_logger("verify")

# Signature glyphs of FiraCode's calt ligature machinery. Present in the
# upstream Regular/Bold and grafted into the italic styles by Stage 1.
LIGATURE_MARKER_GLYPHS = ("hyphen_hyphen.liga", "equal_equal.liga")


def verify_ligatures(font, errors: list[str]) -> None:
    """Check that the GSUB ``calt`` ligature machinery survived the pipeline."""
    if "GSUB" not in font:
        errors.append("missing GSUB table - ligatures cannot work")
        return
    gsub = font["GSUB"].table
    calt = [fr for fr in gsub.FeatureList.FeatureRecord if fr.FeatureTag == "calt"]
    if not calt:
        errors.append("GSUB has no 'calt' feature - ligatures cannot work")
        return
    if not any(fr.Feature.LookupListIndex for fr in calt):
        errors.append("'calt' feature has no lookups")
    glyph_names = set(font.getGlyphOrder())
    missing = [g for g in LIGATURE_MARKER_GLYPHS if g not in glyph_names]
    if missing:
        errors.append(f"missing ligature glyphs: {', '.join(missing)}")


def verify_one(ttf: Path, expected_family_prefix: str) -> list[str]:
    """Return a list of human-readable error strings for one TTF.

    Returning a list (rather than raising) lets the caller report every
    failing file in a single run, which is far more useful in CI than
    aborting on the first problem.
    """
    errors: list[str] = []
    try:
        font = TTFont(str(ttf))
    except Exception as e:
        return [f"failed to open: {e}"]

    family = get_name(font, 1)
    full = get_name(font, 4)
    psname = get_name(font, 6)
    typo_family = get_name(font, 16)
    typo_subfamily = get_name(font, 17)

    if not family or not family.startswith(expected_family_prefix):
        errors.append(f"name[1] family={family!r} does not start with {expected_family_prefix!r}")
    for nid, val, label in [
        (4, full, "full name"),
        (6, psname, "ps name"),
        (16, typo_family, "typo family"),
        (17, typo_subfamily, "typo subfamily"),
    ]:
        if not val:
            errors.append(f"missing name[{nid}] ({label})")

    if psname:
        is_bold, is_italic = style_from_psname(psname.split("-", 1)[-1])
        os2 = font["OS/2"]
        head = font["head"]
        post = font["post"]

        fs = os2.fsSelection
        ms = head.macStyle

        if is_italic and not (fs & (1 << 0)):
            errors.append(f"fsSelection italic bit not set ({fs:#06b})")
        if is_bold and not (fs & (1 << 5)):
            errors.append(f"fsSelection bold bit not set ({fs:#06b})")
        if not is_italic and not is_bold and not (fs & (1 << 6)):
            errors.append("fsSelection regular bit not set for upright")

        if is_italic and not (ms & (1 << 1)):
            errors.append(f"macStyle italic bit not set ({ms:#06b})")
        if is_bold and not (ms & (1 << 0)):
            errors.append(f"macStyle bold bit not set ({ms:#06b})")

        if is_italic and post.italicAngle == 0:
            errors.append("post.italicAngle == 0 for italic style")
        if not is_italic and post.italicAngle != 0:
            errors.append(f"post.italicAngle == {post.italicAngle} for upright")

    verify_ligatures(font, errors)

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        default=str(STAGE2_DIR),
        help="Directory tree of TTFs to verify.",
    )
    args = parser.parse_args()

    cfg = load_config()
    family = cfg["family_name"]

    target = Path(args.dir).resolve()
    ttfs = sorted(target.rglob("*.ttf"))
    if not ttfs:
        log(f"no TTFs found under {target}")
        return 1

    n_fail = 0
    for ttf in ttfs:
        errs = verify_one(ttf, family)
        if errs:
            n_fail += 1
            log(f"FAIL  {ttf.relative_to(ROOT)}")
            for e in errs:
                log(f"        - {e}")
        else:
            log(f"ok    {ttf.relative_to(ROOT)}")

    if n_fail:
        log(f"{n_fail} of {len(ttfs)} TTFs failed verification")
        return 1
    log(f"all {len(ttfs)} TTFs passed verification")
    return 0


if __name__ == "__main__":
    sys.exit(main())
