# ============================================================
# TreeMap VALUE to FIA/FVS Linkage Table
# ============================================================
#
# PURPOSE:
#   Builds a lookup table linking each TreeMap raster VALUE
#   (TM_ID) to its corresponding FIA and FVS identifiers.
#   This enables FVS output tables to be joined back to the
#   TreeMap raster for spatial analysis and comparison.
#
# LINKAGE STRUCTURE:
#
#   TreeMap raster pixel
#       |
#       VALUE (TM_ID, integer raster pixel value)
#       |
#       PLT_CN  [from FL_5county_TreeMap_TMIDs.csv]
#       |
#       +-- COND.CN = FVS_STANDINIT_COND.STAND_CN  (condition level)
#       +-- PLOT.CN = FVS_STANDINIT_PLOT.STAND_CN  (plot level)
#       +-- FVS_STANDINIT_COND.STAND_ID             (FVS stand identifier)
#       +-- FVS_STANDINIT_PLOT.STAND_ID
#
#   FVS output tables (FVS_Summary2, FVS_Carbon, etc.) join via:
#     FVS output CaseID -> FVS_Cases.CaseID -> FVS_Cases.StandID
#     FVS_Cases.StandID = FVS_STANDINIT_COND.STAND_ID (or PLOT)
#
# OUTPUTS:
#   output/TreeMap_FVS_linkage.csv  -- full linkage table
#   output/TreeMap_FVS_linkage.db   -- SQLite version for joins
#
# USAGE:
#   After FVS runs, join FVS output to this table via STAND_ID_COND
#   or STAND_ID_PLOT, then join to the TreeMap raster via VALUE to
#   map projected forest attributes back to pixels.
# ============================================================

library(DBI)
library(RSQLite)
library(dplyr)

# ---- File paths ----
output_path<- "output2020"
tmid_csv       <- file.path(output_path,"FL_5county_TreeMap_TMIDs.csv")
consolidated_db <- file.path(output_path,"FIA_5county_consolidated.db")
output_csv     <- file.path(output_path,"TreeMap_FVS_linkage.csv")
output_db      <- file.path(output_path,"TreeMap_FVS_linkage.db")


# ============================================================
# SECTION 1: LOAD TREEMAP TM_ID / PLT_CN BASE TABLE
# ============================================================

# Read PLT_CN as character to preserve precision
tmid_list <- read.csv(tmid_csv, colClasses = c(PLT_CN = "character"))

# Core columns for linkage
base_link <- tmid_list %>%
  select(VALUE     = Value,
         PLT_CN,
         PIXEL_COUNT = pixel_count,
         PIXEL_ACRES = pixel_acres,
         FORTYPCD,
         ForTypName,
         BALIVE_TM   = BALIVE,
         TPA_LIVE_TM = TPA_LIVE,
         CARBON_L_TM = CARBON_L) %>%
  distinct(VALUE, PLT_CN, .keep_all = TRUE)

cat(paste0("TreeMap TM_IDs (VALUE): ", nrow(base_link), "\n"))
cat(paste0("Unique PLT_CNs:         ", n_distinct(base_link$PLT_CN), "\n\n"))


# ============================================================
# SECTION 2: RESOLVE STAND_CN AND STAND_ID FROM CONSOLIDATED DB
# ============================================================

con <- dbConnect(SQLite(), consolidated_db)

# ---- Condition-level: PLT_CN -> COND.CN = STAND_CN_COND ----
# Also retrieve STAND_ID from FVS_STANDINIT_COND for FVS output joins
plt_cn_str <- paste(unique(base_link$PLT_CN), collapse = ",")

cond_link <- dbGetQuery(con, sprintf(
  "SELECT
     c.PLT_CN,
     c.CN          AS STAND_CN_COND,
     c.CONDID,
     s.STAND_ID    AS STAND_ID_COND,
     s.INV_YEAR    AS INV_YEAR_COND,
     s.VARIANT,
     s.STATE,
     s.COUNTY
   FROM COND c
   LEFT JOIN FVS_STANDINIT_COND s ON s.STAND_CN = c.CN
   WHERE c.PLT_CN IN (%s)", plt_cn_str))

cat(paste0("Condition-level links resolved: ", nrow(cond_link), "\n"))

# ---- Plot-level: PLT_CN = PLOT.CN = STAND_CN_PLOT ----
plot_link <- dbGetQuery(con, sprintf(
  "SELECT
     p.CN          AS PLT_CN,
     p.CN          AS STAND_CN_PLOT,
     s.STAND_ID    AS STAND_ID_PLOT,
     s.INV_YEAR    AS INV_YEAR_PLOT
   FROM PLOT p
   LEFT JOIN FVS_STANDINIT_PLOT s ON s.STAND_CN = p.CN
   WHERE p.CN IN (%s)", plt_cn_str))

cat(paste0("Plot-level links resolved:      ", nrow(plot_link), "\n\n"))

dbDisconnect(con)


# ============================================================
# SECTION 3: BUILD CONSOLIDATED LINKAGE TABLE
# ============================================================
#
# Each TM_ID (VALUE) maps to one PLT_CN.
# Each PLT_CN may map to one or more conditions (CONDID 1, 2, etc.)
# and exactly one plot.
# The final table has one row per VALUE x CONDID combination.

linkage <- base_link %>%
  # Join condition-level identifiers
  left_join(cond_link, by = "PLT_CN", relationship = "many-to-many") %>%
  # Join plot-level identifiers
  left_join(plot_link %>%
              select(PLT_CN, STAND_CN_PLOT, STAND_ID_PLOT, INV_YEAR_PLOT),
            by = "PLT_CN") %>%
  # Arrange for readability
  arrange(VALUE, CONDID) %>%
  select(
    # TreeMap identifiers
    VALUE,
    PLT_CN,
    PIXEL_COUNT,
    PIXEL_ACRES,
    FORTYPCD,
    ForTypName,

    # TreeMap per-acre attributes (inventory year snapshot)
    BALIVE_TM,
    TPA_LIVE_TM,
    CARBON_L_TM,

    # FIA/FVS condition-level identifiers
    CONDID,
    STAND_CN_COND,
    STAND_ID_COND,
    INV_YEAR_COND,
    VARIANT,
    STATE,
    COUNTY,

    # FIA/FVS plot-level identifiers
    STAND_CN_PLOT,
    STAND_ID_PLOT,
    INV_YEAR_PLOT
  )

cat(paste0("Linkage table rows:  ", nrow(linkage), "\n"))
cat(paste0("Rows with STAND_ID_COND: ",
           sum(!is.na(linkage$STAND_ID_COND)), "\n"))
cat(paste0("Rows with STAND_ID_PLOT: ",
           sum(!is.na(linkage$STAND_ID_PLOT)), "\n"))
cat(paste0("Unmatched VALUE rows:    ",
           sum(is.na(linkage$STAND_CN_COND)), "\n\n"))

cat("Sample linkage rows:\n")
print(head(linkage %>%
             select(VALUE, PLT_CN, CONDID, STAND_ID_COND,
                    STAND_ID_PLOT, BALIVE_TM, STATE, COUNTY), 8))


# ============================================================
# SECTION 4: SAVE OUTPUTS
# ============================================================

# CSV for general use
write.csv(linkage, output_csv, row.names = FALSE)
cat(paste0("\nLinkage table saved to: ", output_csv, "\n"))

# SQLite for efficient joining with FVS output databases
if (file.exists(output_db)) file.remove(output_db)
link_con <- dbConnect(SQLite(), output_db)

dbWriteTable(link_con, "TreeMap_FVS_Linkage", linkage, overwrite = TRUE)

# Create indexes for fast joining
dbExecute(link_con,
  "CREATE INDEX IF NOT EXISTS idx_value
   ON TreeMap_FVS_Linkage (VALUE)")
dbExecute(link_con,
  "CREATE INDEX IF NOT EXISTS idx_pltcn
   ON TreeMap_FVS_Linkage (PLT_CN)")
dbExecute(link_con,
  "CREATE INDEX IF NOT EXISTS idx_stand_id_cond
   ON TreeMap_FVS_Linkage (STAND_ID_COND)")
dbExecute(link_con,
  "CREATE INDEX IF NOT EXISTS idx_stand_id_plot
   ON TreeMap_FVS_Linkage (STAND_ID_PLOT)")

dbDisconnect(link_con)
cat(paste0("Linkage database saved to: ", output_db, "\n"))


# ============================================================
# SECTION 5: USAGE NOTES
# ============================================================

cat("
=== How to use this linkage table ===

1. JOIN FVS OUTPUT BACK TO TREEMAP:

   After running FVS, join FVS_Summary2 to this table via STAND_ID:

   library(DBI); library(RSQLite)
   fvs_out  <- dbConnect(SQLite(), 'FVSOut.db')
   link_con <- dbConnect(SQLite(), 'output/TreeMap_FVS_linkage.db')

   # Attach linkage database to FVS output connection
   dbExecute(fvs_out,
     \"ATTACH 'output/TreeMap_FVS_linkage.db' AS lnk\")

   # Join FVS projected BA back to TreeMap pixels via STAND_ID_COND
   result <- dbGetQuery(fvs_out,
     \"SELECT
         l.VALUE,
         l.PLT_CN,
         l.PIXEL_ACRES,
         l.BALIVE_TM     AS BA_TreeMap_inventory,
         s.Year,
         s.BA            AS BA_FVS_projected
       FROM FVS_Summary2 s
       JOIN lnk.TreeMap_FVS_Linkage l
         ON s.StandID = l.STAND_ID_COND
       ORDER BY l.VALUE, s.Year\")

   dbDisconnect(fvs_out)

2. MAP PROJECTED ATTRIBUTES BACK TO RASTER (terra):

   library(terra)
   tm   <- rast('RDS-2025-0032/Data/TreeMap2022_CONUS.tif')
   # ... crop/mask to study area as before ...

   # Substitute projected BA for inventory-year BA
   # by reclassifying pixel values using the linkage table
   # (one row per VALUE, one projected year at a time)
\n")
