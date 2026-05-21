"""
FVS binary wrapper.

Planned interface (to be implemented in Step 4a):

    from fvs.wrapper import FVSRun

    result = FVSRun(
        tree_list=...,       # pd.DataFrame matching FVS-Ready DB schema
        site_attrs=...,      # dict: elev, slope, aspect, site_index, forest_type
        keyword_file=...,    # Path to .key template (pre-filled by keyword_builder)
        variant="SN",
    ).run()

    # result.cycles: pd.DataFrame — one row per 5-year cycle
    # result.carbon: pd.DataFrame — five IPCC pools per cycle
    # result.summary: dict — final-year stand attributes

Dependencies (not yet installed):
  - Open-FVS binary: https://github.com/USDAForestService/ForestVegetationSimulator
  - pyFVS or rFVS (evaluate at Step 4a; build from scratch if neither wraps cleanly)
"""
