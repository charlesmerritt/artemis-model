"""Management-unit segmentation strategies for the S1 pipeline.

Three interchangeable methods, all producing the same output contract
(`MU_ID`, `Acres`, `SEGMENTATION_METHOD`, `PLT_CN`, `TM_VALUE`, `OWN_CODE`,
`OWN_TYPE`, `SMZ_Pct`, `geometry` -- see `segmentation.artifacts.
CANONICAL_UNIT_COLUMNS`):

- `cellular_automata` (default) -- the actual LETO algorithm, ported from
  `aauslander480/Leto`. See `segmentation.cellular_automata`.
- `voronoi_tessellation` -- a from-scratch Thiessen/Voronoi subdivision of
  the TreeMap domain, unrelated to LETO despite living in a module
  historically named `leto.py`. See `segmentation.leto`.
- `boundary_overlay` -- naive parcel/forest-mask boundary intersection.
  See `segmentation.boundary_overlay`.
"""

DEFAULT_SEGMENTATION_METHOD = "cellular_automata"
SEGMENTATION_METHODS = ("cellular_automata", "voronoi_tessellation", "boundary_overlay")
