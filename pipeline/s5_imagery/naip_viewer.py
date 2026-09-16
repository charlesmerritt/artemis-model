"""
Interactive NAIP time-slider with the AOI drawn over it, for notebook use.

The correction workflow asks a human to answer one question before any model
runs: *is this polygon really misclassified?* The evidence for that is what the
ground looked like in the years around the classification's vintage. This module
builds that view — a NAIP mosaic per year as the backsplash, the AOI on top as a
transparent polygon with a hatched border, and a slider that swaps the year
underneath it.

Three parts, deliberately separable:

``resolve_year_window``  Pure. Turns "±10 years around 2022" into the years that
                         can actually exist, with the warnings that go with it.
``hatch_ticks``          Pure. The cartographic hatching — tick marks angled off
                         the boundary into the polygon. Leaflet has no fill or
                         stroke pattern, so the hatching is real geometry.
``NaipYearSlider``       The widget. Imports geemap/ipywidgets lazily and builds
                         each year's mosaic on first view, because a year mosaic
                         costs Earth Engine round-trips and most sessions look at
                         three or four of the twenty.

Year mosaics come from :func:`pipeline.s5_imagery.naip_acquire.build_year_mosaic`,
so the coverage gap-filling and its provenance are the same here as in the
export path — the slider shows the mosaic you would get if you exported it, not
a prettier one.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, MultiLineString, Point
from shapely.geometry.base import BaseGeometry

from pipeline.s5_imagery import naip_acquire, vectors

logger = logging.getLogger(__name__)

DEFAULT_TARGET_YEAR = 2022
DEFAULT_WINDOW_YEARS = 10

# Transparent fill: the imagery is the evidence, the polygon only bounds it.
AOI_STYLE = {
    "color": "#ffd166",
    "weight": 2,
    "opacity": 1.0,
    "fillColor": "#ffd166",
    "fillOpacity": 0.08,
}

# The hatching is a separate layer of short lines, drawn thinner than the outline.
HATCH_STYLE = {"color": "#ffd166", "weight": 1.4, "opacity": 0.9, "fillOpacity": 0.0}

# Hatch geometry defaults, as fractions/metres on the ground.
DEFAULT_HATCH_ANGLE_DEG = 45.0
DEFAULT_HATCH_TICKS = 120
MIN_HATCH_SPACING_M = 5.0
MAX_HATCH_SPACING_M = 2_000.0
MAX_HATCH_TICKS = 5_000


# ──────────────────────────────────────────────────────────────────────────────
# Temporal window
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class YearWindow:
    """The years a ±N window around a target actually resolves to."""

    target: int
    requested: list[int]
    years: list[int]
    missing: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def before(self) -> list[int]:
        return [year for year in self.years if year < self.target]

    @property
    def after(self) -> list[int]:
        return [year for year in self.years if year > self.target]


def resolve_year_window(
    target_year: int = DEFAULT_TARGET_YEAR,
    back_years: int = DEFAULT_WINDOW_YEARS,
    forward_years: int = DEFAULT_WINDOW_YEARS,
    available: Sequence[int] | None = None,
    today_year: int | None = None,
) -> YearWindow:
    """
    Resolve a ±N-year window around a target into years that can exist.

    The forward half is the interesting one: a 2022 target with a ±10 window asks
    for imagery through 2032, and most of that has not been flown yet. Rather than
    silently trimming, the trim is reported — a window that comes back with two
    years after the target instead of ten changes how much weight the "after"
    comparison can carry, and the reader should know that before looking at it.

    ``available`` is the set of years NAIP actually holds over the extent (from
    :func:`naip_acquire.available_naip_years`). Without it the window is only
    clipped to NAIP's first year and to today.
    """
    if back_years < 0 or forward_years < 0:
        raise ValueError("back_years and forward_years must be >= 0")

    today_year = today_year if today_year is not None else _current_year()
    requested = list(range(target_year - back_years, target_year + forward_years + 1))
    warnings: list[str] = []

    before_naip = [year for year in requested if year < naip_acquire.NAIP_FIRST_YEAR]
    if before_naip:
        warnings.append(
            f"{len(before_naip)} requested year(s) predate NAIP "
            f"({naip_acquire.NAIP_FIRST_YEAR}): {before_naip[0]}–{before_naip[-1]}"
        )

    future = [year for year in requested if year > today_year]
    if future:
        warnings.append(
            f"{len(future)} requested year(s) are in the future ({future[0]}–{future[-1]}); "
            f"a ±{forward_years}-year window around {target_year} cannot be symmetric today."
        )

    feasible = [
        year
        for year in requested
        if naip_acquire.NAIP_FIRST_YEAR <= year <= today_year
    ]

    if available is None:
        years = feasible
        missing: list[int] = []
    else:
        offered = set(int(year) for year in available)
        years = [year for year in feasible if year in offered]
        missing = [year for year in feasible if year not in offered]
        if missing:
            warnings.append(
                f"No NAIP over this extent for {len(missing)} feasible year(s): {missing}"
            )

    if not years:
        raise ValueError(
            f"No usable NAIP years for a ±{back_years}/{forward_years} window around "
            f"{target_year}. Widen the window or check that the extent is over CONUS."
        )

    after = [year for year in years if year > target_year]
    if len(after) < forward_years:
        warnings.append(
            f"Only {len(after)} year(s) available after {target_year} "
            f"(asked for {forward_years}): {after or 'none'}. The post-target comparison "
            "rests on that much imagery and no more."
        )
    if target_year not in years:
        warnings.append(
            f"The target year {target_year} itself has no NAIP over this extent; the "
            "nearest available years are the reference."
        )

    for message in warnings:
        logger.warning(message)

    return YearWindow(
        target=target_year,
        requested=requested,
        years=years,
        missing=missing,
        warnings=warnings,
    )


def _current_year() -> int:
    import datetime as dt

    return dt.date.today().year


# ──────────────────────────────────────────────────────────────────────────────
# Hatched border
# ──────────────────────────────────────────────────────────────────────────────


def hatch_ticks(
    geom_wgs84: BaseGeometry,
    spacing_m: float | None = None,
    length_m: float | None = None,
    angle_deg: float = DEFAULT_HATCH_ANGLE_DEG,
) -> MultiLineString:
    """
    Tick marks angled off the polygon boundary, pointing inward — a hatched border.

    Leaflet styles strokes with a colour, a width and a dash pattern, and nothing
    else; there is no hatch pattern to ask for. So the hatching is drawn as
    geometry: walk the boundary at ``spacing_m`` intervals, and at each station
    emit a short segment rotated ``angle_deg`` off the local tangent, flipped if
    needed so it falls inside the polygon rather than outside it.

    Both distances default to the polygon's own size (``spacing_m`` from the
    perimeter, ``length_m`` from the spacing), so the hatching looks the same on a
    two-hectare stand and a whole county.

    Geometry in and out is EPSG:4326; the walk itself runs in the project's
    equal-area CRS, because a tick spaced in degrees is a different length at the
    top of the polygon than at the bottom.
    """
    if angle_deg % 180 == 0:
        raise ValueError("angle_deg must not be a multiple of 180 — ticks would lie on the boundary")

    projected = vectors.to_equal_area(geom_wgs84)
    boundary = projected.boundary
    lines = list(getattr(boundary, "geoms", [boundary]))
    perimeter = sum(line.length for line in lines)
    if perimeter <= 0:
        raise ValueError("Geometry has no boundary to hatch")

    if spacing_m is None:
        spacing_m = float(np.clip(perimeter / DEFAULT_HATCH_TICKS, MIN_HATCH_SPACING_M, MAX_HATCH_SPACING_M))
    if spacing_m <= 0:
        raise ValueError("spacing_m must be > 0")
    if length_m is None:
        length_m = spacing_m * 0.75
    if length_m <= 0:
        raise ValueError("length_m must be > 0")

    expected = int(perimeter / spacing_m) + len(lines)
    if expected > MAX_HATCH_TICKS:
        raise ValueError(
            f"{expected:,} ticks at {spacing_m:g} m spacing over a {perimeter:,.0f} m "
            f"perimeter, over the {MAX_HATCH_TICKS:,} cap. Increase spacing_m."
        )

    angle = np.deg2rad(angle_deg)
    cos_a, sin_a = float(np.cos(angle)), float(np.sin(angle))
    ticks: list[LineString] = []

    for line in lines:
        if line.length <= 0:
            continue
        # A closed ring's start and end are the same point; stop one step short so
        # the first tick is not drawn twice.
        stations = np.arange(0.0, line.length, spacing_m)
        for distance in stations:
            station = line.interpolate(float(distance))
            tangent = _tangent_at(line, float(distance))
            if tangent is None:
                continue
            tx, ty = tangent
            # Rotate the unit tangent by angle_deg to get the tick direction.
            dx = tx * cos_a - ty * sin_a
            dy = tx * sin_a + ty * cos_a

            end_x, end_y = station.x + dx * length_m, station.y + dy * length_m
            probe_x = station.x + dx * min(length_m, spacing_m) * 0.5
            probe_y = station.y + dy * min(length_m, spacing_m) * 0.5
            if not projected.contains(Point(probe_x, probe_y)):
                end_x, end_y = station.x - dx * length_m, station.y - dy * length_m

            ticks.append(LineString([(station.x, station.y), (end_x, end_y)]))

    if not ticks:
        raise ValueError("Hatching produced no ticks — spacing_m is larger than the perimeter")

    hatched = MultiLineString(ticks)
    return gpd.GeoSeries([hatched], crs=vectors.EQUAL_AREA_CRS).to_crs(vectors.WGS84).iloc[0]


def _tangent_at(line: LineString, distance: float, delta: float = 0.5) -> tuple[float, float] | None:
    """Unit tangent of ``line`` at ``distance`` along it, or None where it degenerates."""
    before = line.interpolate(max(distance - delta, 0.0))
    after = line.interpolate(min(distance + delta, line.length))
    dx, dy = after.x - before.x, after.y - before.y
    norm = float(np.hypot(dx, dy))
    if norm == 0:
        return None
    return dx / norm, dy / norm


def fit_zoom(
    bounds: tuple[float, float, float, float],
    map_px: int = 800,
    tile_px: int = 256,
    min_zoom: int = 3,
    max_zoom: int = 18,
) -> int:
    """
    A web-map zoom level at which ``bounds`` fits in a ``map_px``-wide viewport.

    Computed rather than delegated to ipyleaflet's ``fit_bounds``, which schedules
    an asyncio task and therefore raises outside a running event loop. That makes
    it work in a Jupyter kernel and fail in a plain script or a test — a dependency
    the map has no reason to carry when the arithmetic is four lines.
    """
    minx, miny, maxx, maxy = bounds
    tiles = map_px / tile_px
    zoom_lon = math.log2(tiles * 360.0 / max(maxx - minx, 1e-9))
    zoom_lat = math.log2(tiles * 180.0 / max(maxy - miny, 1e-9))
    return int(max(min_zoom, min(max_zoom, math.floor(min(zoom_lon, zoom_lat)))))


def hatched_aoi_layers(
    geom_wgs84: BaseGeometry,
    spacing_m: float | None = None,
    length_m: float | None = None,
    angle_deg: float = DEFAULT_HATCH_ANGLE_DEG,
) -> dict[str, dict[str, Any]]:
    """
    The two GeoJSON layers that draw the AOI: transparent fill, then hatch ticks.

    Returned as plain GeoJSON so this is usable from any map library — the widget
    below is one consumer, ``viewer_catalog`` could be another.
    """
    return {
        "aoi": {
            "geojson": vectors.feature_collection(geom_wgs84, {"role": "area_of_interest"}),
            "style": dict(AOI_STYLE),
        },
        "hatch": {
            "geojson": vectors.feature_collection(
                hatch_ticks(geom_wgs84, spacing_m, length_m, angle_deg), {"role": "aoi_hatch"}
            ),
            "style": dict(HATCH_STYLE),
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# The widget
# ──────────────────────────────────────────────────────────────────────────────


class NaipYearSlider:
    """
    A geemap map whose NAIP backsplash is chosen by a year slider.

    Layers are built on demand and kept: moving the slider to a year for the first
    time costs the Earth Engine calls that mosaic and coverage-check it, and
    moving back to it costs nothing. That is why every year of a twenty-year
    window can be offered without twenty mosaics being built up front.

    Earth Engine must already be initialized — this class does not authenticate,
    because a widget constructor is the wrong place to open a browser tab.
    """

    AOI_LAYER = "AOI"
    HATCH_LAYER = "AOI hatch"

    def __init__(
        self,
        ee,
        aoi_wgs84: BaseGeometry,
        years: Sequence[int],
        target_year: int | None = None,
        bands: str = "rgb",
        coverage_mode: str = "fill",
        min_coverage: float = naip_acquire.DEFAULT_MIN_COVERAGE,
        fill_window: int = naip_acquire.DEFAULT_FILL_WINDOW,
        coverage_scale_m: float = naip_acquire.DEFAULT_COVERAGE_SCALE_M,
        available_years: Sequence[int] | None = None,
        height: str = "600px",
        hatch_spacing_m: float | None = None,
        hatch_length_m: float | None = None,
    ) -> None:
        import geemap

        self._ee = ee
        self.aoi = aoi_wgs84
        self.years = sorted({int(year) for year in years})
        if not self.years:
            raise ValueError("NaipYearSlider needs at least one year")
        self.target_year = int(target_year) if target_year is not None else self.years[0]
        self.bands = naip_acquire.BAND_SETS[bands]
        self.coverage_mode = coverage_mode
        self.min_coverage = min_coverage
        self.fill_window = fill_window
        self.coverage_scale_m = coverage_scale_m
        self.available_years = list(available_years) if available_years is not None else None
        self._mosaics: dict[int, dict[str, Any]] = {}

        self.extent_ee = vectors.to_ee_geometry(aoi_wgs84)
        centroid = aoi_wgs84.centroid
        self.map = geemap.Map(
            center=(centroid.y, centroid.x),
            zoom=fit_zoom(aoi_wgs84.bounds),
            height=height,
        )
        self.map.add_basemap("SATELLITE")
        self._add_aoi_layers(hatch_spacing_m, hatch_length_m)

    # -- layers ---------------------------------------------------------------

    def _add_aoi_layers(self, spacing_m: float | None, length_m: float | None) -> None:
        layers = hatched_aoi_layers(self.aoi, spacing_m, length_m)
        # Hatching first, so the outline draws over its own ticks.
        self.map.add_geojson(
            layers["hatch"]["geojson"],
            layer_name=self.HATCH_LAYER,
            style=layers["hatch"]["style"],
        )
        self.map.add_geojson(
            layers["aoi"]["geojson"], layer_name=self.AOI_LAYER, style=layers["aoi"]["style"]
        )

    def layer_name(self, year: int) -> str:
        return f"NAIP {year}"

    def mosaic_for(self, year: int) -> dict[str, Any]:
        """Build (and cache) the coverage-filled mosaic for one year."""
        if year not in self._mosaics:
            self._mosaics[year] = naip_acquire.build_year_mosaic(
                self._ee,
                self.extent_ee,
                year,
                self.bands,
                self.coverage_mode,
                self.min_coverage,
                self.fill_window,
                self.coverage_scale_m,
                self.available_years,
            )
        return self._mosaics[year]

    def _ensure_layer(self, year: int) -> bool:
        """Add this year's tile layer if it is not on the map yet. False when there is no imagery."""
        name = self.layer_name(year)
        if self.map.find_layer(name) is not None:
            return True

        result = self.mosaic_for(year)
        if result["mosaic"] is None:
            return False

        visual = self.bands[:3]
        self.map.addLayer(
            result["mosaic"].select(visual), {"min": 0, "max": 255}, name, False
        )
        # A newly added Earth Engine layer goes on top of the AOI vectors; move the
        # vectors back up so the polygon is never buried by the backsplash.
        self._raise_aoi_layers()
        return True

    def _raise_aoi_layers(self) -> None:
        """Move the AOI vectors back to the top of the draw order.

        Reorders the layer tuple rather than removing and re-adding the layers.
        ``remove_layer`` closes a layer's widget comm, so the re-add then fails on
        a dead handle — which is how the AOI vanished under the backsplash the
        first time a new year was selected.
        """
        order = (self.HATCH_LAYER, self.AOI_LAYER)
        layers = list(self.map.layers)
        aoi = [layer for layer in layers if getattr(layer, "name", None) in order]
        if len(aoi) != len(order):
            return
        rest = [layer for layer in layers if layer not in aoi]
        aoi.sort(key=lambda layer: order.index(layer.name))
        self.map.layers = tuple(rest + aoi)

    def show_year(self, year: int) -> dict[str, Any]:
        """Make ``year`` the visible backsplash and return its coverage record.

        A year with no NAIP over the extent changes nothing on screen: hiding the
        year that is showing in order to reveal a layer that does not exist would
        leave the analyst staring at a blank map with a polygon on it.
        """
        record = self.mosaic_for(year)
        if record["mosaic"] is None:
            return record

        self._ensure_layer(year)
        for other in self.years:
            layer = self.map.find_layer(self.layer_name(other))
            if layer is not None:
                layer.visible = other == year
        return record

    # -- widget ---------------------------------------------------------------

    def widget(self):
        """The map with a year slider and a coverage readout beneath it."""
        import ipywidgets as widgets

        slider = widgets.SelectionSlider(
            options=self.years,
            value=self.target_year if self.target_year in self.years else self.years[0],
            description="NAIP year",
            continuous_update=False,
            style={"description_width": "initial"},
            layout=widgets.Layout(width="90%"),
        )
        status = widgets.HTML()

        def render(year: int) -> None:
            status.value = "<i>loading…</i>"
            record = self.show_year(int(year))
            status.value = self._status_html(int(year), record)

        slider.observe(lambda change: render(change["new"]), names="value")
        render(slider.value)

        return widgets.VBox([slider, status, self.map])

    def _status_html(self, year: int, record: dict[str, Any]) -> str:
        if record["mosaic"] is None:
            return (
                f"<b>{year}</b> — no NAIP imagery intersects this extent. "
                "The previous year stays on screen."
            )
        contributing = record["contributing_years"]
        filled = [other for other in contributing if other != year]
        note = f" · gap-filled from {filled}" if filled else ""
        marker = " · <b>target year</b>" if year == self.target_year else ""
        return (
            f"<b>{year}</b> — {record['coverage'] * 100:.1f}% of the AOI covered · "
            f"{record['image_count']} image(s){note}{marker}"
        )
