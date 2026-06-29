# ============================================================
# Multistate FIA PLT_CN Search, SQLite Subsetting, and
# TreeMap Five-County Summary by State
# ============================================================
#
# PURPOSE:
#   TreeMap 2022 assigns FIA plot identifiers (PLT_CNs) to every
#   forested 30m pixel in the CONUS raster. For a five-county study
#   area in north Florida (Hamilton, Baker, Columbia, Union, Suwannee),
#   the spatial TreeMap analysis identified 693 unique PLT_CNs. Because
#   TreeMap draws plots from anywhere in the national FIA database --
#   not just from the state being mapped -- these PLT_CNs may reside
#   in FIA databases for states other than Florida. This script:
#
#   1. Searches a prioritized list of state SQLite FIA databases for
#      each unmatched PLT_CN, working through states sequentially
#      until all PLT_CNs are accounted for or the list is exhausted.
#
#   2. Creates a subsetted SQLite database for each state where
#      matching PLT_CNs are found, retaining only the records needed
#      for subsequent rFIA analysis of the five-county area.
#
#   3. Produces TreeMap-based forest attribute summaries (BALIVE,
#      TPA_LIVE, CARBON_L) for:
#        a. Any unmatched PLT_CNs, compared against matched PLT_CNs
#           to assess whether missing plots bias the summary.
#        b. Found PLT_CNs broken out by the state database in which
#           they were located, with per-acre and forest type summaries
#           for each state and a combined TOTAL column computed
#           directly from pixel-level data (no double-summarisation).
#
# INPUTS:
#   output/FL_5county_TreeMap_TMIDs.csv  -- TM_ID/PLT_CN/attribute
#     table from subset_TreeMap_5county.R; one row per unique TM_ID
#     in the five-county TreeMap raster, with pixel_count, BALIVE,
#     TPA_LIVE, CARBON_L, FORTYPCD, ForTypName, and PLT_CN columns.
#   output/SQLite_FIADB_XX.db            -- state FIA SQLite databases
#     downloaded from the FIA DataMart (FIADB version 9.x).
#
# OUTPUTS:
#   output/FIA_XX_5county_TMID.db        -- subsetted SQLite DB per state
#   output/FL_5county_unmatched_PLTCNs.csv
#   output/FL_5county_unmatched_summary.csv
#   output/FL_5county_unmatched_fortype.csv
#   output/FL_5county_found_by_state_summary.csv
#   output/FL_5county_found_by_state_fortype.csv
#
# NOTES ON PIXEL AREA:
#   Each 30m x 30m TreeMap pixel = 900 sq m = 0.22239 acres
#   (900 / 4046.8564). All "FOREST_ACRES" values are derived from
#   pixel counts using this conversion.
#
# NOTES ON PER-ACRE ESTIMATES:
#   All per-acre summaries use area-weighted means:
#     weighted_mean = sum(attribute * pixel_acres) / sum(pixel_acres)
#   This is equivalent to the TreeMap pixel-level summarisation used
#   throughout the state-level analysis (subset_TreeMap_5county.R).
# ============================================================

library(DBI)       # database interface
library(RSQLite)   # SQLite backend for DBI
library(dplyr)     # data manipulation
library(knitr)     # kable() for formatted console tables


# ============================================================
# SECTION 1: FILE PATHS AND CONFIGURATION
# ============================================================

# Path to TM_ID/PLT_CN table produced by subset_TreeMap_5county.R
output_path<- "output2020"
tmid_csv <- file.path(output_path,"FL_5county_TreeMap_TMIDs.csv")

# Source FIA SQLite databases -- searched in order from most to least
# likely to contain Florida-area plots. Florida is searched first
# since most plots will be in-state. Neighboring states (GA, AL, SC)
# follow, then progressively more distant states. States with no
# matching PLT_CNs (confirmed during testing: TN, MS, NC, AR, KY,
# VA, TX, OK, WV, MO, LA) are retained in the list so the script
# can confirm no matches and skip cleanly rather than silently
# omitting them.
db_paths <- list(
  FL = "output/SQLite_FIADB_FL.db",
  GA = "output/SQLite_FIADB_GA.db",
  AL = "output/SQLite_FIADB_AL.db",
  SC = "output/SQLite_FIADB_SC.db",
  TN = "output/SQLite_FIADB_TN.db",
  MS = "output/SQLite_FIADB_MS.db",
  NC = "output/SQLite_FIADB_NC.db",
  AR = "output/SQLite_FIADB_AR.db",
  KY = "output/SQLite_FIADB_KY.db",
  VA = "output/SQLite_FIADB_VA.db",
  TX = "output/SQLite_FIADB_TX.db",
  OK = "output/SQLite_FIADB_OK.db",
  WV = "output/SQLite_FIADB_WV.db",
  MO = "output/SQLite_FIADB_MO.db",
  LA = "output/SQLite_FIADB_LA.db"
)

# FIPS state codes -- used to filter population estimation tables
# (POP_EVAL, POP_STRATUM, etc.) which lack PLT_CN keys but are
# required by rFIA for design-based estimation. These tables are
# retained at the state level rather than filtered to PLT_CNs.
state_codes <- list(
  FL=12, GA=13, AL=1,  SC=45, TN=47, MS=28, NC=37, AR=5,
  KY=21, VA=51, TX=48, OK=40, WV=54, MO=29, LA=22
)

# Output paths for subsetted SQLite databases, one per state.
# These can be loaded together using rFIA::readFIA() for combined
# estimation across the full set of plots assigned to the study area.
dest_paths <- list(
  FL = file.path(output_path,"FIA_FL_5county_TMID.db"),
  GA = file.path(output_path,"FIA_GA_5county_TMID.db"),
  AL = file.path(output_path,"FIA_AL_5county_TMID.db"),
  SC = file.path(output_path,"FIA_SC_5county_TMID.db"),
  TN = file.path(output_path,"FIA_TN_5county_TMID.db"),
  MS = file.path(output_path,"FIA_MS_5county_TMID.db"),
  NC = file.path(output_path,"FIA_NC_5county_TMID.db"),
  AR = file.path(output_path,"FIA_AR_5county_TMID.db"),
  KY = file.path(output_path,"FIA_KY_5county_TMID.db"),
  VA = file.path(output_path,"FIA_VA_5county_TMID.db"),
  TX = file.path(output_path,"FIA_TX_5county_TMID.db"),
  OK = file.path(output_path,"FIA_OK_5county_TMID.db"),
  WV = file.path(output_path,"FIA_WV_5county_TMID.db"),
  MO = file.path(output_path,"FIA_MO_5county_TMID.db"),
  LA = file.path(output_path,"FIA_LA_5county_TMID.db")
)


# ============================================================
# SECTION 2: LOAD TREEMAP PLT_CN LIST
# ============================================================

tmid_list   <- read.csv(tmid_csv)
all_plt_cns <- unique(tmid_list$PLT_CN)

cat(paste0("Total unique PLT_CNs from TreeMap five-county area: ",
           length(all_plt_cns), "\n\n"))


# ============================================================
# SECTION 3: HELPER FUNCTION -- COPY TABLE BY PLT_CN
# ============================================================
#
# Copies one table from a source SQLite database to a destination
# database, applying the appropriate filter based on which key
# columns are present:
#
#   PLT_CN / PLOT_CN present -> filter to target plot list
#     (applies to TREE, COND, SEEDLING, SUBP_COND, etc.)
#   CN present AND table is PLOT -> filter by CN (PLOT's primary key,
#     which equals PLT_CN in linked tables)
#   STATECD present but no plot key -> filter to state FIPS code
#     (applies to POP_EVAL, POP_STRATUM, POP_ESTN_UNIT, etc.;
#      these population tables are needed by rFIA for variance
#      estimation and must be retained at state level)
#   Neither -> copy in full
#     (applies to REF_SPECIES, REF_FOREST_TYPE, REF_PLANT_DICTIONARY,
#      etc.; reference tables have no geographic keys but are required
#      for species name and forest type lookups)

copy_table_by_pltcn <- function(src, dest, table_name, plt_cn_str, statecd) {

  cols      <- dbListFields(src, table_name)
  has_pltcn <- any(c("PLT_CN", "PLOT_CN") %in% cols)
  has_cn    <- "CN" %in% cols
  has_state <- "STATECD" %in% cols

  if (has_pltcn) {
    cn_col <- ifelse("PLT_CN" %in% cols, "PLT_CN", "PLOT_CN")
    query  <- sprintf("SELECT * FROM %s WHERE %s IN (%s)",
                      table_name, cn_col, plt_cn_str)
    cat(sprintf("  Subsetting %-35s by PLT_CN   ... ", table_name))

  } else if (has_cn & table_name == "PLOT") {
    # PLOT table uses CN as its own primary key; PLT_CN in other
    # tables is a foreign key pointing to PLOT.CN
    query <- sprintf("SELECT * FROM %s WHERE CN IN (%s)",
                     table_name, plt_cn_str)
    cat(sprintf("  Subsetting %-35s by CN       ... ", table_name))

  } else if (has_state) {
    query <- sprintf("SELECT * FROM %s WHERE STATECD = %d",
                     table_name, statecd)
    cat(sprintf("  Subsetting %-35s by STATECD  ... ", table_name))

  } else {
    query <- sprintf("SELECT * FROM %s", table_name)
    cat(sprintf("  Copying    %-35s in full     ... ", table_name))
  }

  dat <- dbGetQuery(src, query)
  dbWriteTable(dest, table_name, dat, overwrite = TRUE)
  cat(sprintf("%d rows\n", nrow(dat)))
}


# ============================================================
# SECTION 4: MAIN SEARCH LOOP
# ============================================================
#
# Iterates through state databases in the order defined in db_paths.
# For each state:
#   - Searches only for PLT_CNs not yet matched in a prior state
#   - If matches are found, stores them and creates a subsetted DB
#   - If no matches, records an empty vector and moves on
#   - Skips states with no database file rather than erroring
#
# After each state, remaining_cns is updated so subsequent searches
# are progressively smaller. The loop exits early for any state
# once remaining_cns reaches zero.

found_by_state <- list()       # named list: state -> vector of found PLT_CNs
remaining_cns  <- all_plt_cns  # PLT_CNs not yet matched in any state DB

for (state in names(db_paths)) {

  cat(paste0(rep("=", 55), collapse=""), "\n")
  cat(paste0("Searching ", state, " database...\n"))
  cat(paste0("  Remaining unmatched PLT_CNs: ", length(remaining_cns), "\n"))

  # Short-circuit if all PLT_CNs have been found
  if (length(remaining_cns) == 0) {
    cat("  All PLT_CNs accounted for. Skipping.\n")
    next
  }

  db_path <- db_paths[[state]]
  if (!file.exists(db_path)) {
    cat(paste0("  WARNING: Database not found at ", db_path, " -- skipping.\n"))
    next
  }

  con    <- dbConnect(SQLite(), db_path)
  cn_str <- paste(remaining_cns, collapse = ",")

  # Query PLOT table: match remaining PLT_CNs against PLOT.CN.
  # Retrieve STATECD, COUNTYCD, LAT, LON for verification --
  # confirms whether out-of-state plots are geographically plausible
  # (e.g., southern Georgia counties bordering Florida).
  found <- dbGetQuery(con,
    sprintf("SELECT CN, STATECD, COUNTYCD, LAT, LON
             FROM PLOT WHERE CN IN (%s)", cn_str))

  cat(paste0("  PLT_CNs found in ", state, " DB: ", nrow(found), "\n"))

  if (nrow(found) > 0) {

    # Record found CNs and remove from the unmatched pool
    found_by_state[[state]] <- found$CN
    remaining_cns <- setdiff(remaining_cns, found$CN)

    cat(paste0("  County distribution:\n"))
    print(found %>% count(STATECD, COUNTYCD, name = "N_PLOTS") %>%
            arrange(COUNTYCD))

    # ---- Create subsetted SQLite database for this state ----
    # Only tables/rows relevant to the matched PLT_CNs are retained,
    # keeping the subset DB as small as possible while remaining
    # fully functional for rFIA.
    cat(paste0("\n  Creating subset database for ", state, "...\n"))
    dest   <- dbConnect(SQLite(), dest_paths[[state]])
    cn_sub <- paste(found$CN, collapse = ",")
    tables <- dbListTables(con)

    for (tbl in tables) {
      tryCatch(
        copy_table_by_pltcn(con, dest, tbl, cn_sub, state_codes[[state]]),
        error = function(e) cat(sprintf("  ERROR on %s: %s\n", tbl, e$message))
      )
    }

    # Verify row counts in the new subset DB
    n_plots <- dbGetQuery(dest, "SELECT COUNT(*) as N FROM PLOT")
    n_trees <- dbGetQuery(dest, "SELECT COUNT(*) as N FROM TREE")
    cat(paste0("\n  Subset verification -- PLOT rows: ", n_plots$N,
               "  TREE rows: ", n_trees$N, "\n"))

    dbDisconnect(dest)

  } else {
    # Record empty vector so the state appears in found_by_state
    # but is cleanly excluded from downstream summaries by Filter()
    found_by_state[[state]] <- integer(0)
  }

  dbDisconnect(con)
  cat(paste0("  Remaining unmatched after ", state, ": ",
             length(remaining_cns), "\n\n"))
}


# ============================================================
# SECTION 5: FINAL PLT_CN ACCOUNTING SUMMARY
# ============================================================

cat(paste0(rep("=", 55), collapse=""), "\n")
cat("=== PLT_CN Accounting Summary ===\n")
cat(paste0(rep("=", 55), collapse=""), "\n")
cat(paste0("Total PLT_CNs from TreeMap:       ", length(all_plt_cns), "\n"))

total_found <- 0
for (state in names(found_by_state)) {
  n <- length(found_by_state[[state]])
  total_found <- total_found + n
  cat(paste0("  Found in ", state, ":                  ", n, "\n"))
}

cat(paste0("Total accounted for:              ", total_found, "\n"))
cat(paste0("Still unaccounted for:            ", length(remaining_cns), "\n"))


# ============================================================
# SECTION 6: TREEMAP SUMMARY FOR UNMATCHED PLT_CNs
# ============================================================
#
# If any PLT_CNs remain unmatched after all state databases are
# searched, this section characterises the TreeMap pixels they
# represent. The key questions are:
#
#   (1) Area: what share of five-county forested acres do unmatched
#       PLT_CNs represent? A small share (e.g., <2%) suggests the
#       missing plots are unlikely to meaningfully bias summaries.
#
#   (2) Attributes: do unmatched pixels have systematically different
#       BAA, TPA, or carbon relative to matched pixels? If so, the
#       missing plots are not missing at random and could introduce
#       bias into the overall five-county TreeMap summary.
#
#   (3) Forest type: if unmatched PLT_CNs cluster in particular
#       forest types, those types will be underrepresented or biased
#       in downstream rFIA comparisons for those types specifically.

if (length(remaining_cns) > 0) {

  cat("\nUnmatched PLT_CNs (may require additional state DBs):\n")
  print(remaining_cns)
  write.csv(data.frame(PLT_CN = remaining_cns),
            file.path(output_path,"FL_5county_unmatched_PLTCNs.csv"),
            row.names = FALSE)
  cat("Unmatched PLT_CNs saved to output/FL_5county_unmatched_PLTCNs.csv\n")

  cat("\n")
  cat(paste0(rep("=", 55), collapse=""), "\n")
  cat("=== TreeMap Summary for Unmatched PLT_CNs ===\n")
  cat(paste0(rep("=", 55), collapse=""), "\n")

  ACRES_PER_PIXEL <- 900 / 4046.8564   # 30m pixel in acres

  # Partition tmid_list into matched and unmatched subsets
  unmatched_data <- tmid_list %>%
    filter(PLT_CN %in% remaining_cns) %>%
    mutate(pixel_acres = pixel_count * ACRES_PER_PIXEL)

  matched_data <- tmid_list %>%
    filter(!PLT_CN %in% remaining_cns) %>%
    mutate(pixel_acres = pixel_count * ACRES_PER_PIXEL)

  # Total five-county forested acres from all pixels in tmid_list
  total_acres     <- sum(tmid_list$pixel_count) * ACRES_PER_PIXEL
  unmatched_acres <- sum(unmatched_data$pixel_acres)
  matched_acres   <- sum(matched_data$pixel_acres)

  cat(paste0("\nPixel area represented by unmatched PLT_CNs:\n"))
  cat(paste0("  Total five-county forested acres:    ",
             round(total_acres, 0), "\n"))
  cat(paste0("  Acres from matched PLT_CNs:          ",
             round(matched_acres, 0), "\n"))
  cat(paste0("  Acres from unmatched PLT_CNs:        ",
             round(unmatched_acres, 0), "\n"))
  cat(paste0("  Unmatched as % of total:             ",
             round(100 * unmatched_acres / total_acres, 2), "%\n"))

  # Per-acre and total attribute summary for unmatched pixels
  unmatched_summary <- unmatched_data %>%
    summarise(
      N_PLT_CNS         = n_distinct(PLT_CN),
      N_PIXELS          = sum(pixel_count),
      FOREST_ACRES      = sum(pixel_acres),
      BAA_sqft_ac       = sum(BALIVE   * pixel_acres) / sum(pixel_acres),
      TPA_live_ac       = sum(TPA_LIVE * pixel_acres) / sum(pixel_acres),
      Carbon_tons_ac    = sum(CARBON_L * pixel_acres) / sum(pixel_acres),
      BA_Total_sqft     = sum(BALIVE   * pixel_acres),
      TPA_Total_trees   = sum(TPA_LIVE * pixel_acres),
      Carbon_Total_tons = sum(CARBON_L * pixel_acres)
    )

  cat("\nPer-acre and total estimates for unmatched pixels:\n")
  print(t(unmatched_summary))

  # Side-by-side comparison: matched vs unmatched per-acre attributes
  matched_summary <- matched_data %>%
    summarise(
      N_PLT_CNS      = n_distinct(PLT_CN),
      N_PIXELS       = sum(pixel_count),
      FOREST_ACRES   = sum(pixel_acres),
      BAA_sqft_ac    = sum(BALIVE   * pixel_acres) / sum(pixel_acres),
      TPA_live_ac    = sum(TPA_LIVE * pixel_acres) / sum(pixel_acres),
      Carbon_tons_ac = sum(CARBON_L * pixel_acres) / sum(pixel_acres)
    )

  cat("\nPer-acre comparison -- matched vs unmatched:\n")
  comparison <- bind_rows(
    matched_summary   %>% mutate(GROUP = "Matched")   %>% select(GROUP, everything()),
    unmatched_summary %>% mutate(GROUP = "Unmatched") %>% select(GROUP, N_PLT_CNS,
                                                                   N_PIXELS, FOREST_ACRES,
                                                                   BAA_sqft_ac, TPA_live_ac,
                                                                   Carbon_tons_ac)
  ) %>% mutate(across(where(is.numeric), ~ round(.x, 2)))
  print(t(comparison))

  # Forest type breakdown for unmatched pixels
  unmatched_fortyp <- unmatched_data %>%
    group_by(FORTYPCD, ForTypName) %>%
    summarise(
      N_PLT_CNS      = n_distinct(PLT_CN),
      FOREST_ACRES   = sum(pixel_acres),
      BAA_sqft_ac    = sum(BALIVE   * pixel_acres) / sum(pixel_acres),
      TPA_live_ac    = sum(TPA_LIVE * pixel_acres) / sum(pixel_acres),
      Carbon_tons_ac = sum(CARBON_L * pixel_acres) / sum(pixel_acres),
      .groups = "drop"
    ) %>%
    mutate(PCT_ACRES = round(100 * FOREST_ACRES / sum(FOREST_ACRES), 2)) %>%
    arrange(desc(FOREST_ACRES))

  cat("\nForest type distribution of unmatched pixels:\n")
  print(unmatched_fortyp, n = Inf)

  write.csv(unmatched_summary, file.path(output_path,"FL_5county_unmatched_summary.csv"),
            row.names = FALSE)
  write.csv(unmatched_fortyp,  file.path(output_path,"FL_5county_unmatched_fortype.csv"),
            row.names = FALSE)
  cat("\nUnmatched summaries saved:\n")
  cat("  output/FL_5county_unmatched_summary.csv\n")
  cat("  output/FL_5county_unmatched_fortype.csv\n")
}


# ============================================================
# SECTION 7: TREEMAP SUMMARY FOR FOUND PLT_CNs BY STATE
# ============================================================
#
# For each state database where PLT_CNs were found, summarise the
# TreeMap pixels whose PLT_CNs came from that state. This reveals:
#   - How much of the five-county forested area each state contributes
#   - What forest attributes (BAA, TPA, carbon) those pixels carry
#   - Which forest types are represented by out-of-state plots
#
# PCT_OF_FORTYP in the forest type table answers:
#   "What fraction of this forest type's five-county acreage is
#    represented by plots from this state?"
# This is more interpretable than a within-state percentage because
# it expresses each state's contribution relative to the total
# five-county extent of each forest type. Values should sum to ~100%
# across all states for each FORTYPCD (slightly less if unmatched
# PLT_CNs exist for that type).
#
# The TOTAL column in the combined summary (Section 8) is computed
# directly from tmid_tagged pixel-level data rather than by summing
# the already-summarised per-state values. This avoids:
#   - Double-summarisation errors in area-weighted means
#   - Accumulation of rounding error in totals

cat("\n")
cat(paste0(rep("=", 55), collapse=""), "\n")
cat("=== TreeMap Summary for Found PLT_CNs by State ===\n")
cat(paste0(rep("=", 55), collapse=""), "\n")

ACRES_PER_PIXEL <- 900 / 4046.8564
total_acres     <- sum(tmid_list$pixel_count) * ACRES_PER_PIXEL

# ---- Build state lookup table ----
# Maps each PLT_CN to the state database where it was found.
# FL PLT_CNs must be reconstructed separately: they were matched
# via PLOT.CN in the main search loop but not stored in found_by_state
# (which only captures non-FL states). All PLT_CNs not found in
# other states and not in the unmatched pool belong to Florida.
all_found_cns <- unlist(found_by_state)

# Filter found_by_state to states with at least one match before
# passing to lapply -- prevents empty data.frame rows in bind_rows
# for states that were searched but returned no PLT_CNs.
states_with_finds <- Filter(function(x) length(x) > 0, found_by_state)

state_lookup <- bind_rows(
  lapply(names(states_with_finds), function(st) {
    data.frame(PLT_CN   = states_with_finds[[st]],
               FOUND_IN = st,
               stringsAsFactors = FALSE)
  })
)

# Reconstruct FL PLT_CNs by exclusion
fl_cns <- setdiff(all_plt_cns, c(all_found_cns, remaining_cns))
if (length(fl_cns) > 0) {
  state_lookup <- bind_rows(
    state_lookup,
    data.frame(PLT_CN = fl_cns, FOUND_IN = "FL", stringsAsFactors = FALSE)
  )
}

# Ensure PLT_CN is numeric for join -- state_lookup PLT_CNs are
# character from lapply; tmid_list PLT_CN is numeric
state_lookup$tmp    <- state_lookup$PLT_CN
state_lookup$PLT_CN <- as.numeric(state_lookup$tmp)
state_lookup$tmp    <- NULL

# Join state tags to pixel-level data.
# Rows with no FOUND_IN match (unmatched PLT_CNs) are excluded;
# they are handled separately in Section 6.
tmid_tagged <- tmid_list %>%
  mutate(pixel_acres = pixel_count * ACRES_PER_PIXEL) %>%
  left_join(state_lookup, by = "PLT_CN") %>%
  filter(!is.na(FOUND_IN))

found_states <- unique(tmid_tagged$FOUND_IN)

state_summary_list <- list()   # collects per-state summary rows
state_fortyp_list  <- list()   # collects per-state forest type rows

# ---- Per-state loop ----
for (st in found_states) {

  st_data <- tmid_tagged %>% filter(FOUND_IN == st)
  if (nrow(st_data) == 0) next

  cat(paste0(rep("-", 55), collapse=""), "\n")
  cat(paste0("State: ", st, "\n"))

  st_summary <- st_data %>%
    summarise(
      STATE             = st,
      N_PLT_CNS         = n_distinct(PLT_CN),
      N_PIXELS          = sum(pixel_count),
      FOREST_ACRES      = sum(pixel_acres),
      # PCT_TOTAL_ACRES: this state's share of all five-county
      # forested acres (found + unmatched combined)
      PCT_TOTAL_ACRES   = round(100 * sum(pixel_acres) / total_acres, 2),
      BAA_sqft_ac       = sum(BALIVE   * pixel_acres) / sum(pixel_acres),
      TPA_live_ac       = sum(TPA_LIVE * pixel_acres) / sum(pixel_acres),
      Carbon_tons_ac    = sum(CARBON_L * pixel_acres) / sum(pixel_acres),
      BA_Total_sqft     = sum(BALIVE   * pixel_acres),
      TPA_Total_trees   = sum(TPA_LIVE * pixel_acres),
      Carbon_Total_tons = sum(CARBON_L * pixel_acres)
    )

  # Beautified two-column console table: metric labels + state values.
  # Column header is the state abbreviation for clear identification.
  st_summary_fmt <- data.frame(
    Metric = c(
      "N plots", "N pixels", "Forest acres", "% of total acres",
      "Basal area (sq ft/ac)", "TPA (trees/ac)", "Carbon (tons/ac)",
      "Total BA (sq ft)", "Total trees", "Total carbon (tons)"
    ),
    Value = c(
      formatC(st_summary$N_PLT_CNS,        format = "d", big.mark = ","),
      formatC(st_summary$N_PIXELS,          format = "d", big.mark = ","),
      formatC(st_summary$FOREST_ACRES,      format = "f", digits = 0, big.mark = ","),
      formatC(st_summary$PCT_TOTAL_ACRES,   format = "f", digits = 2),
      formatC(st_summary$BAA_sqft_ac,       format = "f", digits = 2),
      formatC(st_summary$TPA_live_ac,       format = "f", digits = 2),
      formatC(st_summary$Carbon_tons_ac,    format = "f", digits = 2),
      formatC(st_summary$BA_Total_sqft,     format = "f", digits = 0, big.mark = ","),
      formatC(st_summary$TPA_Total_trees,   format = "f", digits = 0, big.mark = ","),
      formatC(st_summary$Carbon_Total_tons, format = "f", digits = 0, big.mark = ",")
    )
  )
  names(st_summary_fmt)[2] <- st

  cat("\nPer-acre and total estimates:\n")
  print(kable(st_summary_fmt, format = "simple", align = c("l", "r")))
  state_summary_list[[st]] <- st_summary

  # ---- Forest type summary for this state ----
  st_fortyp <- st_data %>%
    group_by(FORTYPCD, ForTypName) %>%
    summarise(
      N_PLT_CNS      = n_distinct(PLT_CN),
      FOREST_ACRES   = sum(pixel_acres),
      BAA_sqft_ac    = sum(BALIVE   * pixel_acres) / sum(pixel_acres),
      TPA_live_ac    = sum(TPA_LIVE * pixel_acres) / sum(pixel_acres),
      Carbon_tons_ac = sum(CARBON_L * pixel_acres) / sum(pixel_acres),
      .groups = "drop"
    ) %>%
    left_join(
      # Five-county total acres per FORTYPCD across all states combined.
      # Denominator for PCT_OF_FORTYP so percentages reflect each
      # state's share of that forest type's five-county extent.
      tmid_tagged %>%
        group_by(FORTYPCD) %>%
        summarise(FORTYP_5COUNTY_ACRES = sum(pixel_acres), .groups = "drop"),
      by = "FORTYPCD"
    ) %>%
    mutate(
      STATE            = st,
      # PCT_WITHIN_STATE: forest type as % of this state's total acres
      PCT_WITHIN_STATE = round(100 * FOREST_ACRES / sum(FOREST_ACRES), 2),
      # PCT_OF_FORTYP: this state's contribution as % of five-county
      # total for this forest type
      PCT_OF_FORTYP    = round(100 * FOREST_ACRES / FORTYP_5COUNTY_ACRES, 2)
    ) %>%
    select(-FORTYP_5COUNTY_ACRES) %>%
    arrange(desc(FOREST_ACRES)) %>%
    select(STATE, everything())

  # Beautified forest type table; FORTYPCD and STATE dropped from
  # display (redundant) but retained in state_fortyp_list for CSV.
  st_fortyp_fmt <- st_fortyp %>%
    mutate(
      FOREST_ACRES     = formatC(FOREST_ACRES,     format = "f", digits = 0, big.mark = ","),
      BAA_sqft_ac      = formatC(BAA_sqft_ac,      format = "f", digits = 2),
      TPA_live_ac      = formatC(TPA_live_ac,      format = "f", digits = 2),
      Carbon_tons_ac   = formatC(Carbon_tons_ac,   format = "f", digits = 2),
      PCT_WITHIN_STATE = formatC(PCT_WITHIN_STATE, format = "f", digits = 2),
      PCT_OF_FORTYP    = formatC(PCT_OF_FORTYP,    format = "f", digits = 2)
    ) %>%
    select(ForTypName, N_PLT_CNS, FOREST_ACRES, PCT_WITHIN_STATE, PCT_OF_FORTYP,
           BAA_sqft_ac, TPA_live_ac, Carbon_tons_ac) %>%
    rename(
      "Forest Type"      = ForTypName,
      "N plots"          = N_PLT_CNS,
      "Acres"            = FOREST_ACRES,
      "% within state"   = PCT_WITHIN_STATE,
      "% of forest type" = PCT_OF_FORTYP,
      "BA (sq ft/ac)"    = BAA_sqft_ac,
      "TPA"              = TPA_live_ac,
      "Carbon (tons/ac)" = Carbon_tons_ac
    )

  cat("\nForest type distribution:\n")
  print(kable(st_fortyp_fmt, format = "simple",
              align = c("l", rep("r", ncol(st_fortyp_fmt) - 1))))
  state_fortyp_list[[st]] <- st_fortyp
}


# ============================================================
# SECTION 8: COMBINED STATE SUMMARY TABLE WITH TOTAL COLUMN
# ============================================================
#
# Produces a transposed console table with states as columns,
# ordered left to right by descending PCT_TOTAL_ACRES (largest
# contributor first), with TOTAL as the rightmost column.
#
# TOTAL is computed from tmid_tagged pixel-level data -- not by
# summing the per-state summaries -- to ensure:
#   - Area-weighted means are correctly weighted by raw pixel acres
#   - Totals are exact sums with no accumulated rounding error
#   - PCT_TOTAL_ACRES in TOTAL reflects found PLT_CNs only (will
#     be slightly < 100% if unmatched PLT_CNs exist)

state_summary_all <- bind_rows(state_summary_list) %>%
  mutate(across(where(is.numeric), ~ round(.x, 2)))

state_fortyp_all <- bind_rows(state_fortyp_list)

cat("\n")
cat(paste0(rep("=", 55), collapse=""), "\n")
cat("Combined found PLT_CN summary across all states:\n")

# Row label column
row_labels <- data.frame(
  Metric = c(
    "N plots", "N pixels", "Forest acres", "% of total acres",
    "Basal area (sq ft/ac)", "TPA (trees/ac)", "Carbon (tons/ac)",
    "Total BA (sq ft)", "Total trees", "Total carbon (tons)"
  )
)

# Sort states left to right by descending PCT_TOTAL_ACRES
fmt_state <- state_summary_all %>%
  arrange(desc(PCT_TOTAL_ACRES)) %>%
  mutate(
    N_PLT_CNS         = formatC(N_PLT_CNS,         format = "d", big.mark = ","),
    N_PIXELS          = formatC(N_PIXELS,           format = "d", big.mark = ","),
    FOREST_ACRES      = formatC(FOREST_ACRES,       format = "f", digits = 0, big.mark = ","),
    PCT_TOTAL_ACRES   = formatC(PCT_TOTAL_ACRES,    format = "f", digits = 2),
    BAA_sqft_ac       = formatC(BAA_sqft_ac,        format = "f", digits = 2),
    TPA_live_ac       = formatC(TPA_live_ac,        format = "f", digits = 2),
    Carbon_tons_ac    = formatC(Carbon_tons_ac,     format = "f", digits = 2),
    BA_Total_sqft     = formatC(BA_Total_sqft,      format = "f", digits = 0, big.mark = ","),
    TPA_Total_trees   = formatC(TPA_Total_trees,    format = "f", digits = 0, big.mark = ","),
    Carbon_Total_tons = formatC(Carbon_Total_tons,  format = "f", digits = 0, big.mark = ",")
  )

# Compute TOTAL column from pixel-level tmid_tagged data
totals_raw <- tmid_tagged %>%
  summarise(
    N_PLT_CNS         = n_distinct(PLT_CN),
    N_PIXELS          = sum(pixel_count),
    FOREST_ACRES      = sum(pixel_acres),
    PCT_TOTAL_ACRES   = round(100 * sum(pixel_acres) / total_acres, 2),
    BAA_sqft_ac       = sum(BALIVE   * pixel_acres) / sum(pixel_acres),
    TPA_live_ac       = sum(TPA_LIVE * pixel_acres) / sum(pixel_acres),
    Carbon_tons_ac    = sum(CARBON_L * pixel_acres) / sum(pixel_acres),
    BA_Total_sqft     = sum(BALIVE   * pixel_acres),
    TPA_Total_trees   = sum(TPA_LIVE * pixel_acres),
    Carbon_Total_tons = sum(CARBON_L * pixel_acres)
  )

totals_fmt <- data.frame(
  TOTAL = c(
    formatC(totals_raw$N_PLT_CNS,         format = "d", big.mark = ","),
    formatC(totals_raw$N_PIXELS,           format = "d", big.mark = ","),
    formatC(totals_raw$FOREST_ACRES,       format = "f", digits = 0, big.mark = ","),
    formatC(totals_raw$PCT_TOTAL_ACRES,    format = "f", digits = 2),
    formatC(totals_raw$BAA_sqft_ac,        format = "f", digits = 2),
    formatC(totals_raw$TPA_live_ac,        format = "f", digits = 2),
    formatC(totals_raw$Carbon_tons_ac,     format = "f", digits = 2),
    formatC(totals_raw$BA_Total_sqft,      format = "f", digits = 0, big.mark = ","),
    formatC(totals_raw$TPA_Total_trees,    format = "f", digits = 0, big.mark = ","),
    formatC(totals_raw$Carbon_Total_tons,  format = "f", digits = 0, big.mark = ",")
  )
)

# Transposed display: rows = metrics, columns = states (sorted) + TOTAL
display_table <- row_labels %>%
  bind_cols(
    setNames(
      as.data.frame(t(fmt_state %>% select(-STATE))),
      fmt_state$STATE
    )
  ) %>%
  bind_cols(totals_fmt)

# nrow(fmt_state) + 1 = number of state columns + TOTAL column
print(kable(display_table, format = "simple",
            align = c("l", rep("r", nrow(fmt_state) + 1))))


# ============================================================
# SECTION 9: SAVE OUTPUTS AND FINAL STATUS
# ============================================================

write.csv(state_summary_all, file.path(output_path,"FL_5county_found_by_state_summary.csv"),
          row.names = FALSE)
write.csv(state_fortyp_all,  file.path(output_path,"FL_5county_found_by_state_fortype.csv"),
          row.names = FALSE)

cat("\nFound-by-state summaries saved:\n")
cat("  output/FL_5county_found_by_state_summary.csv\n")
cat("  output/FL_5county_found_by_state_fortype.csv\n")

cat("\nSubset databases created:\n")
for (state in names(dest_paths)) {
  if (file.exists(dest_paths[[state]])) {
    cat(paste0("  ", dest_paths[[state]], "\n"))
  }
}

# ============================================================
# SECTION 10: OUTPUT TM_ID / PLT_CN / STATE / COUNTY LOOKUP TABLE
# ============================================================
#
# Produces a CSV linking each TM_ID (TreeMap raster pixel value) to:
#   PLT_CN   -- FIA control number (join key to FIADB PLOT table)
#   FOUND_IN -- two-letter state abbreviation of the FIA database
#              in which the PLT_CN was located
#   STATECD  -- FIA numeric state FIPS code
#   COUNTYCD -- FIA numeric county FIPS code
#
# STATECD and COUNTYCD are retrieved by querying the subsetted PLOT
# table from each state's output SQLite database. This is more
# reliable than storing them during the main search loop because it
# draws from the already-verified subset databases.
#
# Note: PLT_CNs that were not matched in any state database
# (remaining_cns) will not appear in this table. Unmatched PLT_CNs
# are documented separately in FL_5county_unmatched_PLTCNs.csv.
# ============================================================

cat("\n")
cat(paste0(rep("=", 55), collapse=""), "\n")
cat("=== Building TM_ID / PLT_CN / State / County Lookup ===\n")
cat(paste0(rep("=", 55), collapse=""), "\n")

# Query STATECD and COUNTYCD from each subsetted SQLite database
plot_meta_list <- list()

for (state in names(dest_paths)) {
  
  db_path <- dest_paths[[state]]
  
  # Only query databases that were actually created
  if (!file.exists(db_path)) next
  
  con <- dbConnect(SQLite(), db_path)
  
  # Retrieve plot-level geographic identifiers
  plot_meta <- dbGetQuery(con,
                          "SELECT CN AS PLT_CN, STATECD, COUNTYCD FROM PLOT")
  
  dbDisconnect(con)
  
  if (nrow(plot_meta) > 0) {
    plot_meta$PLT_CN   <- as.numeric(plot_meta$PLT_CN)
    plot_meta$FOUND_IN <- state
    plot_meta_list[[state]] <- plot_meta
    cat(paste0("  ", state, ": ", nrow(plot_meta), " plots retrieved\n"))
  }
}

# Combine plot metadata across all states
plot_meta_all <- bind_rows(plot_meta_list)

# Join TM_ID (Value) from tmid_list to PLT_CN, then to plot metadata.
# GEOID is a standard Census/FIA geographic identifier constructed by
# zero-padding STATECD to 2 digits and COUNTYCD to 3 digits and
# concatenating them (e.g., STATECD=12, COUNTYCD=47 -> "12047").
# This matches the GEOID format used in Census TIGER/Line files and
# is useful for joining to external spatial or tabular datasets.
tmid_lookup <- tmid_list %>%
  select(Value, PLT_CN) %>%
  left_join(plot_meta_all, by = "PLT_CN") %>%
  mutate(GEOID = ifelse(
    !is.na(STATECD) & !is.na(COUNTYCD),
    paste0(sprintf("%02d", STATECD), sprintf("%03d", COUNTYCD)),
    NA_character_
  )) %>%
  arrange(Value)

cat(paste0("\nTotal TM_IDs with matched plot metadata: ",
           sum(!is.na(tmid_lookup$STATECD)), "\n"))
cat(paste0("TM_IDs without match (unmatched PLT_CNs): ",
           sum(is.na(tmid_lookup$STATECD)), "\n"))

# Save output
write.csv(tmid_lookup, file.path(output_path,"FL_5county_TMID_PLT_lookup.csv"),
          row.names = FALSE)
cat("\nLookup table saved to output/FL_5county_TMID_PLT_lookup.csv\n")
cat(paste0("Columns: Value (TM_ID), PLT_CN, FOUND_IN, STATECD, COUNTYCD, GEOID\n"))