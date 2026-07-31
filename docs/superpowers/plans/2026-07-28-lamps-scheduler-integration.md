# LAMPS Eligibility + Adjacency Scheduler Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a constrained harvest scheduler where LAMPS's eligibility screening (MHA/MHP) and adjacency/blocking (ARM/URM) decide *whether and when* a management unit can be cut, and PR #6's existing deterministic regime assignment + FVS keyfile rendering decide *what happens* to a unit once it's scheduled — with riparian zones absolutely excluded from harvest via a single shared rule.

**Architecture:** Port `harvest_eligibility.py` and `adjacency.py` unchanged from the `lamps-harvest-constraints` branch. Keep `regime_assignment.py` and `regime_templates.py` unchanged from PR #6. Add a new orchestration layer in `harvest_scheduler.py` that: (1) screens candidate units for eligibility per cycle, with riparian units excluded via PR #6's absolute `SMZ_Pct` rule; (2) builds the spatial adjacency graph once and re-partitions it into blocks each cycle from that cycle's eligible units; (3) allocates harvest per block (not per unit) against TOTAL/COUNTY/OWNER volume caps, enforcing that adjacent blocks never harvest in the same cycle (URM); (4) assigns a regime to every harvested unit via the existing deterministic rule. The existing per-unit `allocate_cycle` primitive is kept for backward compatibility and reused inside the new block-aware allocator's budget-ledger logic.

**Tech Stack:** Python, pandas, geopandas, pytest, PyYAML — matches the rest of `pipeline/s3_management`.

## Global Constraints

- Riparian exclusion is absolute: `SMZ_Pct >= RIPARIAN_SMZ_PCT` (50.0, defined in `regime_assignment.py`) means a unit is never eligible for harvest, full stop. No partial-cut riparian policy.
- MHA (minimum harvest age) lookups must raise for unmapped owner groups per LAMPS's own design ("do not invent values") — but the new scheduler must catch that per-owner-group and degrade gracefully to `schedulable=False` for that group, not crash the whole scheduling run.
- Adjacency graph is built once per landscape (not recomputed per cycle) — only block assembly is recomputed per cycle, from a cycle-specific subgraph of eligible units.
- A block is an atomic harvest decision: either every member unit is harvested this cycle, or none are.
- No test may depend on real data under `data/raw` or R2 — everything here uses synthetic fixtures, matching the existing test style in `tests/test_s3_adjacency.py` and `tests/test_s3_harvest_scheduler.py`.
- No `__init__.py` files exist in `pipeline/s3_management` or `pipeline/s4_fvs` — follow the existing test convention of `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` at the top of each test file.

---

### Task 1: Port `harvest_eligibility.py` from the LAMPS branch

**Files:**
- Create: `pipeline/s3_management/harvest_eligibility.py`
- Create: `tests/test_s3_harvest_eligibility.py`
- Create: `config/harvest_constraints.yaml`
- Create: `docs/harvest-constraints.md`

**Interfaces:**
- Produces: `load_constraints(path=None) -> dict`, `minimum_harvest_age_for(owner_group: str, constraints=None) -> int`, `pixel_eligible(stand_age: pd.Series, minimum_harvest_age: float, riparian_restricted: Optional[pd.Series]) -> pd.Series`, `screen_units(pixels: pd.DataFrame, minimum_harvest_age: float, minimum_harvestable_percentage: float, unit_id_col="unit_id", age_col="stand_age", area_col="area_ha", riparian_col="riparian_restricted") -> pd.DataFrame` (indexed by unit id, columns `total_area_ha, eligible_area_ha, gamma, schedulable`). Constants: `UNIT_ID_COL, AGE_COL, AREA_COL, RIPARIAN_COL`.

- [ ] **Step 1: Copy the four files verbatim from the LAMPS branch**

```bash
git show lamps-harvest-constraints:pipeline/s3_management/harvest_eligibility.py > pipeline/s3_management/harvest_eligibility.py
git show lamps-harvest-constraints:tests/test_s3_harvest_eligibility.py > tests/test_s3_harvest_eligibility.py
git show lamps-harvest-constraints:config/harvest_constraints.yaml > config/harvest_constraints.yaml
git show lamps-harvest-constraints:docs/harvest-constraints.md > docs/harvest-constraints.md
```

- [ ] **Step 2: Run the ported tests to confirm they pass unmodified in this repo**

Run: `uv run pytest tests/test_s3_harvest_eligibility.py -v`
Expected: All tests PASS (this file has no dependency on anything else in the LAMPS branch — it only reads `config/harvest_constraints.yaml`, which was copied alongside it).

- [ ] **Step 3: Commit**

```bash
git add pipeline/s3_management/harvest_eligibility.py tests/test_s3_harvest_eligibility.py config/harvest_constraints.yaml docs/harvest-constraints.md
git commit -m "feat(s3): port LAMPS harvest eligibility screening (MHA/MHP)"
```

---

### Task 2: Port `adjacency.py` from the LAMPS branch

**Files:**
- Create: `pipeline/s3_management/adjacency.py`
- Create: `tests/test_s3_adjacency.py`

**Interfaces:**
- Consumes: `config/harvest_constraints.yaml` (from Task 1) via `load_constraints`.
- Produces: `build_adjacency(units: gpd.GeoDataFrame, unit_id_col="unit_id", area_col="unit_area_ha", min_area_ha=2.0) -> tuple[dict[str, set[str]], gpd.GeoDataFrame]`, `assemble_blocks(graph, units, unit_id_col="unit_id", area_col="unit_area_ha", max_clearcut_area_ha=None, ordering=None, constraints=None) -> pd.DataFrame` (columns `block_id: int, unit_id, block_area_ha, exceeds_cap: bool`), `identify_adjacent_block_pairs(blocks: pd.DataFrame, graph: dict) -> set[tuple[int, int]]`. Constants: `UNIT_AREA_COL = "unit_area_ha"`, `MIN_UNIT_AREA_HA = 2.0`.

- [ ] **Step 1: Copy the two files verbatim from the LAMPS branch**

```bash
git show lamps-harvest-constraints:pipeline/s3_management/adjacency.py > pipeline/s3_management/adjacency.py
git show lamps-harvest-constraints:tests/test_s3_adjacency.py > tests/test_s3_adjacency.py
```

- [ ] **Step 2: Run the ported tests to confirm they pass unmodified**

Run: `uv run pytest tests/test_s3_adjacency.py -v`
Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add pipeline/s3_management/adjacency.py tests/test_s3_adjacency.py
git commit -m "feat(s3): port LAMPS ARM/URM adjacency and block assembly"
```

---

### Task 3: Port `regime_assignment.py` + `regime_templates.py` from PR #6, add missing tests

PR #6 never added a test file for `regime_assignment.py` itself (only for `regime_templates.py`). This task closes that gap with TDD before the scheduler starts depending on it.

**Files:**
- Create: `pipeline/s3_management/regime_assignment.py`
- Create: `pipeline/s4_fvs/regime_templates.py`
- Create: `tests/test_s4_regime_templates.py`
- Create: `tests/test_s3_regime_assignment.py`

**Interfaces:**
- Produces: from `regime_assignment.py` — `FAMILY, CORPORATE, TRIBAL, FEDERAL, STATE, LOCAL = 3, 4, 5, 6, 7, 8`, `PUBLIC_OWNERS = {FEDERAL, STATE, TRIBAL, LOCAL}`, `RIPARIAN_SMZ_PCT = 50.0`, `is_pine(unit: Mapping) -> bool`, `assign_regime(unit: Mapping, inv_year: int = 2022) -> tuple[str, dict]`, `assign_regimes(units, inv_year=2022) -> DataFrame`. From `regime_templates.py` — `REGIMES: dict[str, Callable]`, `render_keyfile(...)`.

- [ ] **Step 1: Copy the two production files and PR #6's existing template test verbatim**

```bash
git show origin/claude/artemis-week-summary-rpkrn9:pipeline/s3_management/regime_assignment.py > pipeline/s3_management/regime_assignment.py
git show origin/claude/artemis-week-summary-rpkrn9:pipeline/s4_fvs/regime_templates.py > pipeline/s4_fvs/regime_templates.py
git show origin/claude/artemis-week-summary-rpkrn9:tests/test_s4_regime_templates.py > tests/test_s4_regime_templates.py
```

- [ ] **Step 2: Run the ported template test to confirm it passes unmodified**

Run: `uv run pytest tests/test_s4_regime_templates.py -v`
Expected: All tests PASS.

- [ ] **Step 3: Write the failing test file for `regime_assignment.py`**

Create `tests/test_s3_regime_assignment.py`:

```python
"""Tests for deterministic regime assignment (pipeline/s3_management/regime_assignment.py)."""

from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s3_management.regime_assignment import (
    CORPORATE,
    FAMILY,
    FEDERAL,
    LOCAL,
    RIPARIAN_SMZ_PCT,
    STATE,
    TRIBAL,
    assign_regime,
    assign_regimes,
    is_pine,
)


def test_is_pine_from_fortypcd_in_range():
    assert is_pine({"FORTYPCD": 161}) is True


def test_is_pine_from_fortypcd_out_of_range():
    assert is_pine({"FORTYPCD": 500}) is False


def test_is_pine_from_name_when_code_missing():
    assert is_pine({"ForTypName": "Longleaf pine"}) is True
    assert is_pine({"ForTypName": "Sweetgum-Yellow-poplar"}) is False


def test_is_pine_missing_all_fields_defaults_false():
    assert is_pine({}) is False


def test_riparian_unit_gets_no_management_regardless_of_owner():
    unit = {"OWN_CODE": CORPORATE, "SMZ_Pct": RIPARIAN_SMZ_PCT, "FORTYPCD": 161}
    regime, params = assign_regime(unit)
    assert regime == "no_management"
    assert params == {}


def test_riparian_threshold_is_inclusive():
    unit = {"OWN_CODE": CORPORATE, "SMZ_Pct": RIPARIAN_SMZ_PCT - 0.01}
    regime, _ = assign_regime(unit)
    assert regime != "no_management"


@pytest.mark.parametrize("owner", [FEDERAL, STATE, TRIBAL, LOCAL])
def test_public_owners_get_selection_harvest(owner):
    unit = {"OWN_CODE": owner, "SMZ_Pct": 0.0}
    regime, params = assign_regime(unit, inv_year=2022)
    assert regime == "selection_harvest"
    assert params["start_year"] == 2032
    assert params["end_year"] == 2062


def test_family_forest_gets_thin_from_below():
    unit = {"OWN_CODE": FAMILY, "SMZ_Pct": 0.0}
    regime, params = assign_regime(unit)
    assert regime == "thin_from_below"
    assert params["max_dbh"] == 8.0


def test_corporate_pine_gets_plantation_rotation():
    unit = {"OWN_CODE": CORPORATE, "SMZ_Pct": 0.0, "FORTYPCD": 161}
    regime, params = assign_regime(unit, inv_year=2022)
    assert regime == "plantation_rotation"
    assert params["thin_year"] == 2037
    assert params["clearcut_year"] == 2052


def test_corporate_hardwood_gets_clearcut():
    unit = {"OWN_CODE": CORPORATE, "SMZ_Pct": 0.0, "FORTYPCD": 500}
    regime, params = assign_regime(unit, inv_year=2022)
    assert regime == "clearcut"
    assert params["year"] == 2052


def test_unknown_ownership_defaults_to_thin_from_below():
    unit = {"OWN_CODE": None, "SMZ_Pct": 0.0}
    regime, _ = assign_regime(unit)
    assert regime == "thin_from_below"


def test_assign_regimes_adds_columns_to_dataframe():
    units = pd.DataFrame({
        "OWN_CODE": [CORPORATE, FAMILY],
        "SMZ_Pct": [0.0, 0.0],
        "FORTYPCD": [161, 500],
    })
    out = assign_regimes(units, inv_year=2022)
    assert list(out["regime"]) == ["plantation_rotation", "thin_from_below"]
    assert all(isinstance(p, dict) for p in out["regime_params"])
```

- [ ] **Step 4: Run the new test file to verify it currently passes against the ported (unmodified) module**

Run: `uv run pytest tests/test_s3_regime_assignment.py -v`
Expected: All tests PASS (the module was already fully implemented in PR #6 — this task adds coverage, it does not change behavior). If any test fails, it means this plan's understanding of `regime_assignment.py`'s behavior is wrong — stop and re-read the module rather than editing the test to match.

- [ ] **Step 5: Commit**

```bash
git add pipeline/s3_management/regime_assignment.py pipeline/s4_fvs/regime_templates.py tests/test_s4_regime_templates.py tests/test_s3_regime_assignment.py
git commit -m "feat(s3,s4): port PR #6 regime assignment + keyfile templates, add missing regime_assignment tests"
```

---

### Task 4: Ownership-code and riparian helpers in `harvest_scheduler.py`

Bridges `regime_assignment.py`'s numeric `OWN_CODE` vocabulary and `SMZ_Pct` threshold to the string owner-group vocabulary and boolean riparian flag that `harvest_eligibility.py` expects.

**Files:**
- Modify: `pipeline/s3_management/harvest_scheduler.py` (append to existing file — do not remove `to_cycle_budget`, `allocate_cycle`, `schedule_harvests`, `summarize_schedule`, `TOTAL`/`COUNTY`/`OWNER`)
- Test: `tests/test_s3_harvest_scheduler.py` (append)

**Interfaces:**
- Consumes: `regime_assignment.CORPORATE`, `regime_assignment.PUBLIC_OWNERS`, `regime_assignment.RIPARIAN_SMZ_PCT` (Task 3).
- Produces: `MHA_INDUSTRIAL = "industrial"`, `MHA_PUBLIC = "public"`, `owner_code_to_mha_group(own_code: Optional[int]) -> str` (raises `ValueError` for unmapped codes), `riparian_restricted(units: pd.DataFrame, smz_col: str = "SMZ_Pct") -> pd.Series`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_s3_harvest_scheduler.py`:

```python
from pipeline.s3_management.harvest_scheduler import (
    MHA_INDUSTRIAL,
    MHA_PUBLIC,
    owner_code_to_mha_group,
    riparian_restricted,
)
from pipeline.s3_management.regime_assignment import (
    CORPORATE, FAMILY, FEDERAL, LOCAL, RIPARIAN_SMZ_PCT, STATE, TRIBAL,
)


def test_corporate_maps_to_industrial():
    assert owner_code_to_mha_group(CORPORATE) == MHA_INDUSTRIAL


@pytest.mark.parametrize("owner", [FEDERAL, STATE, TRIBAL, LOCAL])
def test_public_owners_map_to_public(owner):
    assert owner_code_to_mha_group(owner) == MHA_PUBLIC


def test_family_forest_has_no_mha_mapping():
    with pytest.raises(ValueError, match="FAMILY|family|3"):
        owner_code_to_mha_group(FAMILY)


def test_unknown_owner_code_has_no_mha_mapping():
    with pytest.raises(ValueError):
        owner_code_to_mha_group(None)


def test_riparian_restricted_flags_units_at_or_above_threshold():
    units = pd.DataFrame({"SMZ_Pct": [RIPARIAN_SMZ_PCT, RIPARIAN_SMZ_PCT - 0.01, 0.0]})
    flags = riparian_restricted(units)
    assert list(flags) == [True, False, False]
```

`pytest` is already imported at the top of `tests/test_s3_harvest_scheduler.py`; add `import pandas as pd` if not already present (it is — the existing `_units()` fixture uses it).

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_s3_harvest_scheduler.py -k "mha_group or riparian_restricted" -v`
Expected: FAIL with `ImportError` — `owner_code_to_mha_group`, `riparian_restricted`, `MHA_INDUSTRIAL`, `MHA_PUBLIC` don't exist yet.

- [ ] **Step 3: Implement the helpers**

Append to `pipeline/s3_management/harvest_scheduler.py` (after the existing imports, before `DEFAULT_CYCLE_YEARS`):

```python
from typing import Optional

from pipeline.s3_management.regime_assignment import (
    CORPORATE,
    PUBLIC_OWNERS,
    RIPARIAN_SMZ_PCT,
)
```

Append at the end of the file:

```python
# ---- LAMPS eligibility/adjacency integration -------------------------------------------

MHA_INDUSTRIAL = "industrial"
MHA_PUBLIC = "public"


def owner_code_to_mha_group(own_code: Optional[int]) -> str:
    """
    Map a regime_assignment.py OWN_CODE to the owner-group vocabulary
    harvest_eligibility.minimum_harvest_age_for expects.

    LAMPS only distinguishes 'industrial' (-> CORPORATE) and 'public'
    (-> FEDERAL/STATE/TRIBAL/LOCAL). FAMILY and unmapped/unknown codes raise
    deliberately — there is no MHA value for them yet, and inventing one would
    silently misrepresent the model. Callers must catch this per owner group
    and mark those units as not-yet-schedulable rather than letting one
    unmapped group crash the whole scheduling run.
    """
    if own_code == CORPORATE:
        return MHA_INDUSTRIAL
    if own_code in PUBLIC_OWNERS:
        return MHA_PUBLIC
    raise ValueError(
        f"No minimum-harvest-age owner group mapped for OWN_CODE={own_code!r}. "
        f"LAMPS covers only industrial (CORPORATE) and public "
        f"(FEDERAL/STATE/TRIBAL/LOCAL) owners; family forest and unknown "
        f"ownership have no MHA value defined in config/harvest_constraints.yaml."
    )


def riparian_restricted(units: pd.DataFrame, smz_col: str = "SMZ_Pct") -> pd.Series:
    """
    Absolute riparian exclusion: SMZ_Pct >= RIPARIAN_SMZ_PCT means never eligible.

    Mirrors regime_assignment.assign_regime's own riparian check so a unit that
    is excluded here also resolves to the "no_management" regime if it ever
    reaches regime assignment (defense in depth, not two different policies).
    """
    return units[smz_col].fillna(0.0) >= RIPARIAN_SMZ_PCT
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_s3_harvest_scheduler.py -k "mha_group or riparian_restricted" -v`
Expected: PASS.

- [ ] **Step 5: Run the full scheduler test file to confirm no regression**

Run: `uv run pytest tests/test_s3_harvest_scheduler.py -v`
Expected: All tests (original + new) PASS.

- [ ] **Step 6: Commit**

```bash
git add pipeline/s3_management/harvest_scheduler.py tests/test_s3_harvest_scheduler.py
git commit -m "feat(s3): map ownership codes to MHA groups, wire absolute riparian exclusion"
```

---

### Task 5: Per-cycle eligibility screening across mixed owner groups

`harvest_eligibility.screen_units` takes one scalar MHA for the whole call, but MHA varies by owner group (40 industrial / 45 public) and some groups aren't mapped at all. This task adds the per-group orchestration.

**Files:**
- Modify: `pipeline/s3_management/harvest_scheduler.py`
- Test: `tests/test_s3_harvest_scheduler.py`

**Interfaces:**
- Consumes: `harvest_eligibility.screen_units`, `harvest_eligibility.minimum_harvest_age_for`, `harvest_eligibility.load_constraints` (Task 1); `owner_code_to_mha_group`, `riparian_restricted` (Task 4).
- Produces: `screen_unit_eligibility(basic_units: pd.DataFrame, minimum_harvestable_percentage: float = 0.5, constraints: Optional[dict] = None) -> pd.DataFrame` — indexed by `unit_id`, columns `total_area_ha, eligible_area_ha, gamma, schedulable, mha_group` (`mha_group` is `None` and `schedulable=False` for units whose owner group has no MHA mapping).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_s3_harvest_scheduler.py`:

```python
from pipeline.s3_management.harvest_scheduler import screen_unit_eligibility
from pipeline.s3_management.regime_assignment import CORPORATE, FAMILY, STATE


def _basic_units():
    # Two "basic simulation units" (rows) per management unit, mirroring the
    # MU x PLT_CN weighted-area grain assign_plt_cn.py produces.
    return pd.DataFrame({
        "unit_id":     ["u1", "u1", "u2", "u2", "u3", "u3"],
        "stand_age":   [45,   50,   30,   50,   60,   60],
        "area_ha":     [1.0,  1.0,  1.0,  1.0,  2.0,  2.0],
        "OWN_CODE":    [CORPORATE, CORPORATE, CORPORATE, CORPORATE, FAMILY, FAMILY],
        "SMZ_Pct":     [0.0,  0.0,  0.0,  0.0,  0.0,  0.0],
    })


def test_screens_industrial_unit_above_mha_and_mhp():
    # u1: both rows age >= 40 (industrial MHA) -> gamma=1.0, schedulable.
    out = screen_unit_eligibility(_basic_units())
    assert out.loc["u1", "schedulable"] == True
    assert out.loc["u1", "gamma"] == pytest.approx(1.0)
    assert out.loc["u1", "mha_group"] == "industrial"


def test_screens_industrial_unit_below_mhp():
    # u2: only 1 of 2 ha-equal rows clears age 40 -> gamma=0.5, MHP default 0.5 -> schedulable (>=).
    out = screen_unit_eligibility(_basic_units())
    assert out.loc["u2", "gamma"] == pytest.approx(0.5)
    assert out.loc["u2", "schedulable"] == True


def test_family_forest_unit_has_no_mha_and_is_not_schedulable():
    # u3 is FAMILY, which has no MHA mapping -> gracefully not schedulable, not a crash.
    out = screen_unit_eligibility(_basic_units())
    assert out.loc["u3", "schedulable"] == False
    assert out.loc["u3", "mha_group"] is None


def test_riparian_unit_never_schedulable_even_if_old_enough():
    basic = _basic_units()
    basic.loc[basic["unit_id"] == "u1", "SMZ_Pct"] = 100.0
    out = screen_unit_eligibility(basic)
    assert out.loc["u1", "schedulable"] == False
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_s3_harvest_scheduler.py -k "screen" -v`
Expected: FAIL — `screen_unit_eligibility` not defined.

- [ ] **Step 3: Implement**

Append to `pipeline/s3_management/harvest_scheduler.py`:

```python
from pipeline.s3_management import harvest_eligibility


def screen_unit_eligibility(
    basic_units: pd.DataFrame,
    minimum_harvestable_percentage: float = 0.5,
    constraints: Optional[dict] = None,
) -> pd.DataFrame:
    """
    Screen management units for harvest eligibility (LAMPS Eqs. 5-8), across
    owner groups with different MHA values.

    ``basic_units`` is one row per basic-simulation-unit (e.g. an
    assign_plt_cn.py MU x PLT_CN weighted row) with at least ``unit_id``,
    ``stand_age``, ``area_ha``, ``OWN_CODE``, and ``SMZ_Pct``. Riparian
    exclusion is absolute (see riparian_restricted) and is broadcast from the
    unit-level SMZ_Pct to every basic-unit row for that unit, since ARTEMIS
    does not yet delineate riparian status below the management-unit scale.

    Units whose OWN_CODE has no MHA mapping (see owner_code_to_mha_group) are
    returned with schedulable=False and mha_group=None rather than raising —
    one owner group's missing calibration must not abort the whole screen.
    """
    constraints = constraints or harvest_eligibility.load_constraints()

    pixels = basic_units.copy()
    pixels["riparian_restricted"] = riparian_restricted(pixels)

    unit_owner = pixels.groupby("unit_id")["OWN_CODE"].first()
    results = []
    for unit_id, own_code in unit_owner.items():
        unit_pixels = pixels[pixels["unit_id"] == unit_id]
        try:
            mha_group = owner_code_to_mha_group(own_code)
            mha = harvest_eligibility.minimum_harvest_age_for(mha_group, constraints)
        except ValueError:
            results.append({
                "unit_id": unit_id, "total_area_ha": unit_pixels["area_ha"].sum(),
                "eligible_area_ha": 0.0, "gamma": 0.0,
                "schedulable": False, "mha_group": None,
            })
            continue

        screened = harvest_eligibility.screen_units(
            unit_pixels, minimum_harvest_age=mha,
            minimum_harvestable_percentage=minimum_harvestable_percentage,
        )
        row = screened.loc[unit_id]
        results.append({
            "unit_id": unit_id, "total_area_ha": row["total_area_ha"],
            "eligible_area_ha": row["eligible_area_ha"], "gamma": row["gamma"],
            "schedulable": bool(row["schedulable"]), "mha_group": mha_group,
        })

    return pd.DataFrame(results).set_index("unit_id")
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_s3_harvest_scheduler.py -k "screen" -v`
Expected: PASS.

- [ ] **Step 5: Run full file, then commit**

```bash
uv run pytest tests/test_s3_harvest_scheduler.py -v
git add pipeline/s3_management/harvest_scheduler.py tests/test_s3_harvest_scheduler.py
git commit -m "feat(s3): per-owner-group eligibility screening with graceful degradation"
```

---

### Task 6: Block assembly wrapper

**Files:**
- Modify: `pipeline/s3_management/harvest_scheduler.py`
- Test: `tests/test_s3_harvest_scheduler.py`

**Interfaces:**
- Consumes: `adjacency.build_adjacency`, `adjacency.assemble_blocks`, `adjacency.identify_adjacent_block_pairs` (Task 2).
- Produces: `build_landscape_graph(units: gpd.GeoDataFrame, constraints=None) -> tuple[dict[str, set[str]], gpd.GeoDataFrame]` (thin wrapper, called once), `assemble_cycle_blocks(graph: dict, eligible_units: gpd.GeoDataFrame, priority_col: str = "stand_age", constraints=None) -> tuple[pd.DataFrame, set[tuple[int, int]]]` (subgraphs to `eligible_units`, then blocks + adjacent pairs).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_s3_harvest_scheduler.py`:

```python
import geopandas as gpd
from shapely.geometry import box

from pipeline.s3_management.harvest_scheduler import (
    assemble_cycle_blocks,
    build_landscape_graph,
)


def _adjacent_units_gdf():
    # Two 3ha units sharing an edge (mergeable into one 6ha block), plus one
    # isolated 5ha unit far away.
    return gpd.GeoDataFrame({
        "unit_id": ["a", "b", "c"],
        "unit_area_ha": [3.0, 3.0, 5.0],
        "stand_age": [80, 60, 90],
        "geometry": [box(0, 0, 100, 300), box(100, 0, 200, 300), box(1000, 0, 1100, 500)],
    }, crs="EPSG:5070")


def test_build_landscape_graph_connects_touching_units():
    graph, excluded = build_landscape_graph(_adjacent_units_gdf())
    assert graph["a"] == {"b"}
    assert graph["c"] == set()
    assert excluded.empty


def test_assemble_cycle_blocks_merges_adjacent_eligible_units():
    graph, _ = build_landscape_graph(_adjacent_units_gdf())
    blocks, adjacent_pairs = assemble_cycle_blocks(graph, _adjacent_units_gdf())
    a_block = blocks.loc[blocks["unit_id"] == "a", "block_id"].iloc[0]
    b_block = blocks.loc[blocks["unit_id"] == "b", "block_id"].iloc[0]
    assert a_block == b_block
    assert adjacent_pairs == set()  # a and b merged into one block, so no distinct adjacent blocks


def test_assemble_cycle_blocks_restricts_to_eligible_subset():
    graph, _ = build_landscape_graph(_adjacent_units_gdf())
    # Only "a" and "c" are eligible this cycle -- "b" dropped, so "a" is now isolated.
    eligible = _adjacent_units_gdf()[lambda d: d["unit_id"].isin(["a", "c"])]
    blocks, _ = assemble_cycle_blocks(graph, eligible)
    assert set(blocks["unit_id"]) == {"a", "c"}
    a_block = blocks.loc[blocks["unit_id"] == "a", "block_id"].iloc[0]
    c_block = blocks.loc[blocks["unit_id"] == "c", "block_id"].iloc[0]
    assert a_block != c_block
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_s3_harvest_scheduler.py -k "landscape_graph or cycle_blocks" -v`
Expected: FAIL — names not defined.

- [ ] **Step 3: Implement**

Append to `pipeline/s3_management/harvest_scheduler.py`:

```python
import geopandas as gpd

from pipeline.s3_management import adjacency


def build_landscape_graph(
    units: gpd.GeoDataFrame,
    constraints: Optional[dict] = None,
) -> tuple[dict[str, set[str]], gpd.GeoDataFrame]:
    """
    Build the spatial adjacency graph once for the whole landscape.

    Call this exactly once per units universe, not per cycle — adjacency is a
    spatial fact independent of which units are age-eligible this cycle. Each
    cycle, restrict this graph to that cycle's eligible units before calling
    assemble_cycle_blocks.
    """
    return adjacency.build_adjacency(units)


def assemble_cycle_blocks(
    graph: dict[str, set[str]],
    eligible_units: gpd.GeoDataFrame,
    priority_col: str = "stand_age",
    constraints: Optional[dict] = None,
) -> tuple[pd.DataFrame, set[tuple[int, int]]]:
    """
    Partition this cycle's eligible units into harvest blocks (ARM) and find
    which resulting blocks are spatially adjacent (URM).

    Restricts the landscape-wide graph to only the units present in
    eligible_units before blocking, so a unit that's ineligible this cycle
    neither joins nor blocks a neighbor's block this cycle.
    """
    eligible_ids = set(eligible_units["unit_id"])
    subgraph = {
        uid: (neighbors & eligible_ids)
        for uid, neighbors in graph.items()
        if uid in eligible_ids
    }
    blocks = adjacency.assemble_blocks(
        subgraph, eligible_units, ordering=priority_col, constraints=constraints,
    )
    adjacent_pairs = adjacency.identify_adjacent_block_pairs(blocks, subgraph)
    return blocks, adjacent_pairs
```

- [ ] **Step 4: Run to verify pass, run full file, commit**

```bash
uv run pytest tests/test_s3_harvest_scheduler.py -v
git add pipeline/s3_management/harvest_scheduler.py tests/test_s3_harvest_scheduler.py
git commit -m "feat(s3): assemble per-cycle harvest blocks from a landscape-wide adjacency graph"
```

---

### Task 7: Block-aware constrained allocator (URM same-cycle exclusion)

The core new algorithm: harvest decisions are made per block, but volume caps are still enforced per unit against its own county/owner budget key (a block may span owners), and no two adjacent blocks may harvest in the same cycle.

**Files:**
- Modify: `pipeline/s3_management/harvest_scheduler.py`
- Test: `tests/test_s3_harvest_scheduler.py`

**Interfaces:**
- Consumes: `_build_budgets`, `_dim_key`, `TOTAL`/`COUNTY`/`OWNER` (existing, this file).
- Produces: `allocate_cycle_with_blocks(units: pd.DataFrame, blocks: pd.DataFrame, adjacent_block_pairs: set[tuple[int, int]], caps: dict, dims: Sequence[str] = (TOTAL,), priority_col: str = "stand_age", volume_col: str = "removable_volume", cycle_years: int = DEFAULT_CYCLE_YEARS) -> pd.DataFrame` — same per-unit output shape as `allocate_cycle` (`harvested`, `volume_removed`, `blocked_by`), plus a `block_id` column.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_s3_harvest_scheduler.py`:

```python
from pipeline.s3_management.harvest_scheduler import allocate_cycle_with_blocks


def _blocked_units():
    # Block 1 = {a, b} (merged, adjacent to block 2). Block 2 = {c} alone.
    # Block 3 = {d} alone, not adjacent to anything.
    units = pd.DataFrame({
        "unit_id": ["a", "b", "c", "d"],
        "county": ["Baker", "Baker", "Baker", "Union"],
        "owner_group": ["Private", "Private", "Private", "Private"],
        "stand_age": [80, 70, 60, 50],
        "removable_volume": [50.0, 50.0, 100.0, 100.0],
    })
    blocks = pd.DataFrame({
        "block_id": [1, 1, 2, 3],
        "unit_id": ["a", "b", "c", "d"],
        "block_area_ha": [6.0, 6.0, 3.0, 5.0],
        "exceeds_cap": [False, False, False, False],
    })
    adjacent_pairs = {(1, 2)}  # block 1 and block 2 are spatially adjacent; block 3 is isolated.
    return units, blocks, adjacent_pairs


def test_whole_block_harvests_together_when_budget_allows():
    units, blocks, adjacent_pairs = _blocked_units()
    caps = {TOTAL: {"": 1000.0}}
    out = allocate_cycle_with_blocks(units, blocks, adjacent_pairs, caps, dims=[TOTAL])
    a_b = out[out["unit_id"].isin(["a", "b"])]
    assert a_b["harvested"].all()
    assert set(a_b["volume_removed"]) == {50.0}


def test_urm_prevents_adjacent_blocks_harvesting_same_cycle():
    units, blocks, adjacent_pairs = _blocked_units()
    caps = {TOTAL: {"": 1000.0}}
    out = allocate_cycle_with_blocks(units, blocks, adjacent_pairs, caps, dims=[TOTAL])
    # Block 1 {a,b} is processed first (oldest priority, age 80/70 > 60).
    block1_harvested = out[out["unit_id"].isin(["a", "b"])]["harvested"].all()
    block2_harvested = out[out["unit_id"] == "c"]["harvested"].iloc[0]
    assert block1_harvested and not block2_harvested
    assert out[out["unit_id"] == "c"]["blocked_by"].iloc[0] == "urm_adjacent"


def test_non_adjacent_block_is_unaffected_by_urm():
    units, blocks, adjacent_pairs = _blocked_units()
    caps = {TOTAL: {"": 1000.0}}
    out = allocate_cycle_with_blocks(units, blocks, adjacent_pairs, caps, dims=[TOTAL])
    assert out[out["unit_id"] == "d"]["harvested"].iloc[0] == True


def test_block_blocked_if_any_member_exceeds_its_own_owner_budget():
    units, blocks, adjacent_pairs = _blocked_units()
    units.loc[units["unit_id"] == "b", "owner_group"] = "Public"
    # Private budget is generous; Public budget is 0 -> unit b can't afford its
    # own volume, so the WHOLE block {a,b} is blocked, including unit a.
    caps = {OWNER: {"Private": 1000.0, "Public": 0.0}}
    out = allocate_cycle_with_blocks(units, blocks, adjacent_pairs, caps, dims=[OWNER])
    assert not out[out["unit_id"].isin(["a", "b"])]["harvested"].any()
    assert (out[out["unit_id"].isin(["a", "b"])]["blocked_by"] == OWNER).all()


def test_singleton_units_not_in_blocks_frame_still_allocate():
    # Regression: a unit missing from `blocks` (e.g. below adjacency's
    # min_area_ha) must still be individually schedulable, as its own block.
    units = pd.DataFrame({
        "unit_id": ["solo"], "county": ["Baker"], "owner_group": ["Private"],
        "stand_age": [80], "removable_volume": [50.0],
    })
    blocks = pd.DataFrame(columns=["block_id", "unit_id", "block_area_ha", "exceeds_cap"])
    out = allocate_cycle_with_blocks(units, blocks, set(), {TOTAL: {"": 1000.0}}, dims=[TOTAL])
    assert out["harvested"].iloc[0] == True
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_s3_harvest_scheduler.py -k "block" -v`
Expected: FAIL — `allocate_cycle_with_blocks` not defined.

- [ ] **Step 3: Implement**

Append to `pipeline/s3_management/harvest_scheduler.py`:

```python
def allocate_cycle_with_blocks(
    units: pd.DataFrame,
    blocks: pd.DataFrame,
    adjacent_block_pairs: set[tuple[int, int]],
    caps: dict[str, dict[str, float]],
    dims: Sequence[str] = (TOTAL,),
    priority_col: str = "stand_age",
    volume_col: str = "removable_volume",
    cycle_years: int = DEFAULT_CYCLE_YEARS,
) -> pd.DataFrame:
    """
    Allocate one cycle's harvest at block granularity (ARM/URM), per-unit budgets.

    A block harvests only if every one of its member units can afford its own
    volume against its own dimension keys (a block may span counties/owners,
    so this is checked per member, not against a block-summed key). If any
    member is blocked, the whole block is blocked -- ARM commits blocks, not
    individual units. Harvesting a block excludes every block adjacent to it
    (URM) from harvesting in this same cycle, regardless of that neighbor's
    own budget.

    Units absent from `blocks` (e.g. excluded by adjacency's min_area_ha) are
    treated as their own singleton block, so nothing silently drops out of
    scheduling just because it never entered the adjacency graph.
    """
    for dim in dims:
        if dim != TOTAL and dim not in units.columns:
            raise ValueError(f"active dimension {dim!r} is missing from the units table")

    block_lookup = blocks.set_index("unit_id")["block_id"] if not blocks.empty else pd.Series(dtype=object)
    working = units.copy()
    working["block_id"] = working["unit_id"].map(block_lookup).astype(object)
    missing = working["block_id"].isna()
    working.loc[missing, "block_id"] = "solo:" + working.loc[missing, "unit_id"].astype(str)

    budgets = _build_budgets(dims, caps, cycle_years)

    block_priority = working.groupby("block_id")[priority_col].max()
    block_order = block_priority.sort_values(ascending=False).index.tolist()

    harvested: dict = {}
    removed: dict = {}
    blocked: dict = {}
    excluded_blocks: set = set()
    harvested_blocks: set = set()

    for block_id in block_order:
        if block_id in excluded_blocks:
            for idx in working.index[working["block_id"] == block_id]:
                harvested[idx], removed[idx], blocked[idx] = False, 0.0, "urm_adjacent"
            continue

        members = working[working["block_id"] == block_id]
        block_block_reason = ""
        for idx, unit in members.iterrows():
            vol = float(unit[volume_col])
            for dim in dims:
                key = _dim_key(unit, dim)
                if budgets[dim].get(key, 0.0) < vol:
                    block_block_reason = dim
                    break
            if block_block_reason:
                break

        if block_block_reason:
            for idx in members.index:
                harvested[idx], removed[idx], blocked[idx] = False, 0.0, block_block_reason
            continue

        for idx, unit in members.iterrows():
            vol = float(unit[volume_col])
            for dim in dims:
                budgets[dim][_dim_key(unit, dim)] -= vol
            harvested[idx], removed[idx], blocked[idx] = True, vol, ""

        harvested_blocks.add(block_id)
        # `==` rather than `is`/isinstance: block_id may be a numpy.int64 pulled
        # from a pandas column, while adjacent_block_pairs holds plain Python
        # ints from identify_adjacent_block_pairs. np.int64(1) == 1 is True and
        # hashes equal, so this comparison (and set membership elsewhere) is
        # safe across the two types; isinstance(block_id, int) is NOT reliable
        # for numpy scalars and must not be used here.
        for i, j in adjacent_block_pairs:
            if block_id == i:
                excluded_blocks.add(j)
            elif block_id == j:
                excluded_blocks.add(i)

    out = working.copy()
    out["harvested"] = pd.Series(harvested)
    out["volume_removed"] = pd.Series(removed)
    out["blocked_by"] = pd.Series(blocked)
    logger.info(
        "Block cycle allocation: %d/%d blocks harvested, %d/%d units, %.0f cuft removed",
        len(harvested_blocks), working["block_id"].nunique(),
        int(out["harvested"].sum()), len(out), out["volume_removed"].sum(),
    )
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_s3_harvest_scheduler.py -k "block" -v`
Expected: PASS. If `test_urm_prevents_adjacent_blocks_harvesting_same_cycle` fails because block 2 harvests before being excluded, check the sort order in `block_order` — block 1 (max age 80) must sort before block 2 (age 60).

- [ ] **Step 5: Run full file, commit**

```bash
uv run pytest tests/test_s3_harvest_scheduler.py -v
git add pipeline/s3_management/harvest_scheduler.py tests/test_s3_harvest_scheduler.py
git commit -m "feat(s3): block-level constrained allocation with URM same-cycle exclusion"
```

---

### Task 8: Top-level orchestrator + regime assignment on harvested units

**Files:**
- Modify: `pipeline/s3_management/harvest_scheduler.py`
- Test: `tests/test_s3_harvest_scheduler.py`

**Interfaces:**
- Consumes: everything from Tasks 4-7, plus `regime_assignment.assign_regime` (Task 3).
- Produces: `run_scheduling_cycle(basic_units: pd.DataFrame, units: gpd.GeoDataFrame, graph: dict, caps: dict, dims: Sequence[str] = (TOTAL,), minimum_harvestable_percentage: float = 0.5, inv_year: int = 2022, priority_col: str = "stand_age", volume_col: str = "removable_volume", cycle_years: int = DEFAULT_CYCLE_YEARS, constraints: Optional[dict] = None) -> pd.DataFrame` — full pipeline for one cycle: screen → block → allocate → assign regime. Output adds `regime` (`None` for unharvested/ineligible units) and `regime_params` columns to `allocate_cycle_with_blocks`'s output.

- [ ] **Step 1: Write the failing end-to-end test**

Append to `tests/test_s3_harvest_scheduler.py`:

```python
from pipeline.s3_management.harvest_scheduler import run_scheduling_cycle
from pipeline.s3_management.regime_assignment import CORPORATE


def test_run_scheduling_cycle_end_to_end():
    basic_units = pd.DataFrame({
        "unit_id": ["a", "a", "b", "b"],
        "stand_age": [80, 80, 30, 30],  # a is old enough (>=40 industrial MHA), b is not.
        "area_ha": [1.5, 1.5, 1.5, 1.5],
        "OWN_CODE": [CORPORATE, CORPORATE, CORPORATE, CORPORATE],
        "SMZ_Pct": [0.0, 0.0, 0.0, 0.0],
    })
    units_gdf = gpd.GeoDataFrame({
        "unit_id": ["a", "b"],
        "unit_area_ha": [3.0, 3.0],
        "county": ["Baker", "Baker"],
        "owner_group": ["Private", "Private"],
        "stand_age": [80, 30],
        "removable_volume": [500.0, 500.0],
        "OWN_CODE": [CORPORATE, CORPORATE],
        "SMZ_Pct": [0.0, 0.0],
        "FORTYPCD": [161, 161],
        "geometry": [box(0, 0, 100, 150), box(200, 0, 300, 150)],
    }, crs="EPSG:5070")
    graph, _ = build_landscape_graph(units_gdf)

    out = run_scheduling_cycle(
        basic_units, units_gdf, graph, caps={TOTAL: {"": 10000.0}}, dims=[TOTAL],
    )

    a_row = out[out["unit_id"] == "a"].iloc[0]
    b_row = out[out["unit_id"] == "b"].iloc[0]
    assert a_row["harvested"] == True
    assert a_row["regime"] == "plantation_rotation"  # corporate + pine (FORTYPCD 161)
    assert b_row["harvested"] == False  # below MHA -> never a candidate
    assert b_row["regime"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_s3_harvest_scheduler.py -k "end_to_end" -v`
Expected: FAIL — `run_scheduling_cycle` not defined.

- [ ] **Step 3: Implement**

Append to `pipeline/s3_management/harvest_scheduler.py`:

```python
from pipeline.s3_management import regime_assignment


def run_scheduling_cycle(
    basic_units: pd.DataFrame,
    units: gpd.GeoDataFrame,
    graph: dict[str, set[str]],
    caps: dict[str, dict[str, float]],
    dims: Sequence[str] = (TOTAL,),
    minimum_harvestable_percentage: float = 0.5,
    inv_year: int = 2022,
    priority_col: str = "stand_age",
    volume_col: str = "removable_volume",
    cycle_years: int = DEFAULT_CYCLE_YEARS,
    constraints: Optional[dict] = None,
) -> pd.DataFrame:
    """
    Run one full scheduling cycle: eligibility screen -> block assembly ->
    constrained block allocation -> regime assignment for harvested units.

    `basic_units` is the per-cycle, sub-unit-grain frame (age varies by cycle
    as stands grow) used only for eligibility. `units` is the per-unit,
    per-cycle frame carrying geometry, area, removable_volume, and the
    ownership/forest-type attributes regime_assignment needs; it should
    already be filtered to whatever population is a candidate this cycle
    before calling this function (e.g. via the FVS trajectory's regime
    schedule). `graph` is the landscape-wide adjacency graph from
    build_landscape_graph, built once and passed in unchanged across cycles.
    """
    eligibility = screen_unit_eligibility(
        basic_units, minimum_harvestable_percentage, constraints,
    )
    schedulable_ids = eligibility[eligibility["schedulable"]].index

    eligible_units = units[units["unit_id"].isin(schedulable_ids)].copy()
    if eligible_units.empty:
        out = units.copy()
        out["block_id"] = pd.NA
        out["harvested"] = False
        out["volume_removed"] = 0.0
        out["blocked_by"] = "ineligible"
        out["regime"] = None
        out["regime_params"] = None
        return out

    blocks, adjacent_pairs = assemble_cycle_blocks(
        graph, eligible_units, priority_col=priority_col, constraints=constraints,
    )
    allocation = allocate_cycle_with_blocks(
        eligible_units, blocks, adjacent_pairs, caps, dims=dims,
        priority_col=priority_col, volume_col=volume_col, cycle_years=cycle_years,
    )

    ineligible_units = units[~units["unit_id"].isin(schedulable_ids)].copy()
    ineligible_units["block_id"] = pd.NA
    ineligible_units["harvested"] = False
    ineligible_units["volume_removed"] = 0.0
    ineligible_units["blocked_by"] = "ineligible"

    combined = pd.concat([allocation, ineligible_units], ignore_index=True)

    regimes, params = [], []
    for _, row in combined.iterrows():
        if not row["harvested"]:
            regimes.append(None)
            params.append(None)
            continue
        regime, regime_params = regime_assignment.assign_regime(row, inv_year=inv_year)
        regimes.append(regime)
        params.append(regime_params)
    combined["regime"] = regimes
    combined["regime_params"] = params

    return combined
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_s3_harvest_scheduler.py -k "end_to_end" -v`
Expected: PASS.

- [ ] **Step 5: Run the entire test suite for this module and its dependencies**

Run: `uv run pytest tests/test_s3_harvest_scheduler.py tests/test_s3_harvest_eligibility.py tests/test_s3_adjacency.py tests/test_s3_regime_assignment.py tests/test_s4_regime_templates.py -v`
Expected: All PASS, no regressions in the ported test files.

- [ ] **Step 6: Commit**

```bash
git add pipeline/s3_management/harvest_scheduler.py tests/test_s3_harvest_scheduler.py
git commit -m "feat(s3): orchestrate eligibility+adjacency+regime assignment into one scheduling cycle"
```

---

### Task 9: Update the plan doc and module docstring

**Files:**
- Modify: `notes/management-pipeline-plan.md` (Step 4.1 section only)
- Modify: `pipeline/s3_management/harvest_scheduler.py` (module docstring)

**Interfaces:** None (documentation only).

- [ ] **Step 1: Update the module docstring**

At the top of `pipeline/s3_management/harvest_scheduler.py`, replace the existing module docstring's second paragraph (the one starting "The scheduler is a pure allocator...") with:

```python
"""
Constrained harvest scheduler (Phase 4.1).

Allocates harvests across management units, per FVS 5-year cycle, honouring the TPO volume
caps parsed by ``pipeline.s3_management.tpo_targets``, LAMPS eligibility screening
(``harvest_eligibility.py``, MHA/MHP), and LAMPS ARM/URM adjacency blocking
(``adjacency.py``). See `notes/management-pipeline-plan.md` Step 4.1.

  - Eligibility: a unit is a harvest candidate only if it clears its owner group's minimum
    harvest age over enough of its area (``screen_unit_eligibility``). Riparian units
    (``SMZ_Pct >= regime_assignment.RIPARIAN_SMZ_PCT``) are never eligible, full stop.
  - Blocking: eligible units are assembled into contiguous harvest blocks
    (``assemble_cycle_blocks``); a block is harvested as a whole or not at all, and no two
    spatially adjacent blocks harvest in the same cycle (``allocate_cycle_with_blocks``).
  - Regime: every harvested unit gets a silvicultural regime via
    ``regime_assignment.assign_regime`` (deterministic ownership x forest-type x riparian
    rule) once it clears the two constraints above.
  - Priority within the above: **oldest stand first**.
  - TPO caps are annual cubic feet; a cycle budget is ``annual x cycle_years``.

``run_scheduling_cycle`` is the top-level entry point for one cycle. ``allocate_cycle`` (the
original per-unit allocator, no eligibility/blocking) is kept for callers that don't need
the LAMPS constraints. The scheduler is a pure allocator over a units table — it does not
run FVS. It decides *which* units harvest in *which* cycle within the caps; the managed FVS
run and the volume model that supplies ``removable_volume`` are separate steps.

Constraint dimensions can be enabled independently (the plan asks to study each in
isolation, then combined), via the ``dims`` argument.
"""
```

- [ ] **Step 2: Update `notes/management-pipeline-plan.md` Step 4.1**

Find the `### Step 4.1: Build the harvest scheduling engine` section and replace its bullet list (the `1. Load management units...` through `4. Output: ...` list) with:

```markdown
- Core logic in `pipeline/s3_management/harvest_scheduler.py`:
  1. Load management units with attributes (ownership, forest type, SMZ_Pct) and baseline inventory.
  2. Load TPO volume targets (annual or multi-year average).
  3. Build the landscape-wide adjacency graph once (`build_landscape_graph`).
  4. For each time step (5-year FVS cycle):
     - Screen eligibility (`screen_unit_eligibility`): owner-group MHA + MHP (LAMPS Eqs. 5-8). Riparian units (SMZ_Pct >= 50%) are never eligible.
     - Assemble this cycle's harvest blocks from the eligible subset (`assemble_cycle_blocks`, LAMPS Eq. 4) and find adjacent block pairs (URM, LAMPS Eq. 3).
     - Allocate the cycle at block granularity (`allocate_cycle_with_blocks`): a block harvests only if every member unit affords its own county/owner budget; harvesting a block excludes every adjacent block from this same cycle.
     - Assign a silvicultural regime to every harvested unit (`regime_assignment.assign_regime` — deterministic ownership x forest-type x riparian rule, unchanged from Step 3.2).
  5. Output: per-unit harvest schedule (unit_id, block_id, cycle, regime, volume_removed).
- **Verify**: scheduled harvest volumes are within TPO constraints for all four constraint levels, and no two spatially adjacent blocks are ever harvested in the same cycle.
```

- [ ] **Step 3: Commit**

```bash
git add pipeline/s3_management/harvest_scheduler.py notes/management-pipeline-plan.md
git commit -m "docs(s3): document the eligibility+adjacency-aware scheduler in the pipeline plan"
```
