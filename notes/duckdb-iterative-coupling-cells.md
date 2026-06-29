You are identifying the right three pillars:

1. **`fvs_cycle_change`**: the 5-year state transition ledger.
2. **`fvs_removals`**: the management/removal ledger.
3. **`fvs_spatial_crosswalk`**: the bridge from spatial units to FVS simulation units.

FVS can simulate scheduled/conditional management internally through keywords and event-monitor logic, and the database extension can write many outputs depending on which database keywords are enabled. For an Artemis-style external coupling loop, you are basically using FVS as a growth engine, then using your own code to decide what happens next. The official FVS documentation describes FVS as a growth-and-yield simulator with management keyword/event-monitor support, and the DB extension as an output system whose tables depend on selected database output keywords. ([US Forest Service][1])

## Inside FVS vs outside FVS management

| Approach                   | Strengths                                                                                                                                                                                                    | Weaknesses                                                                                                                                                                                                     |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Management inside FVS**  | FVS handles treatment timing, residual stand updates, cut/removal accounting, regeneration, and extension interactions more natively. Better when the prescription is known ahead of time.                   | Harder to express spatial constraints, regional optimization, RL decisions, adaptive thresholds, or management decisions that depend on external raster/vector state.                                          |
| **Management outside FVS** | Better for iterative coupling: run 5 years, inspect state, trigger management, update inputs, rerun. Easier to integrate with TreeMap, FIA, stand polygons, optimization models, RL, or spatial constraints. | You must carefully preserve biological/accounting consistency when modifying tree lists or stand states outside FVS. Removal tracking, regeneration assumptions, and state handoff become your responsibility. |
| **Hybrid**                 | Use FVS for growth and treatment mechanics where possible, but let Artemis decide which stands receive which prescriptions between cycles.                                                                   | Requires disciplined bookkeeping so FVS output, external decisions, and spatial crosswalks stay synchronized.                                                                                                  |

For your workflow, I would make `fvs_cycle_change` the core state table.

---

## Cell 1: cycle-to-cycle change view

```sql
CREATE OR REPLACE VIEW fvs_cycle_change AS
WITH
  ordered AS (
    SELECT
      *,
      LAG(calendar_year) OVER (
        PARTITION BY case_id
        ORDER BY cycle
      ) AS previous_year,

      LAG(age) OVER (
        PARTITION BY case_id
        ORDER BY cycle
      ) AS previous_age,

      LAG(trees_per_acre) OVER (
        PARTITION BY case_id
        ORDER BY cycle
      ) AS previous_tpa,

      LAG(basal_area) OVER (
        PARTITION BY case_id
        ORDER BY cycle
      ) AS previous_ba,

      LAG(sdi) OVER (
        PARTITION BY case_id
        ORDER BY cycle
      ) AS previous_sdi,

      LAG(total_cuft) OVER (
        PARTITION BY case_id
        ORDER BY cycle
      ) AS previous_total_cuft,

      LAG(merch_cuft) OVER (
        PARTITION BY case_id
        ORDER BY cycle
      ) AS previous_merch_cuft,

      LAG(mortality) OVER (
        PARTITION BY case_id
        ORDER BY cycle
      ) AS previous_mortality,

      LAG(accretion) OVER (
        PARTITION BY case_id
        ORDER BY cycle
      ) AS previous_accretion
    FROM
      fvs_trajectory
  )
SELECT
  *,
  calendar_year - previous_year AS interval_years,
  age - previous_age AS delta_age,
  trees_per_acre - previous_tpa AS delta_tpa,
  basal_area - previous_ba AS delta_ba,
  sdi - previous_sdi AS delta_sdi,
  total_cuft - previous_total_cuft AS delta_total_cuft,
  merch_cuft - previous_merch_cuft AS delta_merch_cuft,
  mortality - previous_mortality AS delta_mortality,
  accretion - previous_accretion AS delta_accretion,

  CASE
    WHEN previous_total_cuft IS NULL OR previous_total_cuft = 0 THEN NULL
    ELSE (total_cuft - previous_total_cuft) / previous_total_cuft
  END AS pct_delta_total_cuft,

  CASE
    WHEN previous_ba IS NULL OR previous_ba = 0 THEN NULL
    ELSE (basal_area - previous_ba) / previous_ba
  END AS pct_delta_ba
FROM
  ordered
WHERE
  previous_year IS NOT NULL;
```

Creates a cycle-to-cycle transition view where each row compares the current FVS state to the prior cycle for the same `case_id`. This is the main view for your iterative 5-year coupling because it tells you what changed during each growth interval.

---

## Cell 2: external management trigger candidates

```sql
CREATE OR REPLACE VIEW fvs_management_candidates AS
SELECT
  case_id,
  stand_cn,
  stand_id,
  management_id,
  run_title,
  calendar_year,
  cycle,
  age,
  trees_per_acre,
  basal_area,
  sdi,
  total_cuft,
  merch_cuft,
  delta_tpa,
  delta_ba,
  delta_sdi,
  delta_total_cuft,

  CASE
    WHEN basal_area >= 120 THEN TRUE
    ELSE FALSE
  END AS trigger_high_ba,

  CASE
    WHEN sdi >= 450 THEN TRUE
    ELSE FALSE
  END AS trigger_high_sdi,

  CASE
    WHEN trees_per_acre >= 600 THEN TRUE
    ELSE FALSE
  END AS trigger_high_tpa,

  CASE
    WHEN delta_total_cuft < 0 THEN TRUE
    ELSE FALSE
  END AS trigger_volume_decline,

  CASE
    WHEN basal_area >= 120
      OR sdi >= 450
      OR trees_per_acre >= 600
      OR delta_total_cuft < 0
    THEN TRUE
    ELSE FALSE
  END AS management_candidate
FROM
  fvs_cycle_change;
```

Creates a threshold-screening view for external management decisions. The numeric thresholds are placeholders, but the structure is useful because Artemis can query this view after each 5-year FVS run and decide which stands need treatment.

You would tune these fields to your actual prescription logic. For example:

```text
thin if BA > threshold
thin if SDI > threshold
harvest if age > rotation age
salvage if volume drops sharply
burn if fuel/fire outputs exceed threshold
```

---

## Cell 3: removals view

```sql
CREATE OR REPLACE VIEW fvs_removals AS
SELECT
  c.Stand_CN AS stand_cn,
  s.StandID AS stand_id,
  s.CaseID AS case_id,
  c.MgmtID AS management_id,
  c.RunTitle AS run_title,
  c.SamplingWt AS sampling_weight,
  c.Variant AS variant,
  c.Version AS fvs_version,

  s."Year" AS calendar_year,
  s.RmvCode AS removal_code,

  s.Age AS age,
  s.Tpa AS removed_trees_per_acre,
  s.BA AS removed_basal_area,
  s.SDI AS removed_sdi,
  s.CCF AS removed_ccf,
  s.TopHt AS removed_top_height,
  s.QMD AS removed_qmd,
  s.TCuFt AS removed_total_cuft,
  s.MCuFt AS removed_merch_cuft,
  s.SCuFt AS removed_sawlog_cuft,
  s.BdFt AS removed_board_ft,
  s.ForTyp AS forest_type,
  s.SizeCls AS size_class,
  s.StkCls AS stocking_class
FROM
  FVSout.FVS_Summary2 s
  LEFT JOIN FVSout.FVS_Cases c ON s.CaseID = c.CaseID
WHERE
  s.RmvCode <> 0;
```

Creates a view of non-residual `FVS_Summary2` rows, which your current `fvs_trajectory` intentionally excludes with `RmvCode = 0`. This is especially important when management is handled inside FVS, because removal rows can explain sudden changes in basal area, TPA, and volume.

---

## Cell 4: removal summary by cycle

```sql
CREATE OR REPLACE VIEW fvs_removal_summary AS
SELECT
  case_id,
  stand_cn,
  stand_id,
  management_id,
  calendar_year,
  removal_code,

  SUM(removed_trees_per_acre) AS removed_tpa,
  SUM(removed_basal_area) AS removed_ba,
  SUM(removed_total_cuft) AS removed_total_cuft,
  SUM(removed_merch_cuft) AS removed_merch_cuft,
  SUM(removed_board_ft) AS removed_board_ft
FROM
  fvs_removals
GROUP BY
  case_id,
  stand_cn,
  stand_id,
  management_id,
  calendar_year,
  removal_code;
```

Aggregates removal records by stand, case, year, and removal code. This is useful for checking whether a management event actually produced removals and for comparing FVS-internal treatments against externally scripted treatments.

---

## Cell 5: cycle ledger with removals attached

```sql
CREATE OR REPLACE VIEW fvs_cycle_ledger AS
SELECT
  cc.case_id,
  cc.stand_cn,
  cc.stand_id,
  cc.management_id,
  cc.run_title,
  cc.variant,
  cc.fvs_version,
  cc.sampling_weight,

  cc.previous_year,
  cc.calendar_year,
  cc.interval_years,
  cc.cycle,

  cc.age,
  cc.trees_per_acre,
  cc.basal_area,
  cc.sdi,
  cc.total_cuft,
  cc.merch_cuft,

  cc.delta_tpa,
  cc.delta_ba,
  cc.delta_sdi,
  cc.delta_total_cuft,
  cc.delta_merch_cuft,

  COALESCE(rs.removed_tpa, 0) AS removed_tpa,
  COALESCE(rs.removed_ba, 0) AS removed_ba,
  COALESCE(rs.removed_total_cuft, 0) AS removed_total_cuft,
  COALESCE(rs.removed_merch_cuft, 0) AS removed_merch_cuft,
  COALESCE(rs.removed_board_ft, 0) AS removed_board_ft,

  CASE
    WHEN rs.case_id IS NOT NULL THEN TRUE
    ELSE FALSE
  END AS had_removal
FROM
  fvs_cycle_change cc
  LEFT JOIN fvs_removal_summary rs
    ON cc.case_id = rs.case_id
    AND cc.calendar_year = rs.calendar_year;
```

Combines the live-state transition and removal accounting into one analysis table. This is probably the most useful view for comparing “growth-only change” against “change caused by treatment/removal.”

---

## Cell 6: spatial crosswalk skeleton

This one depends on your actual table names, but structurally it should look like this:

```sql
CREATE OR REPLACE VIEW fvs_spatial_crosswalk AS
SELECT DISTINCT
  tm.treemap_id,
  tm.pixel_value AS treemap_pixel_value,

  fia.plot_cn,
  fia.cond_cn,
  fia.stand_cn,

  stands.management_unit_id,
  stands.stand_polygon_id,
  stands.stand_id AS spatial_stand_id,

  c.StandID AS fvs_stand_id,
  c.CaseID AS case_id,
  c.MgmtID AS management_id,
  c.SamplingWt AS sampling_weight
FROM
  treemap_assignments tm
  LEFT JOIN fia_plot_assignments fia
    ON tm.plot_cn = fia.plot_cn
  LEFT JOIN management_stands stands
    ON fia.stand_cn = stands.stand_cn
  LEFT JOIN FVSout.FVS_Cases c
    ON fia.stand_cn = c.Stand_CN;
```

Creates the conceptual bridge from TreeMap raster IDs to FIA plots, management stands, and FVS cases. You will need to replace `treemap_assignments`, `fia_plot_assignments`, and `management_stands` with your real imported table names.

For this project, I would treat this not as a casual helper view, but as a **core data product**. It should probably include fields like:

```text
treemap_id
pixel_value
plot_cn
cond_cn
stand_cn
management_unit_id
stand_polygon_id
case_id
stand_id
management_id
area_acres
assignment_method
assignment_confidence
source_dataset
```

---

## Cell 7: crosswalk audit

```sql
CREATE OR REPLACE VIEW fvs_spatial_crosswalk_audit AS
SELECT
  treemap_id,

  COUNT(*) AS rows_per_treemap_id,
  COUNT(DISTINCT plot_cn) AS plot_count,
  COUNT(DISTINCT stand_cn) AS stand_cn_count,
  COUNT(DISTINCT management_unit_id) AS management_unit_count,
  COUNT(DISTINCT case_id) AS case_count,

  CASE
    WHEN COUNT(DISTINCT case_id) = 0 THEN 'missing_fvs_case'
    WHEN COUNT(DISTINCT case_id) = 1 THEN 'ok_single_case'
    ELSE 'multiple_fvs_cases'
  END AS case_link_status,

  CASE
    WHEN COUNT(DISTINCT management_unit_id) = 0 THEN 'missing_management_unit'
    WHEN COUNT(DISTINCT management_unit_id) = 1 THEN 'ok_single_management_unit'
    ELSE 'multiple_management_units'
  END AS management_link_status
FROM
  fvs_spatial_crosswalk
GROUP BY
  treemap_id;
```

Checks whether each TreeMap ID maps cleanly to one FVS case and one management unit. This is essential because rasterization will fail conceptually if one raster ID ambiguously maps to multiple FVS trajectories without an explicit weighting or assignment rule.

---

## Cell 8: raster-ready state by TreeMap ID

```sql
CREATE OR REPLACE VIEW fvs_raster_ready AS
SELECT
  x.treemap_id,
  x.treemap_pixel_value,
  x.management_unit_id,
  x.stand_polygon_id,
  x.plot_cn,
  x.cond_cn,
  x.stand_cn,

  l.case_id,
  l.stand_id,
  l.management_id,
  l.calendar_year,
  l.cycle,

  l.trees_per_acre,
  l.basal_area,
  l.sdi,
  l.total_cuft,
  l.merch_cuft,
  l.delta_tpa,
  l.delta_ba,
  l.delta_sdi,
  l.delta_total_cuft,
  l.removed_tpa,
  l.removed_ba,
  l.removed_total_cuft,
  l.removed_merch_cuft,
  l.had_removal
FROM
  fvs_spatial_crosswalk x
  INNER JOIN fvs_cycle_ledger l
    ON x.case_id = l.case_id;
```

Creates the practical export table for mapping FVS outputs back to TreeMap spatial units. This is the table you would filter by year and metric before reclassifying the TreeMap raster into a projected GeoTIFF.

---

## Recommended structure for Artemis

I would organize the DuckDB views like this:

```text
Raw FVS output
    FVSout.FVS_Summary2
    FVSout.FVS_Cases

Trajectory layer
    fvs_trajectory
    fvs_cycle_change
    fvs_stand_change

Management layer
    fvs_management_candidates
    fvs_removals
    fvs_removal_summary
    fvs_cycle_ledger

Spatial layer
    fvs_spatial_crosswalk
    fvs_spatial_crosswalk_audit

Export layer
    fvs_raster_ready
```

The most important one for your iterative coupling is probably:

```text
fvs_cycle_ledger
```

because it combines:

```text
previous state
current state
5-year change
removals
management metadata
case identity
stand identity
```

Then the most important one for GeoTIFF generation is:

```text
fvs_raster_ready
```

because it binds those projected states back to TreeMap IDs.

[1]: https://www.fs.usda.gov/sites/default/files/forest-management/essential-fvs.pdf?utm_source=chatgpt.com "Essential FVS: A User's Guide to the Forest Vegetation ..."
