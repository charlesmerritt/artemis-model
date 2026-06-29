# ============================================================
# Florida Forest Inventory Comparison: FIA vs TreeMap 2022
# ============================================================
# Purpose: Generate state-level summaries of BALIVE, TPA_LIVE,
#          and CARBON_L from both FIA (via rFIA) and TreeMap 2022
#          (via terra), and compare the two data sources.
#
# Data sources:
#   FIA:     output/fia_FL/         (Florida FIA CSV files)
#   TreeMap: RDS-2025-0032/Data/    (CONUS TreeMap raster + VAT)
#
# Reference: Houtman et al. 2025. TreeMap 2022 CONUS.
#            https://doi.org/10.2737/RDS-2025-0032
# ============================================================

# ============================================================
# NOTE: Test of mean-balancing (scaling) TreeMap estimates to FIA based on Iles (2009);
# International Journal of Mathematical and Computational Forestry (Vol 1, No 1, pp. 10–13, 2009)
# ============================================================
# The script is organized into four clearly delineated sections:
# Section 1 — FIA summaries via rFIA, including state-level totals and forest-type breakdowns for area, TPA, and carbon.
# Section 2 — TreeMap processing via terra, from loading the CONUS raster through cropping, masking, pixel frequency extraction, VAT joining, and forest type summarization. Key cautions about as.int() and the VAT auto-loading behavior are documented inline.
# Section 3 — Side-by-side FIA vs TreeMap state-level comparison table.
# Section 4 — Uncertainty characterization, including the plot-level distribution analysis, pre-scaling bias quantification, area-scaling adjustment, and the before/after bias decomposition.
# All intermediate and final outputs are written to the output/ directory with consistent naming.
# ============================================================

library(rFIA)
library(terra)
library(geodata)
library(dplyr)


# ============================================================
# SECTION 1: FIA STATE-LEVEL SUMMARY
# ============================================================

# ---- 1.1 Load Florida FIA data ----
# readFIA() reads all standard FIA CSV files from the directory
# (e.g., FL_PLOT.csv, FL_TREE.csv). Since only FL data are loaded,
# all estimates are automatically state-level.
db <- readFIA(dir = "output/fia_FL", common = TRUE)

# Inspect loaded tables and available evaluation years
print(names(db))
db$POP_EVAL %>% select(EVALID, EVAL_DESCR, END_INVYR)


# ---- 1.2 BALIVE & TPA_LIVE ----
# tpa() returns basal area per acre (BAA, sq ft/ac) and trees per
# acre (TPA) with standard errors, plus AREA_TOTAL (forested acres)
# and totals when totals = TRUE.
# treeDomain restricts to live trees (STATUSCD == 1).
balive_tpa <- tpa(
  db,
  treeDomain = STATUSCD == 1,
  landType   = "forest",
  variance   = FALSE,
  totals     = TRUE
)

print("--- FIA: BALIVE & TPA per acre ---")
print(balive_tpa)


# ---- 1.3 CARBON_L: Live Aboveground Carbon ----
# carbon() returns estimates for multiple pools. Filter to AG_LIVE
# to match TreeMap's CARBON_L definition (live aboveground, tons/ac).
carbon_est <- carbon(
  db,
  landType = "forest",
  variance = FALSE,
  totals   = TRUE
)

print("--- FIA: Carbon pools available ---")
print(carbon_est)

carbon_live <- carbon_est %>%
  filter(POOL == "AG_LIVE") %>%
  select(YEAR, CARB_ACRE, CARB_ACRE_SE, CARB_TOTAL, CARB_TOTAL_SE)


# ---- 1.4 Combine FIA summary ----
fia_summary <- left_join(balive_tpa, carbon_live, by = "YEAR")

print("=== FIA Summary Table ===")
print(fia_summary)

write.csv(fia_summary, "output/FL_FIA_summary.csv", row.names = FALSE)
cat("FIA summary saved to output/FL_FIA_summary.csv\n")


# ---- 1.5 FIA forest type area summary ----
# Used later for area-scaling adjustment and uncertainty analysis.
fia_type_ba <- tpa(
  db,
  grpBy      = FORTYPCD,
  treeDomain = STATUSCD == 1,
  landType   = "forest",
  variance   = TRUE
)

fia_type_carb <- carbon(
  db,
  grpBy    = FORTYPCD,
  landType = "forest",
  variance = TRUE
) %>% filter(POOL == "AG_LIVE")

fia_area <- area(
  db,
  grpBy    = FORTYPCD,
  landType = "forest",
  variance = TRUE
)

write.csv(fia_type_ba,   "output/FL_FIA_ForestType_tpa.csv",    row.names = FALSE)
write.csv(fia_type_carb, "output/FL_FIA_ForestType_carbon.csv", row.names = FALSE)
write.csv(fia_area,      "output/FL_FIA_ForestType_area.csv",   row.names = FALSE)
cat("FIA forest type summaries saved.\n")


# ============================================================
# SECTION 2: TREEMAP STATE-LEVEL SUMMARY
# ============================================================

# ---- 2.1 Load TreeMap raster ----
# terra automatically loads the VAT (.tif.vat.dbf) as the raster's
# attribute table when rast() is called. Do NOT load the .dbf
# separately -- terra attaches it automatically.
tm <- rast("RDS-2025-0032/Data/TreeMap2022_CONUS.tif")

# Inspect raster and confirm VAT loaded correctly
print(tm)
cat("\nVAT column names:\n")
print(names(cats(tm)[[1]]))
cat("\nVAT (first few rows):\n")
head(cats(tm)[[1]])

# Note: the raster layer 'name' displays as "ForTypName" because terra
# sets the active category to the last categorical column in the VAT.
# This is cosmetic and does not affect analysis. cats(tm)[[1]]$Value
# correctly contains the integer TM_ID pixel values used for joining.


# ---- 2.2 Load Florida boundary and reproject ----
# Uses geodata package to retrieve state boundary.
# Alternative options (commented out):
#   Option B - tigris:
#     library(tigris)
#     fl_boundary <- vect(states(cb=TRUE) %>% subset(STUSPS=="FL"))
#   Option C - local shapefile:
#     fl_boundary <- vect("path/to/FL_boundary.shp")
usa         <- gadm(country = "USA", level = 1, path = tempdir())
fl_boundary <- usa[usa$NAME_1 == "Florida", ]
fl_proj     <- project(fl_boundary, crs(tm))


# ---- 2.3 Crop and mask raster to Florida ----
cat("\nCropping to Florida extent...\n")
tm_fl_crop <- crop(tm, fl_proj)

cat("Masking to Florida boundary...\n")
tm_fl <- mask(tm_fl_crop, fl_proj)

cat("Florida TreeMap raster:\n")
print(tm_fl)


# ---- 2.4 Extract pixel counts by TM_ID ----
# freq() must operate on raw integer values, not the categorical
# (ForTypName) layer. as.int() strips the VAT association so that
# freq() returns TM_ID integers rather than forest type name strings.
cat("\nCalculating pixel frequencies (may take several minutes)...\n")
tm_fl_int  <- as.int(tm_fl)
pixel_freq <- freq(tm_fl_int, bylayer = FALSE)
names(pixel_freq) <- c("layer", "Value", "pixel_count")
pixel_freq <- pixel_freq %>% select(Value, pixel_count)

cat(paste0("Unique TM_ID values in Florida: ", nrow(pixel_freq), "\n"))
cat(paste0("Total forested pixels:          ", sum(pixel_freq$pixel_count), "\n"))
head(pixel_freq)


# ---- 2.5 Join pixel counts to VAT attributes ----
# VAT is retrieved from the original raster (tm), which retains
# the full attribute table including BALIVE, TPA_LIVE, CARBON_L.
vat <- cats(tm)[[1]]

cat("\nVAT join key check:\n")
print(head(vat %>% select(Value, BALIVE, TPA_LIVE, CARBON_L)))

fl_data <- pixel_freq %>%
  left_join(vat, by = "Value") %>%
  filter(!is.na(BALIVE))   # remove unmatched or non-forest pixels


# ---- 2.6 Compute pixel area in acres ----
# Each 30m x 30m pixel = 900 sq m = 0.22239 acres
ACRES_PER_PIXEL <- 900 / 4046.8564

fl_data <- fl_data %>%
  mutate(pixel_acres = pixel_count * ACRES_PER_PIXEL)

cat(paste0("\nTotal forested acres (TreeMap): ",
           round(sum(fl_data$pixel_acres), 0), "\n"))


# ---- 2.7 State-level TreeMap summary ----
# Per-acre estimates use area-weighted means.
# Totals multiply per-acre estimates by pixel area.
summary_treemap <- fl_data %>%
  summarise(
    STATE             = "FL",
    SOURCE            = "TreeMap2022",
    N_PIXELS          = sum(pixel_count),
    FOREST_ACRES      = sum(pixel_acres),
    BAA_sqft_ac       = sum(BALIVE   * pixel_acres) / sum(pixel_acres),
    TPA_live_ac       = sum(TPA_LIVE * pixel_acres) / sum(pixel_acres),
    Carbon_tons_ac    = sum(CARBON_L * pixel_acres) / sum(pixel_acres),
    BA_Total_sqft     = sum(BALIVE   * pixel_acres),
    TPA_Total_trees   = sum(TPA_LIVE * pixel_acres),
    Carbon_Total_tons = sum(CARBON_L * pixel_acres)
  )

print("=== TreeMap Florida Summary ===")
print(t(summary_treemap))

write.csv(summary_treemap, "output/FL_TreeMap_summary.csv", row.names = FALSE)
cat("TreeMap summary saved to output/FL_TreeMap_summary.csv\n")


# ---- 2.8 TreeMap forest type summary ----
fortyp_summary <- fl_data %>%
  group_by(FORTYPCD, ForTypName) %>%
  summarise(
    N_PIXELS       = sum(pixel_count),
    FOREST_ACRES   = sum(pixel_acres),
    BAA_sqft_ac    = sum(BALIVE   * pixel_acres) / sum(pixel_acres),
    TPA_live_ac    = sum(TPA_LIVE * pixel_acres) / sum(pixel_acres),
    Carbon_tons_ac = sum(CARBON_L * pixel_acres) / sum(pixel_acres),
    .groups = "drop"
  ) %>%
  mutate(PCT_ACRES = round(100 * FOREST_ACRES / sum(FOREST_ACRES), 2)) %>%
  arrange(desc(FOREST_ACRES))

print("=== TreeMap Forest Type Summary ===")
print(fortyp_summary, n = Inf)

write.csv(fortyp_summary, "output/FL_TreeMap_ForestType_summary.csv",
          row.names = FALSE)
cat("TreeMap forest type summary saved.\n")


# ============================================================
# SECTION 3: FIA vs TREEMAP COMPARISON
# ============================================================

# ---- 3.1 Side-by-side state-level comparison ----
fia_compare <- fia_summary %>%
  filter(YEAR == max(YEAR)) %>%
  mutate(SOURCE = "FIA") %>%
  select(SOURCE,
         FOREST_ACRES    = AREA_TOTAL,
         BAA_sqft_ac     = BAA,
         TPA_live_ac     = TPA,
         Carbon_tons_ac  = CARB_ACRE,
         BA_Total_sqft   = BAA_TOTAL,
         TPA_Total_trees = TPA_TOTAL,
         Carbon_Total_tons = CARB_TOTAL)

tm_compare <- summary_treemap %>%
  select(SOURCE, FOREST_ACRES, BAA_sqft_ac, TPA_live_ac, Carbon_tons_ac,
         BA_Total_sqft, TPA_Total_trees, Carbon_Total_tons)

comparison <- bind_rows(fia_compare, tm_compare) %>%
  mutate(across(where(is.numeric), ~ round(.x, 2)))

print("=== FIA vs TreeMap State-Level Comparison ===")
print(t(comparison))

write.csv(comparison, "output/FL_FIA_TreeMap_comparison.csv", row.names = FALSE)
cat("Comparison table saved to output/FL_FIA_TreeMap_comparison.csv\n")


# ============================================================
# SECTION 4: UNCERTAINTY CHARACTERIZATION
# ============================================================
# Approach: Use PLT_CN stored in the VAT to trace each Florida pixel
# back to its assigned FIA plot. Compare the plot-level attribute
# distribution (what TreeMap drew from) to FIA's design-based
# estimates (the population truth) within each forest type.
#
# Two quantities are separated:
#   (1) Area misallocation bias  -- correctable by scaling
#   (2) Within-type attribute bias -- NOT correctable by scaling


# ---- 4.1 Plot-level summary by forest type ----
# Each TM_ID maps to exactly one PLT_CN (confirmed: n_TM_IDs = 1
# for all plots in the Florida subset).
plot_type_summary <- fl_data %>%
  group_by(FORTYPCD, ForTypName, PLT_CN) %>%
  summarise(
    plot_pixels = sum(pixel_count),
    plot_acres  = sum(pixel_acres),
    BALIVE      = first(BALIVE),     # per-acre value is constant per PLT_CN
    TPA_LIVE    = first(TPA_LIVE),
    CARBON_L    = first(CARBON_L),
    .groups = "drop"
  )

# Check leverage: are pixel assignments concentrated on few plots?
cat("\nPlot pixel distribution summary:\n")
plot_type_summary %>%
  group_by(PLT_CN) %>%
  summarise(total_pixels = sum(plot_pixels),
            total_acres  = sum(plot_acres)) %>%
  summary() %>% print()


# ---- 4.2 Within-type attribute distribution ----
# Pixel-weighted mean matches TreeMap's reported per-acre estimates.
# Pixel-weighted SD and CV characterize spread of imputed plot values.
# ESS (effective sample size) reveals plot assignment concentration.
type_plot_dist <- plot_type_summary %>%
  group_by(FORTYPCD, ForTypName) %>%
  summarise(
    N_PLOTS      = n(),
    N_PIXELS     = sum(plot_pixels),
    TM_ACRES     = sum(plot_acres),

    TM_BAA_mean  = sum(BALIVE   * plot_acres) / sum(plot_acres),
    TM_TPA_mean  = sum(TPA_LIVE * plot_acres) / sum(plot_acres),
    TM_CARB_mean = sum(CARBON_L * plot_acres) / sum(plot_acres),

    TM_BAA_sd    = sqrt(sum(plot_acres * (BALIVE   - TM_BAA_mean)^2)  / sum(plot_acres)),
    TM_TPA_sd    = sqrt(sum(plot_acres * (TPA_LIVE - TM_TPA_mean)^2)  / sum(plot_acres)),
    TM_CARB_sd   = sqrt(sum(plot_acres * (CARBON_L - TM_CARB_mean)^2) / sum(plot_acres)),

    TM_BAA_CV    = TM_BAA_sd  / TM_BAA_mean,
    TM_TPA_CV    = TM_TPA_sd  / TM_TPA_mean,
    TM_CARB_CV   = TM_CARB_sd / TM_CARB_mean,

    # ESS: low values indicate few plots dominate -- higher model uncertainty
    ESS          = sum(plot_acres)^2 / sum(plot_acres^2),

    .groups = "drop"
  )


# ---- 4.3 Join FIA design-based estimates and compute bias ----
uncertainty_before <- type_plot_dist %>%
  left_join(
    fia_type_ba %>% select(FORTYPCD, FIA_BAA = BAA, FIA_BAA_SE = BAA_SE,
                            FIA_TPA = TPA, FIA_TPA_SE = TPA_SE),
    by = "FORTYPCD"
  ) %>%
  left_join(
    fia_type_carb %>% select(FORTYPCD, FIA_CARB = CARB_ACRE,
                              FIA_CARB_SE = CARB_ACRE_SE),
    by = "FORTYPCD"
  ) %>%
  left_join(
    fia_area %>% select(FORTYPCD, FIA_ACRES = AREA_TOTAL,
                         FIA_ACRES_SE = AREA_TOTAL_SE),
    by = "FORTYPCD"
  ) %>%
  mutate(
    # Absolute and percent bias: TreeMap vs FIA per-acre estimates
    BAA_bias_abs   = TM_BAA_mean  - FIA_BAA,
    TPA_bias_abs   = TM_TPA_mean  - FIA_TPA,
    CARB_bias_abs  = TM_CARB_mean - FIA_CARB,

    BAA_bias_pct   = 100 * BAA_bias_abs  / FIA_BAA,
    TPA_bias_pct   = 100 * TPA_bias_abs  / FIA_TPA,
    CARB_bias_pct  = 100 * CARB_bias_abs / FIA_CARB,

    # z-score: is FIA's estimate within TreeMap's within-type spread?
    # |z| > 2 suggests FIA falls outside the imputed plot distribution
    BAA_z          = BAA_bias_abs  / TM_BAA_sd,
    TPA_z          = TPA_bias_abs  / TM_TPA_sd,
    CARB_z         = CARB_bias_abs / TM_CARB_sd,

    # Area discrepancy
    AREA_SCALE     = FIA_ACRES / TM_ACRES,
    AREA_PCT_DIFF  = 100 * (TM_ACRES - FIA_ACRES) / FIA_ACRES
  ) %>%
  arrange(desc(abs(BAA_bias_pct)))

print("=== Uncertainty Before Scaling (sorted by BAA % bias) ===")
print(uncertainty_before, n = Inf)

write.csv(uncertainty_before, "output/FL_uncertainty_before_scaling.csv",
          row.names = FALSE)
cat("Pre-scaling uncertainty saved.\n")


# ---- 4.4 Area-scaling adjustment ----
# Reweight TreeMap per-acre estimates using FIA area as weights.
# This corrects area misallocation but cannot correct within-type
# attribute bias -- per-acre estimates remain as TreeMap imputed them.
area_comparison <- fortyp_summary %>%
  select(FORTYPCD, ForTypName, TM_ACRES = FOREST_ACRES,
         TM_BAA = BAA_sqft_ac, TM_TPA = TPA_live_ac,
         TM_CARB = Carbon_tons_ac) %>%
  left_join(
    fia_area %>% select(FORTYPCD, FIA_ACRES = AREA_TOTAL,
                         FIA_ACRES_SE = AREA_TOTAL_SE),
    by = "FORTYPCD"
  ) %>%
  mutate(
    AREA_SCALE = FIA_ACRES / TM_ACRES,
    PCT_DIFF   = 100 * (TM_ACRES - FIA_ACRES) / FIA_ACRES
  )

# Adjusted state-level summary using FIA area weights
adjusted_summary <- area_comparison %>%
  filter(!is.na(FIA_ACRES)) %>%
  summarise(
    FIA_TOTAL_ACRES   = sum(FIA_ACRES,  na.rm = TRUE),
    BAA_adj_ac        = sum(TM_BAA  * FIA_ACRES, na.rm = TRUE) / sum(FIA_ACRES, na.rm = TRUE),
    TPA_adj_ac        = sum(TM_TPA  * FIA_ACRES, na.rm = TRUE) / sum(FIA_ACRES, na.rm = TRUE),
    CARB_adj_ac       = sum(TM_CARB * FIA_ACRES, na.rm = TRUE) / sum(FIA_ACRES, na.rm = TRUE),
    BAA_adj_total     = sum(TM_BAA  * FIA_ACRES, na.rm = TRUE),
    TPA_adj_total     = sum(TM_TPA  * FIA_ACRES, na.rm = TRUE),
    CARB_adj_total    = sum(TM_CARB * FIA_ACRES, na.rm = TRUE)
  )

print("=== Area-Scaled TreeMap Summary ===")
print(t(adjusted_summary))

write.csv(adjusted_summary, "output/FL_TreeMap_scaled_summary.csv",
          row.names = FALSE)


# ---- 4.5 Bias decomposition: before vs after scaling ----
# Separates area misallocation bias (fixed by scaling) from
# within-type attribute bias (not fixed by scaling).
uncertainty_after <- uncertainty_before %>%
  mutate(
    # Weighted contribution to state-level bias before scaling (TM area weights)
    BAA_contrib_before  = BAA_bias_abs  * TM_ACRES,
    TPA_contrib_before  = TPA_bias_abs  * TM_ACRES,
    CARB_contrib_before = CARB_bias_abs * TM_ACRES,

    # Weighted contribution to state-level bias after scaling (FIA area weights)
    BAA_contrib_after   = BAA_bias_abs  * FIA_ACRES,
    TPA_contrib_after   = TPA_bias_abs  * FIA_ACRES,
    CARB_contrib_after  = CARB_bias_abs * FIA_ACRES,

    # Change in bias contribution due to scaling (negative = improvement)
    BAA_scaling_effect  = BAA_contrib_after  - BAA_contrib_before,
    TPA_scaling_effect  = TPA_contrib_after  - TPA_contrib_before,
    CARB_scaling_effect = CARB_contrib_after - CARB_contrib_before
  )

# State-level bias summary
cat("\n=== State-Level Bias Decomposition ===\n")
cat("\nBAA (sq ft/ac):\n")
cat("  Before scaling:", round(sum(uncertainty_after$BAA_contrib_before, na.rm=TRUE) /
                               sum(uncertainty_after$TM_ACRES, na.rm=TRUE), 3), "\n")
cat("  After scaling: ", round(sum(uncertainty_after$BAA_contrib_after,  na.rm=TRUE) /
                               sum(uncertainty_after$FIA_ACRES, na.rm=TRUE), 3), "\n")

cat("\nTPA (trees/ac):\n")
cat("  Before scaling:", round(sum(uncertainty_after$TPA_contrib_before, na.rm=TRUE) /
                               sum(uncertainty_after$TM_ACRES, na.rm=TRUE), 3), "\n")
cat("  After scaling: ", round(sum(uncertainty_after$TPA_contrib_after,  na.rm=TRUE) /
                               sum(uncertainty_after$FIA_ACRES, na.rm=TRUE), 3), "\n")

cat("\nCarbon (tons/ac):\n")
cat("  Before scaling:", round(sum(uncertainty_after$CARB_contrib_before, na.rm=TRUE) /
                               sum(uncertainty_after$TM_ACRES, na.rm=TRUE), 3), "\n")
cat("  After scaling: ", round(sum(uncertainty_after$CARB_contrib_after,  na.rm=TRUE) /
                               sum(uncertainty_after$FIA_ACRES, na.rm=TRUE), 3), "\n")

write.csv(uncertainty_after, "output/FL_uncertainty_after_scaling.csv",
          row.names = FALSE)
cat("\nPost-scaling uncertainty saved to output/FL_uncertainty_after_scaling.csv\n")

cat("\n=== All outputs written. Analysis complete. ===\n")
