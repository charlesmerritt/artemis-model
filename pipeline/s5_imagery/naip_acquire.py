"""
Acquire NAIP imagery that completely covers an imagery-extent vector layer.

Given a bounded area as a vector layer, this pulls NAIP (USDA/NAIP/DOQQ) for every
year named by the temporal parameter and, for each year, checks that the imagery
*actually covers the whole extent* before calling that year done.

That check is the point of this script. NAIP is flown state-by-state on a two- to
three-year cycle and delivered as quarter-quad tiles, so asking for "2021" over an
arbitrary polygon routinely returns imagery for part of it and nothing for the
rest. A mosaic with holes looks fine on a map and quietly corrupts anything
downstream that assumes full coverage.

Coverage handling is controlled by --coverage-mode:

    fill    (default) Fill holes from the nearest years within --fill-window,
            newest first, adding one year at a time until the extent is covered.
            Every contributing year is recorded in the manifest.
    strict  Fail if any requested year cannot cover the extent on its own.
    report  Record the coverage fraction and carry on with a partial mosaic.

Two vector layers are supported, and the distinction matters downstream (see
``vectors``): --extent is what imagery must cover, --aoi is the ground features
under study. Supplying only --aoi derives an extent from it.

Output is a manifest JSON plus GeoJSON copies of both layers, which
``viewer_catalog`` turns into map-viewer layers. Imagery itself either streams
straight to the viewer as Earth Engine tiles (--dest none) or is exported as COGs.

Note on grid alignment: NAIP is deliberately NOT snapped to the project's 30 m
TreeMap grid (gee/scripts/gee_utils.py). It is sub-meter reference imagery for
viewing and for context around embedding clusters, not a 30 m analysis raster, and
resampling it to 30 m would destroy the only thing it is good for.

Usage
-----
    # Stream three years to the viewer, no export
    uv run python -m pipeline.s5_imagery.naip_acquire \
        --extent config/study_extent.geojson --aoi config/stands.geojson \
        --years 2019,2021,2023 --dest none

    # Every other year, exported as COGs to Drive
    uv run python -m pipeline.s5_imagery.naip_acquire \
        --extent config/study_extent.geojson \
        --start-year 2015 --end-year 2023 --year-step 2 --dest drive

    # AOI only; extent derived as a 500 m buffer around it
    uv run python -m pipeline.s5_imagery.naip_acquire \
        --aoi config/stands.geojson --derive-extent buffer --buffer-m 500 \
        --all-available --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path
from typing import Any

from shapely.geometry.base import BaseGeometry

from pipeline.s5_imagery import vectors

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

NAIP_COLLECTION = "USDA/NAIP/DOQQ"

# NAIP's first acquisition year. Requests before this are a typo, not a query.
NAIP_FIRST_YEAR = 2003

# NAIP is 0.6-1 m. 1 m is the honest default: finer just interpolates.
DEFAULT_SCALE_M = 1.0

# Coverage is measured on a coarse grid — a 1 m coverage check over a county is
# billions of pixels to answer a yes/no question. 30 m matches the project grid.
DEFAULT_COVERAGE_SCALE_M = 30.0

# Below 1.0 to absorb rasterization edge effects at the extent boundary; a real
# hole in a NAIP mosaic is orders of magnitude larger than this slack.
DEFAULT_MIN_COVERAGE = 0.999

DEFAULT_FILL_WINDOW = 2

# Refuse exports beyond this without --force. 1e9 pixels x 3 bands is already a
# multi-gigabyte GeoTIFF and a very long-running Earth Engine task.
DEFAULT_MAX_EXPORT_PIXELS = 1_000_000_000

BAND_SETS = {"rgb": ["R", "G", "B"], "rgbn": ["R", "G", "B", "N"]}

COVERAGE_MODES = ("fill", "strict", "report")
EXPORT_TARGETS = ("none", "drive", "gcs")

MANIFEST_SCHEMA = "artemis.naip.manifest/1"


# ──────────────────────────────────────────────────────────────────────────────
# Temporal parameter
# ──────────────────────────────────────────────────────────────────────────────


def resolve_years(
    years: str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    step: int = 1,
) -> list[int]:
    """
    Resolve the temporal parameter into a sorted list of distinct years.

    Accepts either an explicit ``years`` list ("2019,2021,2023") or a
    start/end/step range. Supplying both is rejected rather than guessed at.
    """
    if years and (start_year is not None or end_year is not None):
        raise ValueError("Pass --years or --start-year/--end-year, not both")

    if years:
        parsed = []
        for token in str(years).replace(" ", "").split(","):
            if not token:
                continue
            try:
                parsed.append(int(token))
            except ValueError as err:
                raise ValueError(f"Not a year: {token!r}") from err
        resolved = parsed
    elif start_year is not None and end_year is not None:
        if step < 1:
            raise ValueError("--year-step must be >= 1")
        if end_year < start_year:
            raise ValueError("--end-year must be >= --start-year")
        resolved = list(range(start_year, end_year + 1, step))
    else:
        raise ValueError(
            "No temporal parameter. Pass --years, --start-year/--end-year, or --all-available."
        )

    if not resolved:
        raise ValueError("Temporal parameter resolved to no years")

    current_year = dt.date.today().year
    for year in resolved:
        if year < NAIP_FIRST_YEAR:
            raise ValueError(f"NAIP starts in {NAIP_FIRST_YEAR}; got {year}")
        if year > current_year:
            raise ValueError(f"Year {year} is in the future")

    return sorted(set(resolved))


def fill_candidate_years(
    target: int, window: int, available: list[int] | None = None
) -> list[int]:
    """
    Years to try, in order, when ``target`` alone does not cover the extent.

    Ordered by distance from the target, and for equal distance the newer year
    first — a hole filled with imagery one year newer is less misleading than one
    filled with imagery one year older, since the newer frame is closer to every
    subsequent year in the series.
    """
    if window < 0:
        raise ValueError("fill window must be >= 0")

    candidates = [
        year
        for year in range(target - window, target + window + 1)
        if year != target and year >= NAIP_FIRST_YEAR
    ]
    if available is not None:
        allowed = set(available)
        candidates = [year for year in candidates if year in allowed]

    return sorted(candidates, key=lambda year: (abs(year - target), -year))


def export_size_ok(
    pixel_count: int, band_count: int, max_pixels: int = DEFAULT_MAX_EXPORT_PIXELS
) -> bool:
    """Whether an export is under the size guard. Band count multiplies the cost."""
    return pixel_count * max(1, band_count) <= max_pixels


# ──────────────────────────────────────────────────────────────────────────────
# Earth Engine
# ──────────────────────────────────────────────────────────────────────────────


def _year_collection(ee, extent_ee, year: int, bands: list[str]):
    """NAIP images intersecting the extent in one calendar year."""
    return (
        ee.ImageCollection(NAIP_COLLECTION)
        .filterBounds(extent_ee)
        .filterDate(f"{year}-01-01", f"{year}-12-31")
        .select(bands)
    )


def available_naip_years(ee, extent_ee) -> list[int]:
    """
    Distinct NAIP years intersecting the extent.

    One server round-trip: the alternative is a coverage query per candidate year
    across NAIP's full 20-year span.
    """
    collection = ee.ImageCollection(NAIP_COLLECTION).filterBounds(extent_ee)
    years = collection.aggregate_array("system:time_start").map(
        lambda millis: ee.Date(millis).get("year")
    )
    return sorted({int(year) for year in ee.List(years).distinct().getInfo() or []})


def coverage_fraction(ee, image, extent_ee, scale_m: float) -> float:
    """
    Fraction of the extent where ``image`` has data.

    Unmasking to 0 first is what makes this a coverage measure: reduceRegion skips
    masked pixels, so a mean over the raw mask would report 1.0 for a mosaic that
    covers a single corner of the extent.
    """
    covered = image.select(0).mask().rename("covered").unmask(0)
    result = covered.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=extent_ee,
        scale=scale_m,
        maxPixels=1e10,
        bestEffort=True,
    ).get("covered")
    value = result.getInfo()
    return float(value) if value is not None else 0.0


def build_year_mosaic(
    ee,
    extent_ee,
    target_year: int,
    bands: list[str],
    coverage_mode: str,
    min_coverage: float,
    fill_window: int,
    coverage_scale_m: float,
    available_years: list[int] | None = None,
) -> dict[str, Any]:
    """
    Build the mosaic for one requested year and report how well it covers the extent.

    In "fill" mode neighbour years are added one at a time, re-measuring after each,
    so the manifest records the minimum set of years actually needed rather than
    everything inside the window. Later images win in ``mosaic()``, so the target
    year is placed last and always shows through where it has data.

    Returns a dict with the mosaic, coverage fraction, contributing years, and image
    count. Returns ``mosaic=None`` when no NAIP exists for the year at all.
    """
    collection = _year_collection(ee, extent_ee, target_year, bands)
    image_count = int(collection.size().getInfo())

    if image_count == 0:
        logger.warning("  %d: no NAIP images intersect the extent", target_year)
        base_result: dict[str, Any] = {
            "mosaic": None,
            "coverage": 0.0,
            "contributing_years": [],
            "image_count": 0,
        }
        if coverage_mode != "fill":
            return base_result
        mosaic = None
        coverage = 0.0
        contributing: list[int] = []
        stack: list[Any] = []
    else:
        mosaic = collection.mosaic()
        coverage = coverage_fraction(ee, mosaic, extent_ee, coverage_scale_m)
        contributing = [target_year]
        stack = [mosaic]
        logger.info(
            "  %d: %d images, %.4f of extent covered", target_year, image_count, coverage
        )

    if coverage >= min_coverage or coverage_mode != "fill":
        return {
            "mosaic": mosaic,
            "coverage": coverage,
            "contributing_years": contributing,
            "image_count": image_count,
        }

    for candidate in fill_candidate_years(target_year, fill_window, available_years):
        candidate_collection = _year_collection(ee, extent_ee, candidate, bands)
        candidate_count = int(candidate_collection.size().getInfo())
        if candidate_count == 0:
            continue

        # Earlier entries sit underneath, so fill years go in front of the stack and
        # the target year keeps priority wherever it has data.
        stack.insert(0, candidate_collection.mosaic())
        merged = ee.ImageCollection.fromImages(stack).mosaic()
        merged_coverage = coverage_fraction(ee, merged, extent_ee, coverage_scale_m)

        if merged_coverage <= coverage + 1e-9:
            # Contributed nothing; drop it so the manifest stays honest.
            stack.pop(0)
            continue

        contributing.append(candidate)
        image_count += candidate_count
        mosaic = merged
        coverage = merged_coverage
        logger.info(
            "    + %d: coverage now %.4f (%d images)", candidate, coverage, candidate_count
        )

        if coverage >= min_coverage:
            break

    return {
        "mosaic": mosaic,
        "coverage": coverage,
        "contributing_years": sorted(contributing),
        "image_count": image_count,
    }


def mosaic_tile_url(mosaic, bands: list[str]) -> str | None:
    """
    Earth Engine XYZ tile URL for immediate display in the viewer.

    These URLs are ephemeral — Earth Engine expires map IDs — so the manifest
    records when it was generated and the viewer surfaces that age. Exported COGs
    are the durable path.
    """
    visual = bands[:3] if len(bands) >= 3 else bands
    try:
        mapid = mosaic.select(visual).getMapId({"min": 0, "max": 255})
    except Exception as err:  # noqa: BLE001 — tile preview is best-effort
        logger.warning("  could not build tile URL: %s", err)
        return None
    fetcher = mapid.get("tile_fetcher")
    return fetcher.url_format if fetcher is not None else mapid.get("tile_url")


def submit_export(
    ee,
    mosaic,
    extent_ee,
    description: str,
    target: str,
    scale_m: float,
    crs: str,
    folder: str,
    bucket: str | None,
    prefix: str,
    dry_run: bool,
) -> dict[str, Any]:
    """Submit one COG export task and describe it for the manifest."""
    filename = f"{description}.tif"
    record: dict[str, Any] = {
        "target": target,
        "description": description,
        "filename": filename,
        "scale_m": scale_m,
        "crs": crs,
    }

    common = {
        "image": mosaic.clip(extent_ee),
        "description": description,
        "region": extent_ee,
        "scale": scale_m,
        "crs": crs,
        "maxPixels": 1e13,
        "fileFormat": "GeoTIFF",
        "formatOptions": {"cloudOptimized": True},
    }

    if target == "drive":
        record["folder"] = folder
        task = ee.batch.Export.image.toDrive(folder=folder, fileNamePrefix=description, **common)
    else:
        object_path = f"{prefix.rstrip('/')}/{description}" if prefix else description
        record["bucket"] = bucket
        record["object"] = f"{object_path}.tif"
        task = ee.batch.Export.image.toCloudStorage(
            bucket=bucket, fileNamePrefix=object_path, **common
        )

    if dry_run:
        record["task_id"] = None
        record["status"] = "not submitted (--dry-run)"
        logger.info("  [dry-run] would export %s", description)
        return record

    task.start()
    record["task_id"] = task.id
    record["status"] = "submitted"
    logger.info("  export submitted: %s (task %s)", description, task.id)
    return record


# ──────────────────────────────────────────────────────────────────────────────
# Manifest
# ──────────────────────────────────────────────────────────────────────────────


def build_manifest(
    name: str,
    slug: str,
    extent_geom: BaseGeometry,
    aoi_geom: BaseGeometry | None,
    extent_source: str,
    aoi_source: str | None,
    bands: list[str],
    scale_m: float,
    crs: str,
    coverage_settings: dict[str, Any],
    year_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the manifest. Pure: everything Earth Engine touched is already resolved."""
    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "generated_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "name": name,
        "slug": slug,
        "collection": NAIP_COLLECTION,
        "bands": list(bands),
        "scale_m": scale_m,
        "crs": crs,
        "coverage": dict(coverage_settings),
        "extent": {
            "source": extent_source,
            "geojson": "extent.geojson",
            "bounds": vectors.bounds_list(extent_geom),
            "area_ha": round(vectors.area_ha(extent_geom), 2),
        },
        "years": list(year_entries),
    }

    if aoi_geom is not None:
        manifest["aoi"] = {
            "source": aoi_source,
            "geojson": "aoi.geojson",
            "bounds": vectors.bounds_list(aoi_geom),
            "area_ha": round(vectors.area_ha(aoi_geom), 2),
            "containment_in_extent": round(
                vectors.containment_fraction(aoi_geom, extent_geom), 6
            ),
        }

    manifest["incomplete_years"] = [
        entry["year"] for entry in year_entries if not entry.get("complete")
    ]
    return manifest


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pull NAIP imagery covering an extent vector layer, one mosaic per year",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    layers = parser.add_argument_group("vector layers")
    layers.add_argument("--extent", help="Imagery-extent vector layer (what imagery must cover)")
    layers.add_argument("--extent-layer", help="Layer name within a multi-layer extent source")
    layers.add_argument("--aoi", help="Area-of-interest vector layer (features under study)")
    layers.add_argument("--aoi-layer", help="Layer name within a multi-layer AOI source")
    layers.add_argument(
        "--derive-extent",
        choices=vectors.EXTENT_MODES,
        default="bbox",
        help="How to derive the extent when only --aoi is given",
    )
    layers.add_argument(
        "--buffer-m",
        type=float,
        default=0.0,
        help="Buffer applied when deriving the extent from the AOI (meters)",
    )

    temporal = parser.add_argument_group("temporal parameter")
    temporal.add_argument("--years", help="Explicit years, comma separated (e.g. 2019,2021,2023)")
    temporal.add_argument("--start-year", type=int)
    temporal.add_argument("--end-year", type=int)
    temporal.add_argument("--year-step", type=int, default=1)
    temporal.add_argument(
        "--all-available",
        action="store_true",
        help="Use every NAIP year that intersects the extent",
    )

    coverage = parser.add_argument_group("coverage")
    coverage.add_argument("--coverage-mode", choices=COVERAGE_MODES, default="fill")
    coverage.add_argument("--min-coverage", type=float, default=DEFAULT_MIN_COVERAGE)
    coverage.add_argument("--fill-window", type=int, default=DEFAULT_FILL_WINDOW)
    coverage.add_argument("--coverage-scale", type=float, default=DEFAULT_COVERAGE_SCALE_M)

    imagery = parser.add_argument_group("imagery")
    imagery.add_argument("--bands", choices=sorted(BAND_SETS), default="rgb")
    imagery.add_argument("--scale", type=float, default=DEFAULT_SCALE_M, help="Export scale (m)")
    imagery.add_argument("--crs", default="EPSG:4326", help="Export CRS")

    export = parser.add_argument_group("export")
    export.add_argument(
        "--dest",
        choices=EXPORT_TARGETS,
        default="none",
        help="none streams Earth Engine tiles to the viewer without exporting",
    )
    export.add_argument("--folder", default="artemis_naip", help="Drive folder for --dest drive")
    export.add_argument("--gcs-bucket", help="Bucket for --dest gcs")
    export.add_argument("--gcs-prefix", default="naip", help="Object prefix for --dest gcs")
    export.add_argument("--max-export-pixels", type=int, default=DEFAULT_MAX_EXPORT_PIXELS)
    export.add_argument("--force", action="store_true", help="Export past the size guard")

    parser.add_argument("--name", help="Display name (defaults to the extent file stem)")
    parser.add_argument("--out-dir", help="Output directory (default data/interim/naip/<slug>)")
    parser.add_argument("--project", help="Earth Engine project ID")
    parser.add_argument("--dry-run", action="store_true", help="Resolve and report, submit nothing")
    return parser


def _load_layers(args) -> tuple[BaseGeometry, BaseGeometry | None, str, str | None]:
    """Resolve the extent and AOI geometries from the CLI arguments."""
    if not args.extent and not args.aoi:
        raise SystemExit("Pass --extent, --aoi, or both. See --help.")

    aoi_geom = None
    aoi_source = None
    if args.aoi:
        aoi_geom = vectors.layer_geometry(vectors.load_layer(args.aoi, args.aoi_layer))
        aoi_source = str(args.aoi)
        logger.info("AOI: %s (%.1f ha)", aoi_source, vectors.area_ha(aoi_geom))

    if args.extent:
        extent_geom = vectors.layer_geometry(vectors.load_layer(args.extent, args.extent_layer))
        extent_source = str(args.extent)
    else:
        extent_geom = vectors.derive_extent(aoi_geom, args.derive_extent, args.buffer_m)
        extent_source = f"derived from AOI (mode={args.derive_extent}, buffer={args.buffer_m} m)"

    logger.info("Extent: %s (%.1f ha)", extent_source, vectors.area_ha(extent_geom))

    if aoi_geom is not None:
        contained = vectors.containment_fraction(aoi_geom, extent_geom)
        if contained < 0.999:
            logger.warning(
                "AOI is only %.2f%% inside the imagery extent — %.2f%% of the study area "
                "will have no imagery and no embeddings.",
                contained * 100,
                (1 - contained) * 100,
            )

    return extent_geom, aoi_geom, extent_source, aoi_source


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    extent_geom, aoi_geom, extent_source, aoi_source = _load_layers(args)

    name = args.name or (Path(args.extent or args.aoi).stem)
    slug = vectors.slugify(name)
    out_dir = Path(args.out_dir) if args.out_dir else Path("data/interim/naip") / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    bands = BAND_SETS[args.bands]

    if args.dest == "gcs" and not args.gcs_bucket:
        raise SystemExit("--dest gcs requires --gcs-bucket")

    pixel_count = vectors.estimate_pixel_count(extent_geom, args.scale)
    logger.info(
        "Export estimate: ~%.1f Mpixel x %d bands at %.2f m",
        pixel_count / 1e6,
        len(bands),
        args.scale,
    )
    if args.dest != "none" and not export_size_ok(pixel_count, len(bands), args.max_export_pixels):
        message = (
            f"Export would be ~{pixel_count * len(bands) / 1e9:.1f} Gpixel, over the "
            f"--max-export-pixels guard of {args.max_export_pixels / 1e9:.1f} G. "
            "Coarsen --scale, shrink the extent, or pass --force."
        )
        if not args.force:
            raise SystemExit(message)
        logger.warning("%s (proceeding: --force)", message)

    import ee

    if args.project:
        ee.Initialize(project=args.project)
    else:
        ee.Initialize()

    extent_ee = vectors.to_ee_geometry(extent_geom)

    available_years = available_naip_years(ee, extent_ee)
    if not available_years:
        raise SystemExit("No NAIP imagery intersects this extent at all.")
    logger.info("NAIP years available over this extent: %s", available_years)

    if args.all_available:
        years = available_years
    else:
        try:
            years = resolve_years(args.years, args.start_year, args.end_year, args.year_step)
        except ValueError as err:
            raise SystemExit(str(err)) from err

    missing = [year for year in years if year not in available_years]
    if missing:
        logger.warning("Requested years with no NAIP over this extent: %s", missing)

    logger.info("Resolving %d year(s): %s", len(years), years)

    year_entries: list[dict[str, Any]] = []
    for year in years:
        result = build_year_mosaic(
            ee,
            extent_ee,
            year,
            bands,
            args.coverage_mode,
            args.min_coverage,
            args.fill_window,
            args.coverage_scale,
            available_years,
        )

        complete = result["coverage"] >= args.min_coverage
        entry: dict[str, Any] = {
            "year": year,
            "coverage": round(result["coverage"], 6),
            "complete": complete,
            "contributing_years": result["contributing_years"],
            "image_count": result["image_count"],
            "bounds": vectors.bounds_list(extent_geom),
            "tile_url": None,
            "tile_url_generated_utc": None,
            "export": None,
        }

        if result["mosaic"] is None:
            logger.warning("  %d: skipped, no imagery", year)
            year_entries.append(entry)
            continue

        if not complete:
            message = (
                f"{year}: only {result['coverage']:.4f} of the extent is covered "
                f"(minimum {args.min_coverage})"
            )
            if args.coverage_mode == "strict":
                raise SystemExit(
                    f"{message}. Widen --fill-window with --coverage-mode fill, or use "
                    "--coverage-mode report to accept partial mosaics."
                )
            logger.warning("  %s — keeping partial mosaic", message)

        tile_url = mosaic_tile_url(result["mosaic"], bands)
        if tile_url:
            entry["tile_url"] = tile_url
            entry["tile_url_generated_utc"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")

        if args.dest != "none":
            entry["export"] = submit_export(
                ee,
                result["mosaic"],
                extent_ee,
                f"naip_{slug}_{year}",
                args.dest,
                args.scale,
                args.crs,
                args.folder,
                args.gcs_bucket,
                args.gcs_prefix,
                args.dry_run,
            )

        year_entries.append(entry)

    manifest = build_manifest(
        name=name,
        slug=slug,
        extent_geom=extent_geom,
        aoi_geom=aoi_geom,
        extent_source=extent_source,
        aoi_source=aoi_source,
        bands=bands,
        scale_m=args.scale,
        crs=args.crs,
        coverage_settings={
            "mode": args.coverage_mode,
            "min_coverage": args.min_coverage,
            "fill_window": args.fill_window,
            "scale_m": args.coverage_scale,
        },
        year_entries=year_entries,
    )

    (out_dir / "extent.geojson").write_text(
        json.dumps(vectors.feature_collection(extent_geom, {"role": "imagery_extent"}), indent=2)
    )
    if aoi_geom is not None:
        (out_dir / "aoi.geojson").write_text(
            json.dumps(vectors.feature_collection(aoi_geom, {"role": "area_of_interest"}), indent=2)
        )

    manifest_path = out_dir / "naip_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    complete_count = sum(1 for entry in year_entries if entry["complete"])
    logger.info("─" * 60)
    logger.info("%d/%d years fully cover the extent", complete_count, len(year_entries))
    if manifest["incomplete_years"]:
        logger.warning("Incomplete years: %s", manifest["incomplete_years"])
    logger.info("Manifest: %s", manifest_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
