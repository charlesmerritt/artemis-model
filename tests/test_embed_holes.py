"""AlphaEarth sampling: the region each year's mosaic is filtered to."""

import pandas as pd

from pipeline.s1_initial_state.embed_holes import points_bounds


def test_the_sample_mosaic_covers_the_points_not_the_florida_aoi():
    # Maine points: a mosaic filtered to the FL5 AOI holds none of them.
    points = pd.DataFrame({"lon": [-70.1, -68.5, -69.0], "lat": [44.0, 47.2, 45.5]})
    assert points_bounds(points) == (-70.1, 44.0, -68.5, 47.2)
