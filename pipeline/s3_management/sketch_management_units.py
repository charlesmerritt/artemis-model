"""Compatibility wrapper for S1 boundary-overlay management-unit segmentation."""

from pipeline.s1_initial_state.segmentation import boundary_overlay as _canonical
from pipeline.s1_initial_state.segmentation.boundary_overlay import (
    FEET_TO_METERS_CONVERSION,
    FLORIDA_FIPS,
    HECTARES_TO_ACRES,
    MIN_UNIT_AREA_HA,
    PILOT_COUNTIES,
    PROJECT_CRS,
    SEGMENTATION_METHOD,
    SMALL_ROAD_BUFFER_M,
    TARGET_MAX_AREA_HA,
    classify_stream_fcode,
    classify_unit_size,
    clean_geometries,
    create_forest_mask_from_evt,
    feet_to_meters,
    load_config,
    load_florida_counties,
    normalize_output_contract,
    process_county,
    split_large_geometry,
    target_grid_cell_size_m,
)


def main():
    """Delegate the legacy CLI to the canonical S1 implementation."""
    return _canonical.main()


if __name__ == "__main__":
    main()
