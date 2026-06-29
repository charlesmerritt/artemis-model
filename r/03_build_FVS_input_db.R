# ============================================================
# Build Combined FVS Input SQLite Database from Subsetted
# FIA SQLite Databases
# Five-County Florida Study Area (Hamilton, Baker, Columbia,
# Union, Suwannee)
# ============================================================
#
# PURPOSE:
#   Modern FIA SQLite databases distributed by the FIA DataMart
#   already contain FVS-ready tables produced by the FIA2FVS 2.0
#   translation process. These tables are:
#
#     FVS_StandInit_Cond  -- stand data, one row per condition
#     FVS_StandInit_Plot  -- stand data, one row per plot
#     FVS_TreeInit_Cond   -- tree data linked to conditions
#     FVS_TreeInit_Plot   -- tree data linked to plots
#     FVS_PlotInit_Cond   -- subplot data (conditions)
#     FVS_PlotInit_Plot   -- subplot data (plots)
#     FVS_GroupAddFilesAndKeywords -- SQL linkage table
#
#   This script:
#     1. Identifies which subsetted FIA SQLite databases contain
#        PLT_CNs matched to the five-county TreeMap data
#     2. Confirms FVS-ready tables are present in each database
#     3. Extracts and filters FVS table rows to only the matched
#        PLT_CNs from the TreeMap analysis
#     4. Combines rows across all states into a single SQLite
#        database suitable for upload to FVS Online
#
# LINKAGE BETWEEN TABLES:
#   FVS_StandInit_Cond/Plot are linked to FVS_TreeInit_Cond/Plot
#   via Stand_CN (equivalent to COND.CN in FIADB). Stand_CN is
#   NOT the same as PLT_CN (PLOT.CN). The mapping is:
#     PLT_CN (PLOT.CN) -> COND.PLT_CN -> COND.CN = Stand_CN
#   This script resolves the Stand_CN values for each matched
#   PLT_CN via the COND table in each subsetted database.
#
# FVS VARIANT:
#   All plots in this five-county north Florida study area are
#   assigned variant "SN" (Southern). This applies regardless
#   of which state's FIA database the plot was sourced from,
#   since the variant is determined by where the forest is
#   located, not where the plot was measured.
#
# TABLE CHOICE -- COND vs PLOT:
#   FVS_StandInit_Cond / FVS_TreeInit_Cond treat each FIA
#   condition as a stand. For multi-condition plots this gives
#   finer resolution. FVS_StandInit_Plot / FVS_TreeInit_Plot
#   treat the whole plot as a stand. The _Cond tables are
#   recommended for FIA data and are the default in FVS Online.
#
# INPUTS:
#   output/FIA_XX_5county_TMID.db  -- subsetted FIA SQLite DBs
#   output/FL_5county_TreeMap_TMIDs.csv -- PLT_CN reference list
#
# OUTPUT:
#   output/FVS_5county_input.db    -- combined FVS input database
# ============================================================

library(DBI)
library(RSQLite)
library(dplyr)

# ---- File paths ----
tmid_csv  <- "output/FL_5county_TreeMap_TMIDs.csv"
output_db <- "output/FVS_5county_input.db"

# Subsetted FIA SQLite databases -- only states where PLT_CNs
# were found will have files present; others are skipped
source_dbs <- list(
  FL = "output/FIA_FL_5county_TMID.db",
  GA = "output/FIA_GA_5county_TMID.db",
  AL = "output/FIA_AL_5county_TMID.db",
  SC = "output/FIA_SC_5county_TMID.db",
  TN = "output/FIA_TN_5county_TMID.db",
  MS = "output/FIA_MS_5county_TMID.db",
  NC = "output/FIA_NC_5county_TMID.db",
  AR = "output/FIA_AR_5county_TMID.db",
  KY = "output/FIA_KY_5county_TMID.db",
  VA = "output/FIA_VA_5county_TMID.db",
  TX = "output/FIA_TX_5county_TMID.db",
  OK = "output/FIA_OK_5county_TMID.db",
  WV = "output/FIA_WV_5county_TMID.db",
  MO = "output/FIA_MO_5county_TMID.db",
  LA = "output/FIA_LA_5county_TMID.db"
)

# FVS variant for all plots in this study area
FVS_VARIANT <- "SN"

# FVS-ready table names present in modern FIA SQLite databases.
# _Cond tables are recommended; _Plot tables are also extracted
# so the user can choose which to use in FVS Online.
FVS_TABLES <- c(
  "FVS_STANDINIT_COND",
  "FVS_STANDINIT_PLOT",
  "FVS_TREEINIT_COND",
  "FVS_TREEINIT_PLOT",
  "FVS_PLOTINIT_PLOT",
  "FVS_GROUPADDFILESANDKEYWORDS"
)


# ============================================================
# SECTION 1: LOAD TREEMAP PLT_CN REFERENCE LIST
# ============================================================

tmid_list   <- read.csv(tmid_csv)
all_plt_cns <- unique(tmid_list$PLT_CN)

cat(paste0("TreeMap PLT_CNs to match: ", length(all_plt_cns), "\n\n"))


# ============================================================
# SECTION 2: VERIFY FVS TABLES AND CHECK STAND_CN LINKAGE
# ============================================================
# Modern FIA SQLite databases link FVS tables to FIADB tables
# via Stand_CN = COND.CN. We resolve Stand_CNs for our PLT_CNs
# by querying the COND table in each source database.

cat("Checking source databases for FVS-ready tables...\n\n")

# Accumulator lists for rows from each FVS table across states
collected <- lapply(FVS_TABLES, function(t) list())
names(collected) <- FVS_TABLES

state_results <- list()   # summary of what was found per state

for (state in names(source_dbs)) {
  
  db_path <- source_dbs[[state]]
  if (!file.exists(db_path)) next
  
  con    <- dbConnect(SQLite(), db_path)
  tables <- dbListTables(con)
  
  # Identify which FVS tables are present in this database
  fvs_present <- intersect(FVS_TABLES, tables)
  
  if (length(fvs_present) == 0) {
    cat(paste0("  ", state, ": No FVS-ready tables found -- skipping.\n"))
    cat(paste0("    Available tables: ", paste(tables, collapse=", "), "\n"))
    dbDisconnect(con)
    next
  }
  
  cat(paste0(rep("-", 50), collapse=""), "\n")
  cat(paste0("State: ", state, "\n"))
  cat(paste0("  FVS tables present: ", paste(fvs_present, collapse=", "), "\n"))
  
  # ---- Resolve Stand_CNs for matched PLT_CNs ----
  # COND.PLT_CN links to PLOT.CN (our PLT_CN)
  # COND.CN is Stand_CN used in FVS tables
  plt_cn_str <- paste(all_plt_cns, collapse=",")
  
  stand_cns <- dbGetQuery(con, sprintf(
    "SELECT CN AS Stand_CN, PLT_CN
     FROM COND
     WHERE PLT_CN IN (%s)", plt_cn_str
  ))
  
  if (nrow(stand_cns) == 0) {
    cat(paste0("  No COND records matched -- skipping.\n"))
    dbDisconnect(con)
    next
  }
  
  cat(paste0("  Stand_CNs resolved: ", nrow(stand_cns), "\n"))
  stand_cn_str <- paste(stand_cns$Stand_CN, collapse=",")
  
  # ---- Extract rows from each FVS table ----
  # StandInit tables: filter by Stand_CN
  # TreeInit/PlotInit tables: filter by Stand_CN (foreign key)
  # GroupAddFilesAndKeywords: copy in full (small linkage table)
  
  n_extracted <- list()
  
  for (tbl in fvs_present) {
    
    tbl_cols <- dbListFields(con, tbl)
    
    if (tbl == "FVS_GROUPADDFILESANDKEYWORDS") {
      # Copy the linkage table in full -- it contains SQL queries
      # that connect StandInit to TreeInit and is small
      rows <- dbGetQuery(con, sprintf("SELECT * FROM %s", tbl))
      cat(paste0("  ", tbl, ": ", nrow(rows), " rows (full copy)\n"))
      
    } else if (any(toupper(tbl_cols) == "STAND_CN")) {
      # StandInit, TreeInit, PlotInit: filter to matched Stand_CNs
      rows <- dbGetQuery(con, sprintf(
        "SELECT * FROM %s WHERE STAND_CN IN (%s)",
        tbl, stand_cn_str
      ))
      cat(paste0("  ", tbl, ": ", nrow(rows), " rows\n"))
      
    } else {
      cat(paste0("  ", tbl, ": Stand_CN column not found -- skipping.\n"))
      next
    }
    
    # Tag rows with source state for traceability
    if (nrow(rows) > 0) {
      rows$Source_State <- state
      collected[[tbl]] <- c(collected[[tbl]], list(rows))
    }
    
    n_extracted[[tbl]] <- nrow(rows)
  }
  
  state_results[[state]] <- list(
    fvs_present  = fvs_present,
    stand_cns    = nrow(stand_cns),
    n_extracted  = n_extracted
  )
  
  dbDisconnect(con)
}


# ============================================================
# SECTION 3: COMBINE AND WRITE OUTPUT DATABASE
# ============================================================
#
# All rows from matching states are combined into single tables.
# The Source_State column is included for traceability but is
# not used by FVS itself.
#
# FVS_GroupAddFilesAndKeywords: take from the state with the
# most Stand rows (typically FL) to avoid duplicating SQL
# linkage queries. Duplicates would not break FVS but add clutter.

cat("\n")
cat(paste0(rep("=", 55), collapse=""), "\n")
cat("Building combined FVS input database...\n")
cat(paste0(rep("=", 55), collapse=""), "\n")

# Remove existing output database if present
if (file.exists(output_db)) {
  file.remove(output_db)
  cat("Removed existing output database.\n")
}

out <- dbConnect(SQLite(), output_db)

for (tbl in FVS_TABLES) {
  
  all_rows <- collected[[tbl]]
  
  if (length(all_rows) == 0) {
    cat(paste0("  ", tbl, ": no rows collected -- table not written.\n"))
    next
  }
  
  if (tbl == "FVS_GroupAddFilesAndKeywords") {
    # Use only the first state's copy to avoid duplicate SQL queries
    combined <- all_rows[[1]] %>% select(-Source_State)
  } else {
    combined <- bind_rows(all_rows)
  }
  
  dbWriteTable(out, tbl, combined, overwrite = TRUE)
  cat(paste0("  ", tbl, ": ", nrow(combined), " rows written.\n"))
}


# ============================================================
# SECTION 4: VERIFICATION
# ============================================================

cat("\n")
cat(paste0(rep("=", 55), collapse=""), "\n")
cat("Verification -- output database table row counts:\n")
cat(paste0(rep("=", 55), collapse=""), "\n")

for (tbl in dbListTables(out)) {
  n <- dbGetQuery(out, sprintf("SELECT COUNT(*) AS N FROM %s", tbl))$N
  cat(paste0("  ", tbl, ": ", n, " rows\n"))
}

# Confirm Stand_IDs are unique in StandInit tables
for (tbl in c("FVS_STANDINIT_COND", "FVS_STANDINIT_PLOT")) {
  if (tbl %in% dbListTables(out)) {
    n_total  <- dbGetQuery(out, sprintf("SELECT COUNT(*) AS N FROM %s", tbl))$N
    n_unique <- dbGetQuery(out, sprintf("SELECT COUNT(DISTINCT STAND_ID) AS N FROM %s", tbl))$N
    dupe_flag <- ifelse(n_total != n_unique, " *** DUPLICATES DETECTED ***", "")
    cat(paste0("  ", tbl, " unique Stand_IDs: ", n_unique, " / ", n_total, dupe_flag, "\n"))
  }
}

# Confirm TreeInit rows link back to StandInit
for (pair in list(
  c("FVS_TREEINIT_COND", "FVS_STANDINIT_COND"),
  c("FVS_TREEINIT_PLOT", "FVS_STANDINIT_PLOT")
)) {
  tree_tbl  <- pair[1]
  stand_tbl <- pair[2]
  if (all(c(tree_tbl, stand_tbl) %in% dbListTables(out))) {
    orphan_check <- dbGetQuery(out, sprintf(
      "SELECT COUNT(*) AS N FROM %s t
       WHERE t.STAND_CN NOT IN (SELECT STAND_CN FROM %s)",
      tree_tbl, stand_tbl
    ))$N
    cat(paste0("  ", tree_tbl, " orphaned tree rows: ", orphan_check,
               ifelse(orphan_check > 0, " *** CHECK LINKAGE ***", ""), "\n"))
  }
}


# ============================================================
# SECTION 5: SUMMARY AND FVS ONLINE UPLOAD NOTES
# ============================================================

cat("\n")
cat(paste0(rep("=", 55), collapse=""), "\n")
cat("=== Per-State Extraction Summary ===\n")
cat(paste0(rep("=", 55), collapse=""), "\n")

for (state in names(state_results)) {
  res <- state_results[[state]]
  cat(paste0("\n", state, ":\n"))
  cat(paste0("  Stand_CNs matched:    ", res$stand_cns, "\n"))
  for (tbl in names(res$n_extracted)) {
    cat(paste0("  ", tbl, ": ", res$n_extracted[[tbl]], " rows\n"))
  }
}

dbDisconnect(out)

cat(paste0("\nCombined FVS input database saved to: ", output_db, "\n"))
cat("\n")
cat("=== FVS Online Upload Instructions ===\n")
cat("1. Navigate to Manage Projects > Import input data >\n")
cat("   Upload inventory database\n")
cat("2. Step 1: Click Browse and select:\n")
cat(paste0("   ", output_db, "\n"))
cat("3. Step 2: Click 'Install uploaded database'\n")
cat("4. In the Simulate > Stands tab:\n")
cat("   - Inventory Data Tables: select 'FIA conditions'\n")
cat("     (uses FVS_STANDINIT_COND / FVS_TREEINIT_COND)\n")
cat("   - Variants: select 'sn: Southern'\n")
cat("   - Add stands using the Groups or Stands selection boxes\n")
cat("\n")
cat("NOTE: If FVS-ready tables are NOT present in your subsetted\n")
cat("databases, they may not have been included when the original\n")
cat("FIA SQLite databases were downloaded. In that case, download\n")
cat("full state SQLite databases from the FIA DataMart -- these\n")
cat("include the FIA2FVS pre-built tables -- and re-run the\n")
cat("subsetting scripts to create new subsets that include the\n")
cat("FVS tables.\n")


library(DBI); library(RSQLite)
con <- dbConnect(SQLite(), "output/FVS_5county_input.db")

# Check current values
dbGetQuery(con,
           "SELECT BASAL_AREA_FACTOR, INV_PLOT_SIZE, BRK_DBH, NUM_PLOTS
   FROM FVS_STANDINIT_COND LIMIT 5")

# If INV_PLOT_SIZE needs correction:
dbExecute(con,
   "UPDATE FVS_STANDINIT_COND SET INV_PLOT_SIZE = 0.041800")

dbDisconnect(con)

