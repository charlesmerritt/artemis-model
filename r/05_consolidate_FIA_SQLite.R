# ============================================================
# Subset and Consolidate Affected State FIA SQLite Databases
# Five-County Florida TreeMap Study Area
# ============================================================
#
# PURPOSE:
#   Extracts records for TreeMap-matched PLT_CNs from all affected
#   state FIA SQLite databases and consolidates them into a single
#   SQLite database. The output is fully functional for both rFIA
#   (design-based estimation) and FVS Online (growth projection).
#
# KEY INSIGHT -- TWO JOIN PATHS:
#   FIA and FVS tables use different primary keys that require
#   different filter approaches:
#
#   PLOT table:
#     CN = PLT_CN directly --> filter: CN IN (plt_cns)
#
#   FIADB plot-linked tables (COND, TREE, SEEDLING, etc.):
#     PLT_CN --> filter: PLT_CN IN (plt_cns)
#
#   FVS_STANDINIT_COND, FVS_TREEINIT_COND, FVS_PLOTINIT_COND:
#     STAND_CN = COND.CN --> filter:
#     STAND_CN IN (SELECT CN FROM COND WHERE PLT_CN IN (plt_cns))
#
#   FVS_STANDINIT_PLOT, FVS_TREEINIT_PLOT, FVS_PLOTINIT_PLOT:
#     STAND_CN = PLOT.CN = PLT_CN directly --> filter:
#     STAND_CN IN (plt_cns)
#
#   Population estimation tables (POP_EVAL, POP_STRATUM, etc.):
#     No plot-level key; retain at state level for rFIA variance
#     estimation. Rows from multiple states are appended and
#     deduplicated on EVALID.
#
#   Reference tables (REF_SPECIES, REF_FOREST_TYPE, etc.):
#     No geographic key; copy from first state encountered only
#     to avoid duplicates.
#
#   FVS_GROUPADDFILESANDKEYWORDS:
#     SQL linkage table; copy from first state only.
#
# PLT_CN PRECISION NOTE:
#   FIA control numbers are 16-digit integers that exceed R's double
#   precision (~15 digits). Read from CSV with colClasses = "character"
#   to preserve all digits. The CSV values are 14 digits (truncated
#   during original write.csv) but match COND.PLT_CN via SQLite
#   implicit type coercion.
#
# INPUTS:
#   output/FL_5county_TreeMap_TMIDs.csv  -- PLT_CN reference list
#   output/SQLite_FIADB_XX.db            -- full state databases
#
# OUTPUT:
#   output/FIA_5county_consolidated.db   -- single consolidated DB
# ============================================================

library(DBI)
library(RSQLite)
library(dplyr)

# ---- File paths ----
output_path<- "output2020"
tmid_csv   <- file.path(output_path,"FL_5county_TreeMap_TMIDs.csv")
output_db  <- file.path(output_path,"FIA_5county_consolidated.db")

source_dbs <- list(
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

state_codes <- list(
  FL=12, GA=13, AL=1,  SC=45, TN=47, MS=28, NC=37, AR=5,
  KY=21, VA=51, TX=48, OK=40, WV=54, MO=29, LA=22
)

# FVS tables that use STAND_CN = COND.CN (condition-level join)
FVS_COND_TABLES <- c(
  "FVS_STANDINIT_COND",
  "FVS_TREEINIT_COND",
  "FVS_PLOTINIT_COND"
)

# FVS tables that use STAND_CN = PLOT.CN (plot-level join)
FVS_PLOT_TABLES <- c(
  "FVS_STANDINIT_PLOT",
  "FVS_TREEINIT_PLOT",
  "FVS_PLOTINIT_PLOT"
)

# FVS tables to copy from first state only (no geographic key)
FVS_ONCE_TABLES <- c("FVS_GROUPADDFILESANDKEYWORDS")

# Population estimation tables -- retain at state level, append across states
POP_TABLES <- c(
  "POP_EVAL", "POP_EVAL_TYP", "POP_EVAL_GRND",
  "POP_ESTN_UNIT", "POP_STRATUM", "POP_PLOT_STRATUM_ASSGN"
)


# ============================================================
# SECTION 1: LOAD PLT_CNs
# ============================================================

tmid_list   <- read.csv(tmid_csv, colClasses = c(PLT_CN = "character"))
all_plt_cns <- unique(tmid_list$PLT_CN)
plt_cn_str  <- paste(all_plt_cns, collapse = ",")

cat(paste0("TreeMap PLT_CNs to subset: ", length(all_plt_cns), "\n\n"))


# ============================================================
# SECTION 2: HELPER -- CLASSIFY AND FILTER ONE TABLE
# ============================================================

filter_table <- function(con, table_name, plt_cn_str, statecd,
                         fvs_cond_tables, fvs_plot_tables,
                         fvs_once_tables, pop_tables,
                         ref_written) {

  cols      <- toupper(dbListFields(con, table_name))
  tbl_upper <- toupper(table_name)

  # ---- FVS linkage table: copy once ----
  if (tbl_upper %in% toupper(fvs_once_tables)) {
    if (ref_written) {
      return(list(data = NULL, type = "once_skip"))
    }
    dat <- dbGetQuery(con, sprintf("SELECT * FROM %s", table_name))
    return(list(data = dat, type = "once"))
  }

  # ---- FVS _COND tables: STAND_CN -> COND.CN ----
  if (tbl_upper %in% toupper(fvs_cond_tables)) {
    dat <- dbGetQuery(con, sprintf(
      "SELECT * FROM %s
       WHERE STAND_CN IN (
         SELECT CN FROM COND WHERE PLT_CN IN (%s)
       )", table_name, plt_cn_str))
    return(list(data = dat, type = "fvs_cond"))
  }

  # ---- FVS _PLOT tables: STAND_CN = PLOT.CN = PLT_CN ----
  if (tbl_upper %in% toupper(fvs_plot_tables)) {
    dat <- dbGetQuery(con, sprintf(
      "SELECT * FROM %s WHERE STAND_CN IN (%s)",
      table_name, plt_cn_str))
    return(list(data = dat, type = "fvs_plot"))
  }

  # ---- Population estimation tables: filter by STATECD ----
  if (tbl_upper %in% toupper(pop_tables)) {
    if ("STATECD" %in% cols) {
      dat <- dbGetQuery(con, sprintf(
        "SELECT * FROM %s WHERE STATECD = %d",
        table_name, statecd))
    } else {
      dat <- dbGetQuery(con, sprintf("SELECT * FROM %s", table_name))
    }
    return(list(data = dat, type = "pop"))
  }

  # ---- PLOT table: CN = PLT_CN directly ----
  if (tbl_upper == "PLOT" && "CN" %in% cols) {
    dat <- dbGetQuery(con, sprintf(
      "SELECT * FROM %s WHERE CN IN (%s)",
      table_name, plt_cn_str))
    return(list(data = dat, type = "plot"))
  }

  # ---- Standard FIADB plot-linked tables: PLT_CN column ----
  if ("PLT_CN" %in% cols) {
    dat <- dbGetQuery(con, sprintf(
      "SELECT * FROM %s WHERE PLT_CN IN (%s)",
      table_name, plt_cn_str))
    return(list(data = dat, type = "pltcn"))
  }

  # ---- Reference tables (no geographic key): copy once ----
  if (!"STATECD" %in% cols) {
    if (ref_written) {
      return(list(data = NULL, type = "ref_skip"))
    }
    dat <- dbGetQuery(con, sprintf("SELECT * FROM %s", table_name))
    return(list(data = dat, type = "ref"))
  }

  # ---- Remaining STATECD tables: filter to state ----
  dat <- dbGetQuery(con, sprintf(
    "SELECT * FROM %s WHERE STATECD = %d",
    table_name, statecd))
  return(list(data = dat, type = "statecd"))
}


# ============================================================
# SECTION 3: MAIN LOOP -- EXTRACT AND CONSOLIDATE
# ============================================================

# Remove existing output database
if (file.exists(output_db)) {
  file.remove(output_db)
  cat("Removed existing output database.\n")
}

out <- dbConnect(SQLite(), output_db)

# Track which tables have been written (for ref/once deduplication)
tables_written  <- character(0)
ref_tables_done <- character(0)   # ref/once tables already copied
state_summary   <- list()

for (state in names(source_dbs)) {

  db_path <- source_dbs[[state]]
  if (!file.exists(db_path)) next

  con    <- dbConnect(SQLite(), db_path)
  tables <- dbListTables(con)

  # Check whether this state has any matching PLT_CNs
  present <- dbGetQuery(con, sprintf(
    "SELECT COUNT(*) AS N FROM COND WHERE PLT_CN IN (%s)",
    plt_cn_str))$N

  if (present == 0) {
    cat(paste0(state, ": no matching PLT_CNs -- skipping.\n"))
    dbDisconnect(con)
    next
  }

  cat(paste0(rep("-", 50), collapse=""), "\n")
  cat(paste0("Processing ", state,
             " (", present, " matching conditions)...\n"))

  state_rows <- list()

  for (tbl in tables) {

    tbl_upper  <- toupper(tbl)
    is_ref     <- tbl_upper %in% toupper(ref_tables_done)

    result <- tryCatch(
      filter_table(
        con          = con,
        table_name   = tbl,
        plt_cn_str   = plt_cn_str,
        statecd      = state_codes[[state]],
        fvs_cond_tables = FVS_COND_TABLES,
        fvs_plot_tables = FVS_PLOT_TABLES,
        fvs_once_tables = FVS_ONCE_TABLES,
        pop_tables      = POP_TABLES,
        ref_written     = is_ref
      ),
      error = function(e) {
        cat(paste0("  ERROR on ", tbl, ": ", e$message, "\n"))
        return(NULL)
      }
    )

    if (is.null(result) || is.null(result$data)) next
    if (nrow(result$data) == 0) next

    # Write or append to output database
    append_mode <- tbl_upper %in% toupper(tables_written)

    dbWriteTable(out, tbl, result$data,
                 overwrite = !append_mode,
                 append    =  append_mode)

    # Track written tables and ref tables
    tables_written <- union(tables_written, tbl_upper)
    if (result$type %in% c("ref", "once")) {
      ref_tables_done <- union(ref_tables_done, tbl_upper)
    }

    state_rows[[tbl]] <- nrow(result$data)
    cat(sprintf("  %-40s [%s] %d rows\n",
                tbl, result$type, nrow(result$data)))
  }

  state_summary[[state]] <- state_rows
  dbDisconnect(con)
}


# ============================================================
# SECTION 4: POST-PROCESSING -- DEDUPLICATE POP TABLES
# ============================================================
# POP tables appended from multiple states may have duplicate
# EVALIDs if states share evaluation records (rare but possible).

cat(paste0(rep("=", 55), collapse=""), "\n")
cat("Deduplicating population estimation tables...\n")

for (tbl in POP_TABLES) {
  tbl_upper <- toupper(tbl)
  if (!tbl_upper %in% toupper(dbListTables(out))) next

  # Check for duplicate CNs (primary key in POP tables)
  cols <- toupper(dbListFields(out, tbl_upper))
  if ("CN" %in% cols) {
    n_total  <- dbGetQuery(out,
      sprintf("SELECT COUNT(*) AS N FROM %s", tbl_upper))$N
    n_unique <- dbGetQuery(out,
      sprintf("SELECT COUNT(DISTINCT CN) AS N FROM %s", tbl_upper))$N
    if (n_total > n_unique) {
      cat(paste0("  ", tbl_upper, ": removing ",
                 n_total - n_unique, " duplicate rows.\n"))
      dbExecute(out, sprintf(
        "DELETE FROM %s WHERE rowid NOT IN (
           SELECT MIN(rowid) FROM %s GROUP BY CN
         )", tbl_upper, tbl_upper))
    } else {
      cat(paste0("  ", tbl_upper, ": no duplicates.\n"))
    }
  }
}


# ============================================================
# SECTION 5: ADD StudyArea=5county GROUPING TAG TO FVS TABLES
# ============================================================
# Tag both condition and plot level FVS StandInit tables so
# StudyArea=5county is available in the FVS Online Groups dropdown.

cat(paste0(rep("=", 55), collapse=""), "\n")
cat("Adding StudyArea=5county grouping tag...\n")

GROUP_TAG <- "StudyArea=5county"

for (tbl in c("FVS_STANDINIT_COND", "FVS_STANDINIT_PLOT")) {
  tbl_upper <- toupper(tbl)
  if (!tbl_upper %in% toupper(dbListTables(out))) next

  n <- dbExecute(out, sprintf(
    "UPDATE %s SET GROUPS = GROUPS || ' %s'
     WHERE GROUPS NOT LIKE '%%%s%%'",
    tbl_upper, GROUP_TAG, GROUP_TAG))
  cat(paste0("  ", tbl_upper, ": ", n, " rows tagged.\n"))
}


# ============================================================
# SECTION 6: VERIFICATION
# ============================================================

cat(paste0(rep("=", 55), collapse=""), "\n")
cat("=== Consolidated Database Verification ===\n")
cat(paste0(rep("=", 55), collapse=""), "\n\n")

out_tables <- dbListTables(out)
cat(paste0("Total tables written: ", length(out_tables), "\n\n"))

for (tbl in sort(out_tables)) {
  n <- dbGetQuery(out,
    sprintf("SELECT COUNT(*) AS N FROM %s", tbl))$N
  cat(sprintf("  %-45s %d rows\n", tbl, n))
}

# Key table checks
cat("\nKey table row counts:\n")
for (tbl in c("PLOT", "COND", "TREE",
              "FVS_STANDINIT_COND", "FVS_STANDINIT_PLOT",
              "FVS_TREEINIT_COND",  "FVS_TREEINIT_PLOT")) {
  tbl_upper <- toupper(tbl)
  if (tbl_upper %in% toupper(out_tables)) {
    n <- dbGetQuery(out,
      sprintf("SELECT COUNT(*) AS N FROM %s", tbl_upper))$N
    cat(sprintf("  %-30s %d\n", tbl_upper, n))
  }
}

# Confirm StudyArea tag present
for (tbl in c("FVS_STANDINIT_COND", "FVS_STANDINIT_PLOT")) {
  tbl_upper <- toupper(tbl)
  if (tbl_upper %in% toupper(out_tables)) {
    n_tag <- dbGetQuery(out, sprintf(
      "SELECT COUNT(*) AS N FROM %s
       WHERE GROUPS LIKE '%%StudyArea=5county%%'", tbl_upper))$N
    cat(sprintf("  %-30s %d rows tagged StudyArea=5county\n",
                tbl_upper, n_tag))
  }
}

dbDisconnect(out)

cat(paste0("\nConsolidated database saved to: ", output_db, "\n"))
cat("\nThis database can be:\n")
cat("  1. Uploaded directly to FVS Online (select StudyArea=5county\n")
cat("     in the Groups dropdown to filter to study area plots)\n")
cat("  2. Used with rFIA via readFIA() for design-based estimation\n")
cat("  3. Used with rFVS for programmatic growth projection\n")
