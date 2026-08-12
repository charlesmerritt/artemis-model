"""Tests for the viewer assembly step (viewer/serve_viewer.py)."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "viewer"))

import serve_viewer  # noqa: E402

MINIMAL_HTML = """<!doctype html>
<html>
  <head>
    <title>PERSEUS Map Viewer</title>
    <link rel="stylesheet" href="styles.css" />
  </head>
  <body>
    <div id="app"></div>
    <script src="app.js"></script>
  </body>
</html>
"""


def test_inject_adds_stylesheet_and_scripts():
    result = serve_viewer.inject_panel_assets(MINIMAL_HTML)

    assert 'href="artemis-panel.css"' in result
    assert 'src="artemis-panel-core.js"' in result
    assert 'src="artemis-panel.js"' in result


def test_inject_puts_scripts_after_the_viewer_bundle():
    # The panel reads window.AppState and window.Layers at load, so it must run last.
    result = serve_viewer.inject_panel_assets(MINIMAL_HTML)
    assert result.index('src="app.js"') < result.index('src="artemis-panel-core.js"')
    assert result.index('src="artemis-panel-core.js"') < result.index('src="artemis-panel.js"')


def test_inject_puts_stylesheet_in_head():
    result = serve_viewer.inject_panel_assets(MINIMAL_HTML)
    assert result.index('href="artemis-panel.css"') < result.index("</head>")


def test_inject_is_idempotent():
    once = serve_viewer.inject_panel_assets(MINIMAL_HTML)
    twice = serve_viewer.inject_panel_assets(once)

    assert once == twice
    assert twice.count('src="artemis-panel.js"') == 1


def test_inject_rejects_html_without_the_expected_structure():
    with pytest.raises(ValueError, match="not the expected viewer HTML"):
        serve_viewer.inject_panel_assets("<p>not a viewer</p>")


def test_panel_assets_exist_in_the_repo():
    # build() copies these by name; a rename here fails loudly rather than at serve time.
    for asset in serve_viewer.PANEL_ASSETS:
        assert (REPO_ROOT / "viewer" / asset).exists(), f"missing viewer/{asset}"


def test_build_assembles_viewer_panel_and_catalog(tmp_path):
    viewer_public = tmp_path / "public"
    viewer_public.mkdir()
    (viewer_public / "index.html").write_text(MINIMAL_HTML)
    (viewer_public / "app.js").write_text("// viewer")
    (viewer_public / "layers.json").write_text('{"layers": []}')

    catalog_dir = tmp_path / "catalog"
    (catalog_dir / "artemis").mkdir(parents=True)
    (catalog_dir / "layers.json").write_text('{"layers": [{"name": "generated"}]}')
    (catalog_dir / "artemis" / "catalog.json").write_text('{"name": "Test"}')

    build_dir = tmp_path / "build"
    serve_viewer.build(viewer_public, catalog_dir, build_dir)

    assert (build_dir / "app.js").exists()
    for asset in serve_viewer.PANEL_ASSETS:
        assert (build_dir / asset).exists()
    # The generated catalog replaces the viewer's stock one.
    assert "generated" in (build_dir / "layers.json").read_text()
    assert (build_dir / "artemis" / "catalog.json").exists()
    assert "artemis-panel.js" in (build_dir / "index.html").read_text()


def test_build_without_a_catalog_still_produces_a_usable_viewer(tmp_path):
    viewer_public = tmp_path / "public"
    viewer_public.mkdir()
    (viewer_public / "index.html").write_text(MINIMAL_HTML)

    build_dir = tmp_path / "build"
    serve_viewer.build(viewer_public, None, build_dir)

    assert (build_dir / "artemis-panel.js").exists()
    assert not (build_dir / "artemis").exists()


def test_build_replaces_a_previous_build(tmp_path):
    viewer_public = tmp_path / "public"
    viewer_public.mkdir()
    (viewer_public / "index.html").write_text(MINIMAL_HTML)

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    stale = build_dir / "stale.txt"
    stale.write_text("from a previous run")

    serve_viewer.build(viewer_public, None, build_dir)

    assert not stale.exists()
    assert (build_dir / "index.html").read_text().count('src="artemis-panel.js"') == 1
