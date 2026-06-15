#!/usr/bin/env python3
"""Fetch source fonts required for the FiraPlexCode build.

Downloads:
    * FiraCode Nerd Font (already-patched) Regular & Bold for Standard /
      Mono / Propo from ``ryanoasis/nerd-fonts`` releases.
    * IBM Plex Mono Italic & BoldItalic from ``IBM/plex`` releases.

Outputs into ``./sources/`` with a flat, predictable layout::

    sources/
      firacode-nerd/
        standard/
          FiraCodeNerdFont-Regular.ttf
          FiraCodeNerdFont-Bold.ttf
        mono/
          FiraCodeNerdFontMono-Regular.ttf
          FiraCodeNerdFontMono-Bold.ttf
        propo/
          FiraCodeNerdFontPropo-Regular.ttf
          FiraCodeNerdFontPropo-Bold.ttf
      plex-mono/
        IBMPlexMono-Italic.ttf
        IBMPlexMono-BoldItalic.ttf

Idempotent: existing files are not re-downloaded unless ``--force`` is passed.
Network access is required only on first run.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from _common import (
    CACHE_DIR,
    ROOT,
    SOURCES_DIR,
    load_config,
    make_logger,
)

log = make_logger("fetch")

# Optional GitHub mirror prefix for users behind firewalls / slow links.
# Set ``GH_MIRROR=https://gh-proxy.com`` (no trailing slash) and the script
# will rewrite GitHub URLs through that prefix transparently.
# Example mirrors that have worked: https://gh-proxy.com, https://ghproxy.com.
DEFAULT_MIRRORS: list[str | None] = [
    None,  # try direct first
    "https://gh-proxy.com",
    "https://mirror.ghproxy.com",
]

# Files we expect inside the FiraCode Nerd Font release zip, per variant.
FIRACODE_FILES: dict[str, list[str]] = {
    "standard": [
        "FiraCodeNerdFont-Regular.ttf",
        "FiraCodeNerdFont-Bold.ttf",
    ],
    "mono": [
        "FiraCodeNerdFontMono-Regular.ttf",
        "FiraCodeNerdFontMono-Bold.ttf",
    ],
    "propo": [
        "FiraCodeNerdFontPropo-Regular.ttf",
        "FiraCodeNerdFontPropo-Bold.ttf",
    ],
}

PLEX_FILES: list[str] = [
    "IBMPlexMono-Italic.ttf",
    "IBMPlexMono-BoldItalic.ttf",
]


def _candidate_urls(url: str) -> list[str]:
    """Build the ordered list of URLs to try for a single download.

    Honours ``$GH_MIRROR`` (single override) when set; otherwise tries direct
    GitHub first then falls back to known public mirrors. The fallback chain
    matters because ``objects.githubusercontent.com`` is unreachable from
    several networks where ``github.com`` itself works.
    """
    env_mirror = os.environ.get("GH_MIRROR", "").strip().rstrip("/")
    if env_mirror:
        # Explicit override: try mirror first, then direct as fallback.
        return [f"{env_mirror}/{url}", url]
    out: list[str] = []
    for prefix in DEFAULT_MIRRORS:
        out.append(url if prefix is None else f"{prefix}/{url}")
    return out


def download(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest``, trying every mirror candidate in turn.

    A partial file from a failed candidate is removed before we try the next
    URL, so a half-written zip can never be mistaken for a successful cache
    hit on the next run.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for candidate in _candidate_urls(url):
        log(f"GET  {candidate}")
        req = Request(candidate, headers={"User-Agent": "FiraPlexCode-fetch/1.0"})
        try:
            with urlopen(req, timeout=60) as resp, dest.open("wb") as out:
                shutil.copyfileobj(resp, out)
            log(f"  -> {dest.relative_to(ROOT)} ({dest.stat().st_size:,} bytes)")
            return
        except (URLError, TimeoutError, OSError) as exc:
            last_err = exc
            log(f"  ! failed: {exc}")
            if dest.exists():
                dest.unlink()
    raise RuntimeError(f"all download candidates failed for {url}: {last_err}")


def extract_from_zip(
    zip_path: Path,
    members: list[str],
    dest_dir: Path,
    archive_subdir: str = "",
) -> None:
    """Extract specific filenames from a zip into ``dest_dir`` (flat).

    ``archive_subdir`` is a substring filter on the zip member path, used to
    disambiguate when multiple subfolders inside the zip contain a file of
    the same basename (the IBM Plex archive nests by font family).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    wanted = set(members)
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            base = Path(name).name
            if base not in wanted:
                continue
            if archive_subdir and archive_subdir not in name:
                continue
            target = dest_dir / base
            with zf.open(name) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            log(f"  extracted {base}")
            wanted.discard(base)
    if wanted:
        raise RuntimeError(f"Missing files in {zip_path.name}: {sorted(wanted)}")


def fetch_firacode(cfg: dict, sources_dir: Path, cache_dir: Path, force: bool) -> None:
    """Download (if needed) and extract the FiraCode Nerd Font release zip."""
    version = cfg["sources"]["firacode_nerd_version"]
    url = cfg["sources"]["firacode_nerd_url_template"].format(version=version)
    zip_path = cache_dir / f"FiraCode-{version}.zip"

    if not zip_path.exists() or force:
        download(url, zip_path)
    else:
        log(f"cached {zip_path.relative_to(ROOT)}")

    for variant_id, files in FIRACODE_FILES.items():
        out_dir = sources_dir / "firacode-nerd" / variant_id
        if not force and all((out_dir / f).exists() for f in files):
            log(f"firacode/{variant_id} already extracted, skipping")
            continue
        extract_from_zip(zip_path, files, out_dir)


def fetch_plex(cfg: dict, sources_dir: Path, cache_dir: Path, force: bool) -> None:
    """Download (if needed) and extract the IBM Plex Mono italic TTFs."""
    version = cfg["sources"]["ibm_plex_version"]
    url = cfg["sources"]["ibm_plex_url_template"].format(version=version)
    archive_subdir = cfg["sources"]["ibm_plex_archive_path"]
    zip_path = cache_dir / f"IBMPlex-{version}.zip"

    if not zip_path.exists() or force:
        download(url, zip_path)
    else:
        log(f"cached {zip_path.relative_to(ROOT)}")

    out_dir = sources_dir / "plex-mono"
    if not force and all((out_dir / f).exists() for f in PLEX_FILES):
        log("plex-mono already extracted, skipping")
        return
    extract_from_zip(zip_path, PLEX_FILES, out_dir, archive_subdir=archive_subdir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if cached files exist.",
    )
    parser.add_argument(
        "--sources",
        default=str(SOURCES_DIR),
        help="Output directory for extracted source fonts.",
    )
    parser.add_argument(
        "--cache",
        default=str(CACHE_DIR),
        help="Cache directory for downloaded archives.",
    )
    parser.add_argument(
        "--firacode-version",
        default=None,
        help="Override FiraCode Nerd Font version from config.json.",
    )
    parser.add_argument(
        "--plex-version",
        default=None,
        help="Override IBM Plex version from config.json.",
    )
    args = parser.parse_args()

    cfg = load_config()
    if args.firacode_version:
        cfg["sources"]["firacode_nerd_version"] = args.firacode_version
    if args.plex_version:
        cfg["sources"]["ibm_plex_version"] = args.plex_version

    sources_dir = Path(args.sources)
    cache_dir = Path(args.cache)
    cache_dir.mkdir(exist_ok=True)
    sources_dir.mkdir(exist_ok=True)

    fetch_firacode(cfg, sources_dir, cache_dir, args.force)
    fetch_plex(cfg, sources_dir, cache_dir, args.force)

    log("All sources ready under ./sources/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
