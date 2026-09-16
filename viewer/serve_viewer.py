#!/usr/bin/env python3
"""
Assemble and serve the PERSEUS map viewer with the ARTEMIS panel overlaid.

The viewer (github.com/charlesmerritt/map-viewer) is a separate static app. Rather
than fork it or commit ARTEMIS files into it, this builds a throwaway copy: the
viewer's ``public/`` tree, plus this repo's ``viewer/`` panel assets, plus the
catalog written by ``pipeline.s5_imagery.viewer_catalog``, with two tags injected
into index.html. Nothing in the viewer checkout is modified.

If the panel should live in the viewer permanently, the same two tags plus the
three files in ``viewer/`` are the whole change — see viewer/README.md.

Usage
-----
    # Build and serve, cloning the viewer if needed
    uv run python viewer/serve_viewer.py

    # Point at a local viewer checkout and a specific catalog
    uv run python viewer/serve_viewer.py \
        --map-viewer ~/src/map-viewer \
        --catalog-dir data/interim/viewer/stands

    # Build only, no server
    uv run python viewer/serve_viewer.py --no-serve
"""

from __future__ import annotations

import argparse
import functools
import http.server
import logging
import os
import re
import shutil
import socketserver
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PANEL_DIR = Path(__file__).resolve().parent
REPO_ROOT = PANEL_DIR.parent

MAP_VIEWER_REPO = "https://github.com/charlesmerritt/map-viewer"
DEFAULT_VIEWER_CHECKOUT = REPO_ROOT / "data" / "interim" / "map-viewer"
DEFAULT_BUILD_DIR = REPO_ROOT / "data" / "interim" / "viewer_build"

PANEL_ASSETS = ("artemis-panel-core.js", "artemis-panel.js", "artemis-panel.css")

STYLE_TAGS = ('<link rel="stylesheet" href="artemis-panel.css" />',)
SCRIPT_TAGS = (
    '<script src="artemis-panel-core.js"></script>',
    '<script src="artemis-panel.js"></script>',
)


def _insert_before(html: str, closing_tag: str, tags: tuple[str, ...]) -> str:
    """
    Insert tags just before a closing tag, indented one level deeper than it.

    The indent is read off the closing tag rather than hardcoded, so the result
    matches whatever formatting the viewer's index.html happens to use.
    """
    match = re.search(rf"([ \t]*){re.escape(closing_tag)}", html)
    if match is None:
        raise ValueError(f"index.html has no {closing_tag} — not the expected viewer HTML")

    indent = match.group(1)
    inner = indent + "  "
    block = "".join(f"{inner}{tag}\n" for tag in tags)
    return html[: match.start()] + block + indent + closing_tag + html[match.end() :]


def inject_panel_assets(html: str) -> str:
    """
    Add the panel's stylesheet and scripts to the viewer's index.html.

    Idempotent: re-running over already-injected HTML returns it unchanged, so a
    rebuild over a warm build directory does not stack duplicate tags.

    The scripts go last, after the viewer's own bundle, because the panel reads
    window.AppState and window.Layers at load time.
    """
    if "artemis-panel.js" in html:
        return html

    html = _insert_before(html, "</head>", STYLE_TAGS)
    return _insert_before(html, "</body>", SCRIPT_TAGS)


def resolve_viewer_public(map_viewer: str | None, allow_clone: bool) -> Path:
    """Locate the viewer's public/ directory, cloning the repo if permitted."""
    candidates = []
    if map_viewer:
        candidates.append(Path(map_viewer).expanduser())
    if os.environ.get("MAP_VIEWER_DIR"):
        candidates.append(Path(os.environ["MAP_VIEWER_DIR"]).expanduser())
    candidates.append(DEFAULT_VIEWER_CHECKOUT)

    for candidate in candidates:
        # Accept either the repo root or the public directory itself.
        for public in (candidate / "public", candidate):
            if (public / "index.html").exists():
                return public.resolve()

    if not allow_clone:
        raise SystemExit(
            "Could not find the map viewer. Pass --map-viewer /path/to/map-viewer, set "
            "MAP_VIEWER_DIR, or drop --no-clone to fetch it."
        )

    logger.info("Cloning %s → %s", MAP_VIEWER_REPO, DEFAULT_VIEWER_CHECKOUT)
    DEFAULT_VIEWER_CHECKOUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", MAP_VIEWER_REPO, str(DEFAULT_VIEWER_CHECKOUT)],
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as err:
        raise SystemExit(
            f"Could not clone the map viewer ({err}). Clone it manually and pass --map-viewer."
        ) from err

    public = DEFAULT_VIEWER_CHECKOUT / "public"
    if not (public / "index.html").exists():
        raise SystemExit(f"Clone succeeded but {public}/index.html is missing.")
    return public.resolve()


def latest_catalog_dir() -> Path | None:
    """Most recently written catalog under data/interim/viewer/, if any."""
    root = REPO_ROOT / "data" / "interim" / "viewer"
    if not root.is_dir():
        return None
    candidates = [path.parent for path in root.glob("*/layers.json")]
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path / "layers.json").stat().st_mtime)


def build(viewer_public: Path, catalog_dir: Path | None, build_dir: Path) -> Path:
    """Assemble the servable tree. The build directory is rebuilt from scratch."""
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(viewer_public, build_dir)
    logger.info("Copied viewer from %s", viewer_public)

    missing = [asset for asset in PANEL_ASSETS if not (PANEL_DIR / asset).exists()]
    if missing:
        raise SystemExit(f"Missing panel assets in {PANEL_DIR}: {', '.join(missing)}")
    for asset in PANEL_ASSETS:
        shutil.copyfile(PANEL_DIR / asset, build_dir / asset)
    logger.info("Overlaid ARTEMIS panel assets")

    if catalog_dir is not None:
        layers_json = catalog_dir / "layers.json"
        if layers_json.exists():
            shutil.copyfile(layers_json, build_dir / "layers.json")
            logger.info("Using catalog %s", layers_json)
        overlay = catalog_dir / "artemis"
        if overlay.is_dir():
            shutil.copytree(overlay, build_dir / "artemis", dirs_exist_ok=True)
            logger.info("Copied panel catalog from %s", overlay)
        else:
            logger.warning("No artemis/ directory in %s — the panel will show setup help", catalog_dir)
    else:
        logger.warning(
            "No catalog directory found. Run pipeline.s5_imagery.viewer_catalog first; "
            "the panel will render setup instructions until then."
        )

    index = build_dir / "index.html"
    index.write_text(inject_panel_assets(index.read_text()))
    logger.info("Injected panel tags into index.html")
    return build_dir


def serve(build_dir: Path, port: int, bind: str) -> int:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(build_dir))

    class ReusableServer(socketserver.TCPServer):
        allow_reuse_address = True

    try:
        with ReusableServer((bind, port), handler) as httpd:
            logger.info("Serving %s at http://%s:%d/", build_dir, bind or "localhost", port)
            logger.info("Ctrl-C to stop")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                logger.info("Stopped")
    except OSError as err:
        raise SystemExit(f"Could not bind {bind}:{port} — {err}") from err
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Serve the PERSEUS map viewer with the ARTEMIS panel overlaid",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--map-viewer", help="Path to a map-viewer checkout (or its public/ dir)")
    parser.add_argument("--catalog-dir", help="Output directory from viewer_catalog.py")
    parser.add_argument("--build-dir", default=str(DEFAULT_BUILD_DIR))
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--no-clone", action="store_true", help="Never clone the viewer")
    parser.add_argument("--no-serve", action="store_true", help="Build only")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    viewer_public = resolve_viewer_public(args.map_viewer, allow_clone=not args.no_clone)

    if args.catalog_dir:
        catalog_dir = Path(args.catalog_dir).expanduser().resolve()
        if not catalog_dir.is_dir():
            raise SystemExit(f"Catalog directory not found: {catalog_dir}")
    else:
        catalog_dir = latest_catalog_dir()

    build_dir = build(viewer_public, catalog_dir, Path(args.build_dir).resolve())

    if args.no_serve:
        logger.info("Built %s (not serving: --no-serve)", build_dir)
        return 0
    return serve(build_dir, args.port, args.bind)


if __name__ == "__main__":
    sys.exit(main())
