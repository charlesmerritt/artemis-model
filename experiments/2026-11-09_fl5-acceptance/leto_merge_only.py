"""The leto stage with `SpeckleAction.MERGE`: speckle merges, and what cannot merge is kept.

A diagnostic for #173. The default, `MERGE_DELETE` (ADR 0005's 2026-11-09 amendment), deletes
the speckle that no step can place; this run keeps it, so the golden run's measures can be
compared with and without that deletion. Takes `main.py leto`'s arguments:

    PYTHONPATH=. uv run python experiments/2026-11-09_fl5-acceptance/leto_merge_only.py \
        leto <run_dir> --repair <raster run> ...
"""

import dataclasses
import sys

from pipeline import leto_stage
from pipeline.leto_speckle import SpeckleAction
from pipeline.run import main

_load_settings = leto_stage.load_settings


def merge_only(county_fips: str, *args, **kwargs) -> leto_stage.LetoSettings:
    settings = _load_settings(county_fips, *args, **kwargs)
    return dataclasses.replace(settings, speckle=dataclasses.replace(settings.speckle,
                                                                     action=SpeckleAction.MERGE))


# The planner loads each county's settings in this process and hands them to the workers.
leto_stage.load_settings = merge_only

if __name__ == "__main__":          # workers re-import this module (forkserver)
    main(sys.argv[1:])
