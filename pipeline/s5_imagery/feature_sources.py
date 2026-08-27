"""
Per-point feature vectors for the raster-correction workflow.

``raster_correction`` needs one thing from the outside world: given a list of
lon/lat points, hand back a numeric feature matrix. It does not care whether
those numbers came from Earth Engine, a local COG, or a fixture. This module
declares that contract as :class:`FeatureSource` and provides the implementation
used in practice, :class:`AlphaEarthEmbeddings`.

Keeping the contract this narrow is what lets the correction logic be tested
without credentials, and what lets a future implementation — a windowed read of
an exported embedding raster, say, which would scale past the per-request point
budget below — drop in without the correction code changing.

Missing features are part of the contract, not an error. AlphaEarth does not
cover every pixel of every year, and ``sampleRegions`` silently drops masked
points. A source therefore returns ``NaN`` rows for points it could not answer
for, and the caller decides what to do with them (``raster_correction`` leaves
those pixels uncorrected rather than guessing).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable

import numpy as np

logger = logging.getLogger(__name__)

# AlphaEarth annual satellite embeddings: 64 unit-length dimensions at 10 m.
EMBEDDING_COLLECTION = "GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL"
EMBEDDING_BANDS = tuple(f"A{i:02d}" for i in range(64))
EMBEDDING_SCALE_M = 10.0

# AlphaEarth's published coverage. Outside it the collection is empty and every
# point comes back NaN, which is a confusing way to learn you asked for 2015.
EMBEDDING_FIRST_YEAR = 2017

# Points per sampleRegions/getInfo round-trip. 500 x 64 float bands sits well
# inside Earth Engine's response-size limit; larger chunks start failing with
# opaque payload errors rather than a clean message.
DEFAULT_CHUNK_SIZE = 500


@runtime_checkable
class FeatureSource(Protocol):
    """Turns lon/lat points into a feature matrix.

    Implementations must return an array of shape ``(len(lonlats), len(feature_names))``
    whose row order matches the input order, using ``NaN`` for points they cannot
    answer for.
    """

    @property
    def feature_names(self) -> list[str]:
        """Column names of the returned matrix, in order."""
        ...

    @property
    def description(self) -> str:
        """Short provenance string recorded in the run manifest."""
        ...

    def features_at(self, lonlats: Sequence[tuple[float, float]]) -> np.ndarray:
        """Feature matrix for the given WGS84 points, row-aligned to the input."""
        ...


@dataclass(frozen=True)
class AlphaEarthEmbeddings:
    """AlphaEarth annual embeddings sampled point-by-point through Earth Engine.

    ``year`` is the vintage to sample. For the correction workflow this is the
    classification raster's own vintage: a later year would let the model read
    post-classification change that the raster could not have known about, and an
    earlier one describes ground that has since moved.

    Earth Engine is imported lazily so that importing this module — which
    ``raster_correction`` does — costs nothing without credentials.
    """

    year: int
    chunk_size: int = DEFAULT_CHUNK_SIZE
    scale_m: float = EMBEDDING_SCALE_M
    collection: str = EMBEDDING_COLLECTION

    def __post_init__(self) -> None:
        if self.year < EMBEDDING_FIRST_YEAR:
            raise ValueError(
                f"AlphaEarth annual embeddings start in {EMBEDDING_FIRST_YEAR}; got {self.year}"
            )
        if self.chunk_size < 1:
            raise ValueError("chunk_size must be >= 1")
        if self.scale_m <= 0:
            raise ValueError("scale_m must be > 0")

    @property
    def feature_names(self) -> list[str]:
        return list(EMBEDDING_BANDS)

    @property
    def description(self) -> str:
        return f"{self.collection} {self.year} at {self.scale_m:g} m"

    def features_at(self, lonlats: Sequence[tuple[float, float]]) -> np.ndarray:
        import ee

        points = list(lonlats)
        matrix = np.full((len(points), len(EMBEDDING_BANDS)), np.nan, dtype=np.float64)
        if not points:
            return matrix

        image = self._image(ee)
        for start in range(0, len(points), self.chunk_size):
            chunk = points[start : start + self.chunk_size]
            sampled = image.sampleRegions(
                collection=self._point_collection(ee, chunk, start),
                scale=self.scale_m,
                tileScale=4,
                geometries=False,
            ).getInfo()

            for feature in sampled.get("features", []):
                properties = feature["properties"]
                # sampleRegions drops masked points and does not preserve order,
                # so the row is addressed by the id we attached, not by position.
                row = int(properties["pid"])
                matrix[row] = [properties.get(band, np.nan) for band in EMBEDDING_BANDS]

        missing = int(np.isnan(matrix).any(axis=1).sum())
        if missing:
            logger.warning(
                "%d/%d points have no %d embedding (outside coverage or masked)",
                missing,
                len(points),
                self.year,
            )
        return matrix

    def _image(self, ee):
        start = ee.Date.fromYMD(self.year, 1, 1)
        return (
            ee.ImageCollection(self.collection)
            .filterDate(start, start.advance(1, "year"))
            .mosaic()
            .select(list(EMBEDDING_BANDS))
        )

    @staticmethod
    def _point_collection(ee, chunk: Sequence[tuple[float, float]], id_start: int):
        return ee.FeatureCollection(
            [
                ee.Feature(ee.Geometry.Point([lon, lat]), {"pid": id_start + offset})
                for offset, (lon, lat) in enumerate(chunk)
            ]
        )
