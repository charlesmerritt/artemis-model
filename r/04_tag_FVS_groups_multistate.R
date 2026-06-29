# ============================================================
# Add StudyArea=5county Grouping Variable to FIA SQLite Databases
# All states with PLT_CNs matched to the five-county TreeMap area
# ============================================================
#
# PURPOSE:
#   Appends 'StudyArea=5county' to the GROUPS column in both
#   FVS_STANDINIT_COND and FVS_STANDINIT_PLOT for all plots
#   whose PLT_CNs were identified in the five-county TreeMap
#   spatial analysis. Updates are applied to the full state
#   SQLite databases (not the subsets), since those are the
#   databases uploaded to FVS Online.
#
# FILTERING LOGIC:
#   Uses PLT_CNs from the TreeMap analysis as the filter --
#   not county FIPS codes -- to ensure the grouping exactly
#   reflects the plots assigned to study area pixels, including
#   any out-of-county or out-of-state plots in the FIA design.
#   PLT_CN links to FVS tables via COND.PLT_CN -> COND.CN =
#   FVS_STANDINIT_COND.STAND_CN.
#
# INPUTS:
#   output/FL_5county_TreeMap_TMIDs.csv   -- PLT_CN reference
#   output/SQLite_FIADB_XX.db             -- full state databases
#
# NOTE: Updates are made in-place. Back up databases before
#   running if you want to preserve the originals.
# ============================================================

library(DBI)
library(RSQLite)
library(dplyr)

# ---- File paths ----
tmid_csv <- "output/FL_5county_TreeMap_TMIDs.csv"

# Full state SQLite databases -- the ones uploaded to FVS Online
full_db_paths <- list(
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

GROUP_TAG <- "StudyArea=5county"


# ---- Load TreeMap PLT_CNs ----
# Read PLT_CN as character to prevent floating point precision loss.
# FIA control numbers are 16-digit integers that exceed double precision
# (~15 significant digits). Reading as numeric causes silent truncation.
# The values stored in the CSV are 14 digits (truncated during original
# write.csv) but still match COND.PLT_CN via SQLite implicit coercion.
tmid_list   <- read.csv(tmid_csv, colClasses = c(PLT_CN = "character"))
all_plt_cns <- unique(tmid_list$PLT_CN)
plt_cn_str  <- paste(all_plt_cns, collapse = ",")

cat(paste0("Total TreeMap PLT_CNs to tag: ", length(all_plt_cns), "\n"))
cat(paste0("Sample PLT_CN strings: ",
           paste(head(all_plt_cns, 3), collapse=", "), "\n\n"))


# ---- Helper: tag one database ----
tag_database <- function(db_path, state, plt_cn_str, group_tag) {
  
  if (!file.exists(db_path)) {
    cat(paste0("  SKIP: database not found at ", db_path, "\n"))
    return(NULL)
  }
  
  con    <- dbConnect(SQLite(), db_path)
  tables <- dbListTables(con)
  
  # Confirm required tables exist
  has_cond      <- "COND"              %in% tables
  has_stand_cond <- "FVS_STANDINIT_COND" %in% tables
  has_stand_plot <- "FVS_STANDINIT_PLOT" %in% tables
  
  if (!has_cond) {
    cat(paste0("  SKIP: COND table not found in ", state, " database.\n"))
    dbDisconnect(con)
    return(NULL)
  }
  
  results <- list(state = state)
  
  # ---- Check which PLT_CNs are actually present in this database ----
  present <- dbGetQuery(con,
                        sprintf("SELECT DISTINCT PLT_CN FROM COND
             WHERE PLT_CN IN (%s)", plt_cn_str))
  n_present <- nrow(present)
  
  if (n_present == 0) {
    cat(paste0("  ", state, ": no matching PLT_CNs found -- skipping.\n"))
    dbDisconnect(con)
    return(NULL)
  }
  
  cat(paste0("  ", state, ": ", n_present, " matching PLT_CNs found.\n"))
  
  # Build PLT_CN string for subsequent SQL queries.
  # present$PLT_CN is returned as character from the VARCHAR column.
  # Use unquoted values -- SQLite type affinity handles TEXT/numeric
  # comparison implicitly for IN() matching.
  present_str <- paste(present$PLT_CN, collapse = ",")
  
  # ---- Tag FVS_STANDINIT_COND ----
  if (has_stand_cond) {
    
    # Guard against double-tagging on re-runs
    already_cond <- dbGetQuery(con,
                               sprintf("SELECT COUNT(*) AS N FROM FVS_STANDINIT_COND
               WHERE GROUPS LIKE '%%%s%%'
               AND STAND_CN IN (
                 SELECT CN FROM COND WHERE PLT_CN IN (%s)
               )", group_tag, present_str))$N
    
    if (already_cond > 0) {
      cat(paste0("    FVS_STANDINIT_COND: ", already_cond,
                 " rows already tagged -- skipping to avoid duplicates.\n"))
      results$n_cond <- already_cond
    } else {
      n_cond <- dbExecute(con,
                          sprintf("UPDATE FVS_STANDINIT_COND
                 SET GROUPS = GROUPS || ' %s'
                 WHERE STAND_CN IN (
                   SELECT CN FROM COND WHERE PLT_CN IN (%s)
                 )", group_tag, present_str))
      cat(paste0("    FVS_STANDINIT_COND: ", n_cond, " rows tagged.\n"))
      results$n_cond <- n_cond
    }
    
  } else {
    cat(paste0("    FVS_STANDINIT_COND: table not present.\n"))
    results$n_cond <- 0
  }
  
  # ---- Tag FVS_STANDINIT_PLOT ----
  if (has_stand_plot) {
    
    # Guard against double-tagging
    already_plot <- dbGetQuery(con,
                               sprintf("SELECT COUNT(*) AS N FROM FVS_STANDINIT_PLOT
               WHERE GROUPS LIKE '%%%s%%'", group_tag))$N
    
    # FVS_STANDINIT_PLOT.STAND_CN = PLOT.CN (not COND.CN).
    # Our PLT_CNs equal PLOT.CN directly, so filter STAND_CN
    # against the PLT_CN list with no intermediate join needed.
    where_clause <- sprintf("STAND_CN IN (%s)", present_str)
    
    if (already_plot > 0) {
      cat(paste0("    FVS_STANDINIT_PLOT:  ", already_plot,
                 " rows already tagged -- skipping to avoid duplicates.\n"))
      results$n_plot <- already_plot
    } else {
      n_plot <- dbExecute(con,
                          sprintf("UPDATE FVS_STANDINIT_PLOT
                 SET GROUPS = GROUPS || ' %s'
                 WHERE %s", group_tag, where_clause))
      cat(paste0("    FVS_STANDINIT_PLOT:  ", n_plot, " rows tagged.\n"))
      results$n_plot <- n_plot
    }
    
  } else {
    cat(paste0("    FVS_STANDINIT_PLOT: table not present.\n"))
    results$n_plot <- 0
  }
  
  dbDisconnect(con)
  return(results)
}


# ============================================================
# MAIN LOOP: update all state databases
# ============================================================

cat(paste0(rep("=", 55), collapse=""), "\n")
cat("Tagging FVS grouping variables by state...\n")
cat(paste0(rep("=", 55), collapse=""), "\n\n")

tag_results <- list()

for (state in names(full_db_paths)) {
  cat(paste0(rep("-", 40), collapse=""), "\n")
  cat(paste0("State: ", state, "\n"))
  result <- tag_database(
    db_path    = full_db_paths[[state]],
    state      = state,
    plt_cn_str = plt_cn_str,
    group_tag  = GROUP_TAG
  )
  if (!is.null(result)) tag_results[[state]] <- result
}


# ============================================================
# SUMMARY
# ============================================================

cat("\n")
cat(paste0(rep("=", 55), collapse=""), "\n")
cat("=== Tagging Summary ===\n")
cat(paste0(rep("=", 55), collapse=""), "\n")
cat(sprintf("%-6s  %20s  %20s\n", "State", "STANDINIT_COND rows", "STANDINIT_PLOT rows"))
cat(paste0(rep("-", 50), collapse=""), "\n")

for (state in names(tag_results)) {
  res <- tag_results[[state]]
  cat(sprintf("%-6s  %20d  %20d\n",
              state,
              res$n_cond %||% 0,
              res$n_plot %||% 0))
}

cat(paste0(rep("=", 55), collapse=""), "\n")
cat(paste0("\nGrouping variable '", GROUP_TAG, "' added.\n"))
cat("Re-upload each updated state database to FVS Online to\n")
cat("make the StudyArea=5county group available in the\n")
cat("Simulate > Stands > Groups selection box.\n")