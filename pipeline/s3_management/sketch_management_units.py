"""Compatibility wrapper for S1 boundary-overlay management-unit segmentation."""

from pipeline.s1_initial_state.segmentation import boundary_overlay as _canonical
from pipeline.s1_initial_state.segmentation.boundary_overlay import (
    FEET_TO_METERS_CONVERSION as FEET_TO_METERS_CONVERSION,
    FLORIDA_FIPS as FLORIDA_FIPS,
    HECTARES_TO_ACRES as HECTARES_TO_ACRES,
    MIN_UNIT_AREA_HA as MIN_UNIT_AREA_HA,
    PILOT_COUNTIES as PILOT_COUNTIES,
    PROJECT_CRS as PROJECT_CRS,
    SEGMENTATION_METHOD as SEGMENTATION_METHOD,
    SMALL_ROAD_BUFFER_M as SMALL_ROAD_BUFFER_M,
    TARGET_MAX_AREA_HA as TARGET_MAX_AREA_HA,
    classify_stream_fcode as classify_stream_fcode,
    classify_unit_size as classify_unit_size,
    clean_geometries as clean_geometries,
    create_forest_mask_from_evt as create_forest_mask_from_evt,
    feet_to_meters as feet_to_meters,
    load_config as load_config,
    load_florida_counties as load_florida_counties,
    normalize_output_contract as normalize_output_contract,
    process_county as process_county,
    split_large_geometry as split_large_geometry,
    target_grid_cell_size_m as target_grid_cell_size_m,
)


def main():
    """Delegate the legacy CLI to the canonical S1 implementation."""
    return _canonical.main()


if __name__ == "__main__":
    main()
