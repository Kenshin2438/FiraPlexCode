#!/usr/bin/env python3
"""Stage 1 of the FiraPlexCode build pipeline.

Produces a coherent four-style RIBBI family (Regular, Bold, Italic,
BoldItalic) for ONE Nerd Font variant.

Inputs (per variant)
    * FiraCode Nerd Font Regular & Bold - already contain PUA icons and
      ligatures; we keep them as-is and only rewrite metadata.
    * IBM Plex Mono Italic & BoldItalic - no icons; provide the true
      italic shapes that the upstream FiraCode Italic does not have.

Outputs (per variant) into ``<out>/<variant_id>/``
    * FiraPlexCode-Regular.ttf
    * FiraPlexCode-Bold.ttf
    * FiraPlexCode-Italic.ttf
    * FiraPlexCode-BoldItalic.ttf

The Italic styles do NOT yet contain Nerd Font icons. Stage 2
(``patch_italics.py``) runs the official ``nerd-font-patcher`` (FontForge)
over them to inject the PUA icon ranges, producing the correct variant
flavour.

Key design decisions
    * No ``fontTools.merge.Merger``: we do per-style style replacement.
    * Plex Mono is upscaled from 1000 UPM to 1950 UPM via
      ``fontTools.ttLib.scaleUpem`` so all four styles share one
      coordinate system, then post-scale TT hinting is stripped because
      the original instructions are calibrated for the 1000 UPM space
      and produce visibly squashed glyphs at small sizes after rescale.
    * Vertical metrics are pinned to FiraCode NF's line box so Italic
      does not look smaller than Regular at the same point size.
    * Plex Mono ships without programming ligatures, so the italic styles
      would silently lose FiraCode's signature feature (e.g. in editor
      comments, which are usually italic). ``graft_ligatures`` transplants
      FiraCode NF's ``calt`` machinery into the italic fonts.
    * RIBBI 4-member family with nameIDs 1/2 (legacy) + 16/17
      (typographic), and ``fsSelection`` / ``macStyle`` / ``italicAngle``
      bits set explicitly per style.
"""

from __future__ import annotations

import argparse
import copy
import os
import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.ttLib.scaleUpem import scale_upem
from fontTools.ttLib.tables import otTables

from _common import (
    ROOT,
    SOURCES_DIR,
    STAGE1_DIR,
    STYLES,
    StyleSpec,
    load_config,
    make_logger,
    resolve_variants,
    set_name_records,
    variant_ids,
)

log = make_logger("build")

# ---------------------------------------------------------------------------
# Target metrics
# ---------------------------------------------------------------------------

# Match FiraCode Nerd Font v3.4.0. NF v3 raised the UPM to 1950 (and the
# monospace cell width to 1200) to retain icon precision; we follow suit
# so the patched icons drop in at full fidelity.
TARGET_UPM = 1950
TARGET_ADVANCE = 1200
PLEX_SOURCE_UPM = 1000

# Vertical metrics from FiraCode Nerd Font v3.4.0 (identical across the
# standard / mono / propo variants). Forcing the Italic styles to share
# this exact line box prevents the rendering engine from rescaling each
# style independently, which previously made Italic look ~6% taller and
# therefore visually smaller than the upright styles at the same point
# size.
TARGET_HHEA_ASCENT = 1800
TARGET_HHEA_DESCENT = -600
TARGET_HHEA_LINEGAP = 0
TARGET_TYPO_ASCENDER = 1800
TARGET_TYPO_DESCENDER = -600
TARGET_TYPO_LINEGAP = 0
TARGET_WIN_ASCENT = 1800
TARGET_WIN_DESCENT = 600
TARGET_CAP_HEIGHT = 1377
TARGET_X_HEIGHT = 1053

# Seconds between the TrueType ``head`` epoch (1904-01-01) and the Unix
# epoch (1970-01-01). Used when honouring ``SOURCE_DATE_EPOCH``.
TT_EPOCH_DELTA = 2_082_844_800

# Map from variant id (config-level) to the upstream FiraCode NF basename.
FIRACODE_BASENAMES = {
    "standard": "FiraCodeNerdFont",
    "mono": "FiraCodeNerdFontMono",
    "propo": "FiraCodeNerdFontPropo",
}


# ---------------------------------------------------------------------------
# name table rewriting
# ---------------------------------------------------------------------------


def rewrite_names(font: TTFont, family: str, style: StyleSpec, cfg: dict) -> None:
    """Replace the ``name`` records that identify the family/style.

    We deliberately do not wipe the entire table: FiraCode keeps OpenType
    feature UI labels (nameID >= 256, used by ss01..ss20 etc.) that we
    want to preserve. Only the IDs that describe the family or style are
    rewritten.
    """
    version = cfg["version"]
    vendor = cfg["vendor_id"]
    license_url = cfg["license_url"]

    full_name = f"{family} {style.subfamily}"
    ps_name = f"{family}-{style.ps_subfamily}"
    unique_id = f"{version};{vendor};{ps_name}"

    records = {
        0: cfg["copyright"],
        1: family,
        2: style.subfamily,
        3: unique_id,
        4: full_name,
        5: f"Version {version}",
        6: ps_name,
        8: cfg["manufacturer"],
        9: cfg["designer"],
        11: license_url,
        13: (
            "This font is a derivative of FiraCode (OFL-1.1), "
            "IBM Plex Mono (OFL-1.1), and Nerd Fonts (MIT). "
            "Redistribution must comply with all three licenses."
        ),
        14: license_url,
        16: family,  # Typographic family (RIBBI: same as nameID 1)
        17: style.subfamily,  # Typographic subfamily
    }
    set_name_records(font, records)


# ---------------------------------------------------------------------------
# Style bits: OS/2.fsSelection, head.macStyle, post.italicAngle
# ---------------------------------------------------------------------------


def set_style_bits(font: TTFont, style: StyleSpec, vendor_id: str, fixed_pitch: bool) -> None:
    """Populate the style-related fields in OS/2, head, and post."""
    os2 = font["OS/2"]
    head = font["head"]
    post = font["post"]

    # Bits 7 (USE_TYPO_METRICS), 8 (WWS) and 9 (OBLIQUE) require OS/2 table
    # version >= 4. Plex ships v3, so bump it before setting fsSelection.
    if os2.version < 4:
        os2.version = 4

    # OS/2.fsSelection bits:
    #   0 ITALIC, 5 BOLD, 6 REGULAR, 7 USE_TYPO_METRICS, 8 WWS
    fs = 0
    if style.is_italic:
        fs |= 1 << 0
    if style.is_bold:
        fs |= 1 << 5
    if not style.is_bold and not style.is_italic:
        fs |= 1 << 6
    fs |= 1 << 7
    fs |= 1 << 8
    os2.fsSelection = fs

    os2.usWeightClass = 700 if style.is_bold else 400
    os2.usWidthClass = 5
    os2.achVendID = vendor_id[:4].ljust(4)
    if hasattr(os2, "panose"):
        os2.panose.bFamilyType = 2  # Latin Text
        os2.panose.bProportion = 9  # Monospaced

    # head.macStyle bits: 0 BOLD, 1 ITALIC. Microsoft's spec requires this
    # to agree with fsSelection.
    ms = 0
    if style.is_bold:
        ms |= 1 << 0
    if style.is_italic:
        ms |= 1 << 1
    head.macStyle = ms

    post.italicAngle = float(style.italic_angle)
    post.isFixedPitch = 1 if fixed_pitch else 0


# ---------------------------------------------------------------------------
# advance width normalization for monospace consistency
# ---------------------------------------------------------------------------


def normalize_monospace_widths(font, target=TARGET_ADVANCE):
    """Re-center glyphs whose advance != target onto a target-wide cell by
    shifting LSB only (no geometric scaling). Returns count of adjustments.

    Skipped:
      * aw == 0   - combining marks must keep zero advance
      * aw > target - wider-than-cell glyphs (e.g. CJK double-width) would
                      clip if forced into a 600-unit cell. We log them and
                      leave them alone; if any are caught for italic Latin
                      glyphs, that is a sign the source needs investigation
                      rather than blind shifting.
    """
    hmtx = font["hmtx"]
    fixed = 0
    skipped_wide = 0
    for gname in font.getGlyphOrder():
        aw, lsb = hmtx[gname]
        if aw == target or aw == 0:
            continue
        if aw > target:
            skipped_wide += 1
            continue
        delta = (target - aw) // 2
        hmtx[gname] = (target, lsb + delta)
        fixed += 1
    if skipped_wide:
        log(f"  WARN {skipped_wide} glyphs wider than {target} units left untouched")
    return fixed


def assert_upm(font, expected=TARGET_UPM):
    upm = font["head"].unitsPerEm
    if upm != expected:
        raise RuntimeError(
            f"unitsPerEm={upm}, expected {expected}. Use fontTools.ttLib.scaleUpem before merging."
        )


# ---------------------------------------------------------------------------
# ligature grafting - FiraCode's calt machinery for the italic styles
# ---------------------------------------------------------------------------

# IBM Plex Mono ships without programming ligatures, so the italic styles
# would lose FiraCode's signature feature precisely where it matters most
# (editor comments are usually italic). We transplant the donor's ``calt``
# lookups into the italic font. Because both fonts use AGL glyph names for
# ASCII ("equal", "hyphen", ...), the copied rules bind by name to the
# italic outlines already in the target; only the donor-specific glyphs
# (``hyphen_hyphen.liga`` and friends) are appended. Ligature glyphs keep
# their upright shapes - arrows and operators are conventionally not
# slanted even in italic text.


def _walk_ot_tree(obj, visit, _seen: set[int] | None = None) -> None:
    """Depth-first walk over an otTables tree, calling ``visit`` on each node.

    Containers (list/tuple/dict) are descended into transparently; every other
    node is passed to ``visit`` first, and a True return prunes the subtree
    below it. ``_seen`` guards against shared or cyclic sub-objects.
    """
    if _seen is None:
        _seen = set()
    if id(obj) in _seen:
        return
    _seen.add(id(obj))
    if isinstance(obj, (list, tuple)):
        for v in obj:
            _walk_ot_tree(v, visit, _seen)
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            _walk_ot_tree(k, visit, _seen)
            _walk_ot_tree(v, visit, _seen)
        return
    if visit(obj):
        return
    if hasattr(obj, "__dict__"):
        for v in vars(obj).values():
            _walk_ot_tree(v, visit, _seen)


def _collect_glyph_refs(root, known: set[str], found: set[str]) -> None:
    """Collect every glyph name referenced anywhere inside an otTables tree."""

    def visit(node) -> bool:
        if isinstance(node, str) and node in known:
            found.add(node)
        return False

    _walk_ot_tree(root, visit)


def _sort_coverages(root, gid_of) -> None:
    """Re-sort every Coverage in a transplanted lookup by target glyph ID.

    The OpenType spec requires coverage tables sorted by glyph ID (shapers
    binary-search them). Donor coverages are ordered by donor GIDs, which
    scramble when the lookups land in the target's glyph order. Wherever a
    coverage has a parallel array (substitutes / rule sets), the parallel
    array is reordered alongside it; bare coverages (context format 3, or
    class-based format 2) are pure sets and are sorted independently.
    """

    def visit(node) -> bool:
        if isinstance(node, otTables.Coverage):
            node.glyphs = sorted(node.glyphs, key=gid_of)
            return True

        if isinstance(node, otTables.SingleSubst):
            if hasattr(node, "mapping"):
                # Programmatic form (no Coverage table yet; compile derives one
                # from mapping iteration order) - sort the mapping by target GID.
                node.mapping = dict(sorted(node.mapping.items(), key=lambda kv: gid_of(kv[0])))
                return True
            cov = node.Coverage
            if node.Format == 2:
                pairs = sorted(
                    zip(cov.glyphs, node.Substitute, strict=True), key=lambda p: gid_of(p[0])
                )
                cov.glyphs = [g for g, _ in pairs]
                node.Substitute = [s for _, s in pairs]
            else:  # Format 1 applies a constant delta - coverage order-independent.
                cov.glyphs = sorted(cov.glyphs, key=gid_of)
            return True

        if isinstance(node, otTables.LigatureSubst):
            pairs = sorted(
                zip(node.Coverage.glyphs, node.LigatureSet, strict=True),
                key=lambda p: gid_of(p[0]),
            )
            node.Coverage.glyphs = [g for g, _ in pairs]
            node.LigatureSet = [s for _, s in pairs]
            return True

        # Context / ChainContext format 1: coverage order parallels the rule sets.
        ruleset_attr = next(
            (
                a
                for a in ("ChainSubRuleSet", "SubRuleSet", "ChainPosRuleSet", "PosRuleSet")
                if hasattr(node, a)
            ),
            None,
        )
        if ruleset_attr and hasattr(node, "Coverage"):
            cov = node.Coverage
            rulesets = getattr(node, ruleset_attr)
            pairs = sorted(zip(cov.glyphs, rulesets, strict=True), key=lambda p: gid_of(p[0]))
            cov.glyphs = [g for g, _ in pairs]
            setattr(node, ruleset_attr, [r for _, r in pairs])
            return True

        return False

    _walk_ot_tree(root, visit)


def _walk_nested_index_records(root, fn) -> None:
    """Apply ``fn`` to every nested-lookup record (SubstLookupRecord etc.) in a lookup tree.

    Nested-lookup records are the only otTables nodes with an integer
    ``LookupListIndex`` attribute (a ``Feature``'s attribute of the same name
    is a list and is skipped). The records hold no glyph data themselves -
    their target lookups live in the font's LookupList - which is exactly why
    transplanted rules must have these indices rewritten.
    """

    def visit(node) -> bool:
        if isinstance(getattr(node, "LookupListIndex", None), int):
            fn(node)
        return False

    _walk_ot_tree(root, visit)


def _nested_lookup_indices(lookup) -> set[int]:
    """Donor LookupList indices referenced by this lookup's nested-lookup records."""
    found: set[int] = set()
    _walk_nested_index_records(lookup, lambda rec: found.add(rec.LookupListIndex))
    return found


def graft_ligatures(target: TTFont, donor_path: Path) -> tuple[int, int]:
    """Transplant the donor's ``calt`` ligature machinery into ``target``.

    Returns ``(glyphs_appended, lookups_added)``. Steps:

    1. Collect the donor's ``calt`` lookups plus the transitive closure of
       their nested-lookup references (chained-context rules invoke the actual
       substitutions through nested lookups), and deep-copy them.
    2. Append every donor glyph those lookups reference that the target lacks
       (the ``.liga`` / ``.seq`` / ``.spacer`` ligature machinery). Glyphs the
       two fonts share by name (all of ASCII) are NOT copied, so rules match
       the target's own italic outlines.
    3. Re-sort coverages by target glyph ID (spec requirement: shapers
       binary-search coverage tables).
    4. Append the lookups to the target's GSUB, rewrite nested lookup indices
       to their new positions, and register one ``calt`` feature on every
       script/language system.

    Requires the target to already be at the donor's UPM (scaleUpem first).
    """
    donor = TTFont(str(donor_path))
    donor_names = set(donor.getGlyphOrder())
    donor_gsub = donor["GSUB"].table
    donor_lookups = donor_gsub.LookupList.Lookup

    top_level = sorted(
        {
            i
            for fr in donor_gsub.FeatureList.FeatureRecord
            if fr.FeatureTag == "calt"
            for i in fr.Feature.LookupListIndex
        }
    )
    all_indices = set(top_level)
    frontier = list(top_level)
    while frontier:
        nested: set[int] = set()
        for i in frontier:
            nested |= _nested_lookup_indices(donor_lookups[i])
        frontier = sorted(nested - all_indices)
        all_indices |= nested

    ordered = sorted(all_indices)
    lookups = [copy.deepcopy(donor_lookups[i]) for i in ordered]
    remap = {old: pos for pos, old in enumerate(ordered)}

    refs: set[str] = set()
    for lk in lookups:
        _collect_glyph_refs(lk, donor_names, refs)

    target_names = set(target.getGlyphOrder())
    target_glyf, donor_glyf = target["glyf"], donor["glyf"]
    target_hmtx, donor_hmtx = target["hmtx"], donor["hmtx"]
    appended = 0
    for name in donor.getGlyphOrder():
        if name not in refs or name in target_names:
            continue
        # glyf.__setitem__ also appends to the glyph order, and hmtx is a
        # plain dict - no explicit setGlyphOrder needed.
        target_glyf[name] = copy.deepcopy(donor_glyf[name])
        target_hmtx[name] = donor_hmtx[name]
        appended += 1
    donor.close()
    # The reverse glyph map may have been cached (e.g. by scaleUpem) before
    # the new glyphs were appended; force a rebuild so GSUB compile finds them.
    gid_of = target.getReverseGlyphMap(rebuild=True).__getitem__

    for lk in lookups:
        _sort_coverages(lk, gid_of)

    target_gsub = target["GSUB"].table
    lookup_list = target_gsub.LookupList.Lookup
    base = len(lookup_list)
    lookup_list.extend(lookups)
    target_gsub.LookupList.LookupCount = len(lookup_list)
    for lk in lookups:
        _walk_nested_index_records(
            lk,
            lambda rec: setattr(rec, "LookupListIndex", base + remap[rec.LookupListIndex]),
        )

    feature = otTables.Feature()
    feature.FeatureParams = None
    feature.LookupListIndex = [base + remap[i] for i in top_level]
    feature.LookupCount = len(feature.LookupListIndex)
    record = otTables.FeatureRecord()
    record.FeatureTag = "calt"
    record.Feature = feature
    target_gsub.FeatureList.FeatureRecord.append(record)
    target_gsub.FeatureList.FeatureCount = len(target_gsub.FeatureList.FeatureRecord)
    feature_index = target_gsub.FeatureList.FeatureCount - 1
    for script_record in target_gsub.ScriptList.ScriptRecord:
        lang_systems = [script_record.Script.DefaultLangSys] + [
            lr.LangSys for lr in script_record.Script.LangSysRecord
        ]
        for lang_sys in lang_systems:
            if lang_sys is None:
                continue
            lang_sys.FeatureIndex.append(feature_index)
            lang_sys.FeatureCount = len(lang_sys.FeatureIndex)

    return appended, len(ordered)


# ---------------------------------------------------------------------------
# vertical metric alignment - keep all 4 styles on the same line box
# ---------------------------------------------------------------------------


def align_vertical_metrics(font):
    """Force hhea/OS-2 vertical metrics to match FiraCode NF so Italic
    glyphs are not visually compressed by a taller line box.

    Both engines (CSS/browsers, IDE editors, terminals) compute the
    rendered em size from the line box - either OS/2.usWinAscent +
    usWinDescent on Windows, or the typo metrics elsewhere when
    USE_TYPO_METRICS is set. If those numbers differ between Regular
    and Italic, the renderer rescales each style to fit, and the side
    with the larger line box ends up with smaller-looking glyphs.

    We pin all 8 fields to FiraCode's values, which is safe because
    Plex Italic glyphs sit comfortably inside that envelope (ascent
    1800 vs Plex's scaled 1813; descent -600 vs Plex's -722 - the
    only loss is ~122 units of descender headroom for tails on j/g/p
    which still fit because their actual yMin is well above -722).
    """
    hhea = font["hhea"]
    os2 = font["OS/2"]
    hhea.ascent = TARGET_HHEA_ASCENT
    hhea.descent = TARGET_HHEA_DESCENT
    hhea.lineGap = TARGET_HHEA_LINEGAP
    os2.sTypoAscender = TARGET_TYPO_ASCENDER
    os2.sTypoDescender = TARGET_TYPO_DESCENDER
    os2.sTypoLineGap = TARGET_TYPO_LINEGAP
    os2.usWinAscent = TARGET_WIN_ASCENT
    os2.usWinDescent = TARGET_WIN_DESCENT
    if hasattr(os2, "sCapHeight"):
        os2.sCapHeight = TARGET_CAP_HEIGHT
    if hasattr(os2, "sxHeight"):
        os2.sxHeight = TARGET_X_HEIGHT


# ---------------------------------------------------------------------------
# TrueType hinting removal - mandatory after scaleUpem
# ---------------------------------------------------------------------------


def strip_tt_hinting(font):
    """Remove TrueType hinting bytecode and tables.

    IBM Plex Mono Italic ships with TT instructions tuned for its
    original 1000 UPM coordinate space. After scale_upem(1000 -> 1950),
    those instructions point at obsolete CVT/control-value addresses
    and apply pixel snapping calibrated for the wrong em size. At small
    sizes (e.g. 16-20px which is typical for code editors) the freetype
    hinter follows those broken instructions and visibly squashes the
    glyph - Italic 'H' renders at roughly half the height of Regular 'H'
    despite identical glyf bbox. The fix is to drop hinting entirely
    and let the rasterizer fall back to grayscale anti-aliasing of the
    raw outlines, which looks correct at every size.

    We strip:
      * fpgm  - font program (executed once at font load)
      * prep  - control value program (executed at every PPEM change)
      * cvt   - control value table (referenced by hinting instructions)
      * gasp  - grid-fitting/scan-conversion thresholds (no longer needed)
      * Per-glyph TT instructions inside the glyf table.

    Hinting is a quality-vs-cleanness tradeoff: stripping it means we
    lose Plex's pixel-grid alignment, but on modern AA displays this
    is invisible while the alternative is the catastrophic "italic is
    half-size" bug we are fixing. FiraCode NF has its own correctly
    scaled hinting and is not stripped.
    """
    removed_tables = []
    for tag in ("fpgm", "prep", "cvt ", "gasp"):
        if tag in font:
            del font[tag]
            removed_tables.append(tag.strip())
    glyf = font["glyf"]
    n_dehinted = 0
    for gname in font.getGlyphOrder():
        g = glyf[gname]
        if hasattr(g, "program") and g.program.bytecode:
            g.removeHinting()
            n_dehinted += 1
    # maxp.maxSizeOfInstructions / maxStackElements / etc. become 0;
    # fontTools recompiles them automatically on save based on actual
    # contents, so we don't need to touch them explicitly.
    return removed_tables, n_dehinted


# ---------------------------------------------------------------------------
# per-style file production
# ---------------------------------------------------------------------------


def _apply_source_date_epoch(font: TTFont) -> None:
    """Pin head.created/modified to ``$SOURCE_DATE_EPOCH`` for reproducibility.

    No-op when the env var is unset. The ``head`` table stores timestamps
    as seconds since 1904-01-01, so we offset Unix epoch by TT_EPOCH_DELTA.
    """
    sde = os.environ.get("SOURCE_DATE_EPOCH")
    if not sde:
        return
    try:
        ts = int(sde)
    except ValueError:
        log(f"  WARN ignoring invalid SOURCE_DATE_EPOCH={sde!r}")
        return
    font["head"].created = ts + TT_EPOCH_DELTA
    font["head"].modified = ts + TT_EPOCH_DELTA


def build_one(
    src_ttf: Path,
    out_ttf: Path,
    style: StyleSpec,
    cfg: dict,
    *,
    normalize_widths: bool,
    fixed_pitch: bool,
    scale_to_target_upm: bool = False,
    align_metrics: bool = False,
    ligature_donor: Path | None = None,
) -> None:
    """Build one output TTF from one source TTF.

    ``scale_to_target_upm``, ``align_metrics``, and ``ligature_donor`` are only
    enabled for the Plex italic styles; FiraCode NF Regular/Bold are passed
    through with their original geometry, hinting, and ligatures intact.
    """
    log(f"build {style.subfamily:11s}  <- {src_ttf.name}")
    font = TTFont(str(src_ttf))

    if scale_to_target_upm and font["head"].unitsPerEm != TARGET_UPM:
        log(f"  scaleUpem {font['head'].unitsPerEm} -> {TARGET_UPM}")
        scale_upem(font, new_upem=TARGET_UPM)
        # Mandatory: hinting tuned for the original UPM is broken after
        # scaleUpem and causes severe glyph squashing at small sizes.
        removed, n_dehinted = strip_tt_hinting(font)
        log(f"  stripped TT hinting: tables={removed} glyphs_dehinted={n_dehinted}")

    assert_upm(font, TARGET_UPM)

    if normalize_widths:
        n = normalize_monospace_widths(font, TARGET_ADVANCE)
        if n:
            log(f"  normalized advance for {n} glyphs -> {TARGET_ADVANCE}")

    if ligature_donor is not None:
        n_glyphs, n_lookups = graft_ligatures(font, ligature_donor)
        log(
            f"  grafted calt ligatures from {ligature_donor.name}: {n_lookups} lookups, {n_glyphs} glyphs"
        )

    if align_metrics:
        align_vertical_metrics(font)
        log("  aligned vertical metrics to FiraCode NF line box")

    rewrite_names(font, family=cfg["family_name"], style=style, cfg=cfg)
    set_style_bits(font, style, cfg["vendor_id"], fixed_pitch)
    _apply_source_date_epoch(font)

    out_ttf.parent.mkdir(parents=True, exist_ok=True)
    font.save(str(out_ttf))
    log(f"  -> {out_ttf.relative_to(ROOT)}")


def build_variant(variant: dict, sources_dir: Path, out_dir: Path, cfg: dict) -> None:
    """Produce four TTFs for one variant (``standard`` / ``mono`` / ``propo``).

    Per-variant output subdirectory is created here so Stage 2 can patch
    the italics in place.
    """
    variant_id = variant["id"]
    log(f"=== variant: {variant_id} ===")
    fc_dir = sources_dir / "firacode-nerd" / variant_id
    plex_dir = sources_dir / "plex-mono"
    fc_basename = FIRACODE_BASENAMES[variant_id]
    fixed_pitch = variant["fixed_pitch"]

    family = cfg["family_name"]
    variant_out = out_dir / variant_id
    variant_out.mkdir(parents=True, exist_ok=True)

    # Regular + Bold: copy FiraCode NF directly. Icons, ligatures, and
    # correctly tuned 1950 UPM hinting are preserved untouched.
    for style_name in ("Regular", "Bold"):
        build_one(
            fc_dir / f"{fc_basename}-{style_name}.ttf",
            variant_out / f"{family}-{style_name}.ttf",
            STYLES[style_name],
            cfg,
            normalize_widths=False,
            fixed_pitch=fixed_pitch,
        )

    # Italic + BoldItalic: take the Plex Mono italic shapes; scale UPM up
    # to match FiraCode NF, normalise widths so any non-1200 advances are
    # re-centred, graft FiraCode's calt ligatures in (Plex has none), and
    # pin the vertical metrics to FiraCode's line box so italics do not look
    # squashed next to the upright styles. The ligature donor is the matching
    # FiraCode weight so BoldItalic ligatures get bold strokes. Stage 2
    # injects icons afterwards.
    plex_filenames = {
        "Italic": "IBMPlexMono-Italic.ttf",
        "BoldItalic": "IBMPlexMono-BoldItalic.ttf",
    }
    ligature_donors = {
        "Italic": fc_dir / f"{fc_basename}-Regular.ttf",
        "BoldItalic": fc_dir / f"{fc_basename}-Bold.ttf",
    }
    for style_name, plex_filename in plex_filenames.items():
        build_one(
            plex_dir / plex_filename,
            variant_out / f"{family}-{style_name}.ttf",
            STYLES[style_name],
            cfg,
            normalize_widths=True,
            fixed_pitch=fixed_pitch,
            scale_to_target_upm=True,
            align_metrics=True,
            ligature_donor=ligature_donors[style_name],
        )


def main() -> int:
    cfg = load_config()
    ids = variant_ids(cfg)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variant",
        choices=[*ids, "all"],
        default="all",
        help="Which Nerd Font variant to build.",
    )
    parser.add_argument(
        "--sources",
        default=str(SOURCES_DIR),
        help="Directory containing fetched source fonts.",
    )
    parser.add_argument(
        "--out",
        default=str(STAGE1_DIR),
        help="Output directory for Stage 1 TTFs.",
    )
    args = parser.parse_args()

    sources_dir = Path(args.sources)
    out_dir = Path(args.out)

    for variant in resolve_variants(cfg, args.variant):
        build_variant(variant, sources_dir, out_dir, cfg)

    log("Stage 1 complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
