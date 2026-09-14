# Notes for agents working in this repo

## Data access
Data may be available on an external hard drive under /mnt/d/ symlinked to data on the work laptop, otherwise it is available in Cloudflare R2.

## Gotchas
### FIA control numbers (`PLT_CN`, `STAND_CN`, etc.) must not be cast via `str(int(...))`

`PLT_CN` and the other FIA control numbers (`STAND_CN`, `COND_CN`, `PLOT_CN`, `TREE_CN`,
`SUBP_CN`, …) are up to 19-digit integers. Any time one of these passes through a float64
(e.g. a numeric field read out of a GeoPackage/DBF via geopandas), it can silently lose
digits — an IEEE-754 double only carries 15–17 significant digits.

`str(int(x))` typecast to int = BAD

This was flagged as a real bug in PR #40 (`make_leto_figure.py` in the LETO CA research demo),
where `str(int(rec.PLT_CN))` was used to join TreeMap VAT records to `fvs_trajectory.csv`
instead of `as_id_series`.

## What Artemis should do, step by step

1. A LETO pipeline intializes state
- Repaired TreeMap connects with FIADB for spatial tree data
- LETO Cellular Automata runs to create management units
- Repaired National Woodland Ownership layer maps management units to the vegetation layer
- Riparian and other SMZ's are applied over top of the final management layer and a proportional amount of vegetation is removed and assigned to those regions, which can not be managed, only grown.

2. The ARTEMIS pipeline runs roughly following the procedure of the Ecotrust paper (Diaz et al). 
- We decide sensible management regimes a priori for each class of owner type, using the classes from the ownership database. No management is always an option (as universally applied to riparian zones for instance), the rest can be described in config. To give a few more examples, industrial/private land owners will be running some form of an optimal economic rotation, short rotation on pine plantations. While state/federal lands have flexibility to manage for a variety of objectives, not just economic. They may manage for recreation or ecological preservation, while also scheduling some harvests in pockets of their forested land.
- Every regime is run through FVS, for every management unit. First at smaller scales, then larger, until a map for the entire Eastern US can be run. I.e. locally, we run a single county in Florida, then we run the canonical five county area in Florida, then we run the entire state of Florida, then another state, and so on. This may involve moving from local hardware to more powerful machines and alternative architectures for parallelism. Luckily, FVS is embarrasingly parallelizable. These trajectories should be stored in a SQL database, managed by DuckDB. Remember to preserve CN/ID floating point accuracy.

3. A simulated annealing process is used to optimize the selection of trajectory for each management unit wrt even-flow following TPO harvest reports PER OWNER GROUP.
- It has been observed that classes of landowner generally harvest the same amounts of timber each year, despite being a tapestry of separate entities. Hence each owner class has it's own eligible regimes, and each regime can be offset in a number of 5 year intervals (5, 10, 15, 20, 25) to allow for flexible management activities to find the landscape level optimal outcome.
- The basic management actions are the most important, thinnings, and clearcut harvests. 
- No management is ever applied to riparian zones or other buffers.
- When land is clearcut, we use nearest neighbor imputation to reestablish those management units with an initial treelist, age 0, following the pattern of nearby, similar units. If needed, we can manually prescribe initial conditions such as planting density, species, etc. for owner classes.
- There are common sense heuristics in place such as minimum harvest age (15), minimum harvestable % (25%).


4. With the optimal trajectory for each management unit selected, we should produce a final output table with the trajectory for each management unit and it's vegetation data from FVS for a 50 year time horizon. Our focus output is a number of maps, and other visualization outcomes, mainly in the form of Cloud optimized GeoTifs rasters for each timestep in the simulation.
- A TreeMap like raster for each management unit in the simulation, going forward in time, reflecting the state of vegetation across the landscape.
- A separate map, which indicates management activities across the landscape.
- Comparison to FIA EVALID to speculate across the landscape as to the sustainability of evenflow harvesting at current levels.

## Guiding references
Diaz et al -> docs/references/Climate_FVS_Simulation_Report_20150306_SUBMITTED.pdf

We are using regular FVS, not climate FVS, but most of the methods are similar. We run the southern variant of FVS (FVSsn) to generate trajectories per management unit for each eligible management "scenario" and then a simulated annealing selects a trajectory for each management unit to optimize the trajectories at the state level, perhaps working up from the counties first, then the state. Key here is the use of timing offsets in the management trajectories, which comes directly out of this work, to allow management units to begin rotation at different times in order to optimize at the landscape level.

Optimization in this sense is wrt to even woodflow as based on the TPO reporting in config.

## Agentic principles
Close the loop, verify code rather than relying on plaintext assumptions or out of context code.

Enums over booleans always, as they are extensible!

Use test-driven development, for any given feature, write a failing test, then implement the feature to pass the test.

Automated quality guantlets to generate code, a specifier subagent defines requirements of the code, a coder subagent implements that spec, a cleaner fixes mess and reduces unnecessary complexity, and a hardener which runs mutation testing.

Lean on rigorous CI/CD for deterministic tests for code quality. Code that doesn't meet the quality threshold doesn't merge. Full scale mutation testing (something like https://github.com/unclebob/clj-mutate). Use hard metrics like CRAP score (Change risk anti patterns) which combine test coverage and cyclomatic complexity. Enforce dependency structures as defined only by the human architect. Birth and death lifecycle for agents to optimize for the "smart zone" of the context window between 0-250k tokens. If the context window exceeds 250k tokens, stop, don't implement any code changes, and suggest a new session to begin. Lean on Agile iteration, not spec-driven development. Agents handle small stories/sprints, followed by manual and automated reorganization. 

Record critical gotchas in the above "gotchas" section.