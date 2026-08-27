"""
Window arithmetic shared by the raster-reading modules.

One function lives here so far. ``raster_clip`` and ``raster_correction`` both
carve a fractional window out of a raster and must never shave a fraction of a
pixel off it — the margin *is* the payload (a clip's bounding box, a correction's
training ring) — so they share the rounding rule instead of each carrying a copy
that could drift.
"""

from __future__ import annotations

import math
from typing import Any

from rasterio import windows as rio_windows


def round_outward(window: Any) -> Any:
    """Grow a fractional window to whole pixels in every direction.

    ``Window.round_lengths`` rounds to nearest, which can shave up to half a
    pixel off an edge. Rounding outward instead keeps the window at least as
    large as asked for; erring small is the wrong direction to err when the
    edge carries evidence.
    """
    col_off = math.floor(window.col_off)
    row_off = math.floor(window.row_off)
    return rio_windows.Window(
        col_off=col_off,
        row_off=row_off,
        width=math.ceil(window.col_off + window.width) - col_off,
        height=math.ceil(window.row_off + window.height) - row_off,
    )
