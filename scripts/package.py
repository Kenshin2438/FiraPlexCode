#!/usr/bin/env python3
"""Stage 3 of the FiraPlexCode build pipeline.

Packages the final TTFs from ``build/stage2/<SuffixCompact>/`` into per-variant
zip archives ready for GitHub Release upload, plus a top-level
``FiraPlexCode-all-<version>.zip`` that bundles every variant in one file.

Each archive embeds ``LICENSES.md`` and ``README.md`` so downstream users see
the combined license terms of FiraCode (OFL), IBM Plex (OFL), and Nerd Fonts
(MIT) without having to chase separate sources.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

from _common import (
    DIST_DIR,
    ROOT,
    STAGE2_DIR,
    load_config,
    make_logger,
    variant_compact_suffix,
)

log = make_logger("pack")


def make_zip(archive_path: Path, files: list[Path], extras: list[Path]) -> None:
    """Write a deflate-compressed zip with the given TTFs and license/readme extras.

    Files are stored at the archive root (no nested directory) so end users can
    drop the contents straight into their OS font directory after extracting.
    """
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, arcname=f.name)
        for extra in extras:
            if extra.exists():
                zf.write(extra, arcname=extra.name)
    log(f"  wrote {archive_path.relative_to(ROOT)} ({archive_path.stat().st_size:,} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage2",
        default=str(STAGE2_DIR),
        help="Input directory containing per-variant subfolders of TTFs.",
    )
    parser.add_argument(
        "--out",
        default=str(DIST_DIR),
        help="Output directory for zip archives.",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Version tag to embed in archive filenames (default: read from config.json).",
    )
    args = parser.parse_args()

    cfg = load_config()
    family = cfg["family_name"]
    version = args.version or cfg["version"]

    stage2 = Path(args.stage2)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    extras = [
        ROOT / "LICENSES.md",
        ROOT / "README.md",
    ]

    all_ttfs: list[Path] = []
    for variant in cfg["variants"]:
        suffix_compact = variant_compact_suffix(variant["suffix"])
        variant_dir = stage2 / suffix_compact
        ttfs = sorted(variant_dir.glob("*.ttf"))
        if not ttfs:
            log(f"WARN no TTFs found in {variant_dir}, skipping")
            continue

        archive = out_dir / f"{family}-{suffix_compact}-{version}.zip"
        log(f"packing variant {variant['id']} -> {archive.name}")
        make_zip(archive, ttfs, extras)
        all_ttfs.extend(ttfs)

    if all_ttfs:
        archive = out_dir / f"{family}-all-{version}.zip"
        log(f"packing combined -> {archive.name}")
        make_zip(archive, all_ttfs, extras)

    log("Packaging complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
