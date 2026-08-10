"""
Turn NAIP and embedding outputs into something the PERSEUS map viewer can open.

The viewer (github.com/charlesmerritt/map-viewer) is a static MapLibre app with no
build step. It reads a built-in layer catalog from ``layers.json`` and otherwise
knows nothing about this project. This module writes that catalog, plus an
``artemis/catalog.json`` that the ARTEMIS side panel reads for the things the stock
viewer has no concept of: per-year NAIP coverage, Earth Engine tile URLs, and the
inside-versus-outside cluster charts.

Two ways imagery reaches the viewer, and they have different lifetimes:

  Earth Engine tile URLs  Work immediately, no export, no hosting. They expire —
                          Earth Engine map IDs are not permanent — so the catalog
                          records when each was minted and the panel shows its age.

  Exported COGs           Durable, but they have to be somewhere the browser can
                          reach with CORS enabled. Pass --public-base to say where
                          the exported files landed; GCS exports resolve on their
                          own.

Only COG-backed layers go into ``layers.json``, because a stale Earth Engine URL in
the viewer's permanent catalog is worse than no entry at all.

Usage
-----
    uv run python -m pipeline.s5_imagery.viewer_catalog \
        --naip-manifest data/interim/naip/stands/naip_manifest.json \
        --clusters data/interim/embeddings/stands/clusters.json

    uv run python -m pipeline.s5_imagery.viewer_catalog \
        --naip-manifest data/interim/naip/stands/naip_manifest.json \
        --public-base https://storage.googleapis.com/my-bucket/naip
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Bumped to /2 when the embeddings block became multi-run (one entry per clustering
# method) rather than a single flat clustering result.
CATALOG_SCHEMA = "artemis.viewer.catalog/2"

# Where the overlay files sit relative to the viewer's document root. The panel
# fetches "artemis/catalog.json", so this has to agree with artemis-panel.js.
OVERLAY_DIR = "artemis"

# Vector styling in the viewer is per-layer; these keep the two roles visually
# distinct without the user having to restyle them by hand.
EXTENT_STYLE = {"color": "#56b4e9", "fillOpacity": 0.05, "lineWidth": 2}
AOI_STYLE = {"color": "#e69f00", "fillOpacity": 0.12, "lineWidth": 2}


def resolve_cog_url(export: dict[str, Any] | None, public_base: str | None) -> str | None:
    """
    Public URL for an exported COG, or None when it cannot be known.

    GCS exports resolve themselves from bucket and object. Drive exports cannot —
    Drive has no stable public URL derivable from a task — so they need an explicit
    --public-base pointing at wherever the file was republished.
    """
    if not export:
        return None

    filename = export.get("filename")
    if not filename:
        return None

    if public_base:
        return f"{public_base.rstrip('/')}/{filename}"

    if export.get("target") == "gcs" and export.get("bucket") and export.get("object"):
        return f"https://storage.googleapis.com/{export['bucket']}/{export['object']}"

    return None


def build_naip_section(
    manifest: dict[str, Any], public_base: str | None
) -> dict[str, Any]:
    """Per-year NAIP entries for the panel, carrying coverage provenance through."""
    years = []
    for entry in manifest.get("years", []):
        cog_url = resolve_cog_url(entry.get("export"), public_base)
        years.append(
            {
                "year": entry["year"],
                "label": str(entry["year"]),
                "coverage": entry.get("coverage"),
                "complete": bool(entry.get("complete")),
                "contributing_years": entry.get("contributing_years", []),
                "image_count": entry.get("image_count"),
                "bounds": entry.get("bounds"),
                "tile_url": entry.get("tile_url"),
                "tile_url_generated_utc": entry.get("tile_url_generated_utc"),
                "cog_url": cog_url,
            }
        )

    return {
        "collection": manifest.get("collection"),
        "bands": manifest.get("bands", []),
        "scale_m": manifest.get("scale_m"),
        "generated_utc": manifest.get("generated_utc"),
        "coverage_settings": manifest.get("coverage", {}),
        "incomplete_years": manifest.get("incomplete_years", []),
        "years": years,
    }


def build_layers_json(
    manifest: dict[str, Any] | None,
    clusters: dict[str, Any] | None,
    public_base: str | None,
    cluster_base: str | None,
    name: str,
) -> dict[str, Any]:
    """
    Build the viewer's built-in catalog.

    Deliberately COG- and GeoJSON-only. Earth Engine tile URLs expire, and the
    viewer treats layers.json as durable configuration, so ephemeral URLs belong in
    the panel catalog where their age is visible instead.
    """
    layers: list[dict[str, Any]] = []

    if manifest is not None:
        timesteps = []
        for entry in manifest.get("years", []):
            url = resolve_cog_url(entry.get("export"), public_base)
            if url:
                timesteps.append({"label": str(entry["year"]), "url": url})

        if len(timesteps) > 1:
            layers.append(
                {
                    "name": f"NAIP imagery — {name}",
                    "description": f"{len(timesteps)} years, scrub with the time bar",
                    "type": "cog",
                    "times": timesteps,
                }
            )
        elif timesteps:
            layers.append(
                {
                    "name": f"NAIP imagery — {name} ({timesteps[0]['label']})",
                    "description": "Single year",
                    "type": "cog",
                    "url": timesteps[0]["url"],
                }
            )

    if clusters is not None:
        # One entry per clustering method that was exported. They are separate
        # rasters, not timesteps of one layer, so they get separate catalog entries
        # and can be toggled against each other on the map.
        for run in clusters.get("runs", []):
            cluster_url = resolve_cog_url(
                (run.get("layers") or {}).get("export"), cluster_base or public_base
            )
            if not cluster_url:
                continue
            k_observed = run.get("k_observed", 1)
            layers.append(
                {
                    "name": f"Clusters — {name} {clusters.get('year')} · {run.get('label')}",
                    "description": (
                        f"k={k_observed}"
                        f"{' (auto)' if run.get('auto_k') else ''} on {clusters.get('collection')}"
                    ),
                    "type": "cog",
                    "url": cluster_url,
                    "style": {"colormap": "viridis", "min": 0, "max": max(0, k_observed - 1)},
                }
            )

    layers.append(
        {
            "name": f"Imagery extent — {name}",
            "description": "Footprint the imagery must cover",
            "type": "geojson",
            "url": f"{OVERLAY_DIR}/extent.geojson",
            "style": EXTENT_STYLE,
        }
    )
    layers.append(
        {
            "name": f"Area of interest — {name}",
            "description": "Ground features under study",
            "type": "geojson",
            "url": f"{OVERLAY_DIR}/aoi.geojson",
            "style": AOI_STYLE,
        }
    )

    return {
        "_comment": (
            "Generated by pipeline/s5_imagery/viewer_catalog.py. Regenerate rather than "
            "hand-editing; local edits are overwritten on the next run."
        ),
        "_generated_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "layers": layers,
    }


def build_catalog(
    manifest: dict[str, Any] | None,
    clusters: dict[str, Any] | None,
    public_base: str | None,
    cluster_base: str | None,
    name: str,
) -> dict[str, Any]:
    """Build the ARTEMIS panel catalog — everything the stock viewer has no slot for."""
    catalog: dict[str, Any] = {
        "schema": CATALOG_SCHEMA,
        "generated_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "name": name,
        "vectors": {
            "extent": {
                "name": f"Imagery extent — {name}",
                "url": f"{OVERLAY_DIR}/extent.geojson",
                "style": EXTENT_STYLE,
            },
            "aoi": {
                "name": f"Area of interest — {name}",
                "url": f"{OVERLAY_DIR}/aoi.geojson",
                "style": AOI_STYLE,
            },
        },
        "naip": None,
        "embeddings": None,
    }

    if manifest is not None:
        catalog["naip"] = build_naip_section(manifest, public_base)
        extent = manifest.get("extent") or {}
        aoi = manifest.get("aoi") or {}
        catalog["vectors"]["extent"]["bounds"] = extent.get("bounds")
        catalog["vectors"]["extent"]["area_ha"] = extent.get("area_ha")
        if aoi:
            catalog["vectors"]["aoi"]["bounds"] = aoi.get("bounds")
            catalog["vectors"]["aoi"]["area_ha"] = aoi.get("area_ha")
            catalog["vectors"]["aoi"]["containment_in_extent"] = aoi.get("containment_in_extent")

    if clusters is not None:
        embeddings = dict(clusters)
        resolved_runs = []
        for run in embeddings.get("runs", []):
            resolved = dict(run)
            layer_info = dict(resolved.get("layers") or {})
            layer_info["cluster_cog_url"] = resolve_cog_url(
                layer_info.get("export"), cluster_base or public_base
            )
            resolved["layers"] = layer_info
            resolved_runs.append(resolved)
        embeddings["runs"] = resolved_runs
        catalog["embeddings"] = embeddings

        if catalog["vectors"]["extent"].get("bounds") is None:
            catalog["vectors"]["extent"]["bounds"] = (embeddings.get("extent") or {}).get("bounds")
        if catalog["vectors"]["aoi"].get("bounds") is None:
            catalog["vectors"]["aoi"]["bounds"] = (embeddings.get("aoi") or {}).get("bounds")

    return catalog


def _read_json(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    resolved = Path(path)
    if not resolved.exists():
        raise SystemExit(f"Not found: {resolved}")
    return json.loads(resolved.read_text())


def _copy_vector(sources: list[Path], filename: str, out_dir: Path) -> bool:
    """Copy the first available GeoJSON from the producing scripts' output dirs."""
    for source_dir in sources:
        candidate = source_dir / filename
        if candidate.exists():
            shutil.copyfile(candidate, out_dir / filename)
            return True
    return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build map-viewer catalog files from NAIP and embedding outputs",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--naip-manifest", help="naip_manifest.json from naip_acquire")
    parser.add_argument("--clusters", help="clusters.json from embeddings")
    parser.add_argument(
        "--public-base",
        help="Base URL where exported COGs are hosted (required for Drive exports)",
    )
    parser.add_argument(
        "--cluster-base",
        help="Base URL for the cluster COG, when it is hosted separately from NAIP",
    )
    parser.add_argument("--name", help="Display name (defaults to the manifest name)")
    parser.add_argument("--out-dir", help="Output directory (default data/interim/viewer/<slug>)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.naip_manifest and not args.clusters:
        raise SystemExit("Pass --naip-manifest, --clusters, or both.")

    manifest = _read_json(args.naip_manifest)
    clusters = _read_json(args.clusters)

    name = args.name or (manifest or clusters or {}).get("name") or "ARTEMIS"
    slug = (manifest or clusters or {}).get("slug") or "artemis"

    out_dir = Path(args.out_dir) if args.out_dir else Path("data/interim/viewer") / slug
    overlay_dir = out_dir / OVERLAY_DIR
    overlay_dir.mkdir(parents=True, exist_ok=True)

    source_dirs = [
        Path(path).parent for path in (args.naip_manifest, args.clusters) if path
    ]
    if not _copy_vector(source_dirs, "extent.geojson", overlay_dir):
        logger.warning("No extent.geojson found next to the inputs — the extent layer will 404.")
    if not _copy_vector(source_dirs, "aoi.geojson", overlay_dir):
        logger.warning("No aoi.geojson found next to the inputs — the AOI layer will 404.")

    layers_json = build_layers_json(manifest, clusters, args.public_base, args.cluster_base, name)
    catalog = build_catalog(manifest, clusters, args.public_base, args.cluster_base, name)

    (out_dir / "layers.json").write_text(json.dumps(layers_json, indent=2))
    (overlay_dir / "catalog.json").write_text(json.dumps(catalog, indent=2))

    cog_layers = sum(1 for layer in layers_json["layers"] if layer["type"] == "cog")
    if manifest and cog_layers == 0:
        logger.warning(
            "No COG-backed layers in layers.json. NAIP was not exported, or --public-base "
            "was not given, so imagery is available only as Earth Engine tiles in the panel."
        )

    logger.info("layers.json: %s (%d layers)", out_dir / "layers.json", len(layers_json["layers"]))
    logger.info("panel catalog: %s", overlay_dir / "catalog.json")
    logger.info("Serve with: uv run python viewer/serve_viewer.py --catalog-dir %s", out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
