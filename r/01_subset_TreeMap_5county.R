# ============================================================
# Subset TreeMap 2022 to Five Florida Counties
# Hamilton (047), Baker (003), Columbia (023),
# Union (125), Suwannee (121)
# State FIPS: 12 (Florida)
# ============================================================
#
# PURPOSE:
#   Extract TreeMap 2022 pixel data for a five-county study area
#   in north Florida and produce forest attribute summaries
#   (BALIVE, TPA_LIVE, CARBON_L) at the five-county and individual
#   county levels. The key output -- FL_5county_TreeMap_TMIDs.csv --
#   links each unique TM_ID to its FIA PLT_CN and serves as the
#   input to the multistate PLT_CN search script
#   (subset_FIA_SQLite_multistateR.R).
#
# RASTER STRUCTURE:
#   TreeMap stores pixel values as integer TM_IDs. Each TM_ID
#   corresponds to one row in the VAT (.tif.vat.dbf), which contains
#   all forest attributes including BALIVE, TPA_LIVE, CARBON_L, and
#   the FIA PLT_CN from which those attributes were drawn. Terra
#   loads the VAT automatically when rast() is called -- do NOT load
#   the .vat.dbf separately.
#
#   Because the VAT's active category is ForTypName (the last
#   categorical column), terra displays the layer name as "ForTypName"
#   and freq() returns forest type strings rather than integer TM_IDs
#   unless the categorical interpretation is stripped first with
#   as.int(). This is handled in Section 3.
#
# PIXEL AREA:
#   Each 30m x 30m pixel = 900 sq m = 0.22239 acres
#   (900 / 4046.8564). All FOREST_ACRES values are derived from
#   pixel counts using this conversion.
#
# PER-ACRE ESTIMATES:
#   All per-acre summaries use area-weighted means:
#     weighted_mean = sum(attribute * pixel_acres) / sum(pixel_acres)
#   VAT attributes (BALIVE, TPA_LIVE, CARBON_L) are per-acre values
#   assigned at the plot level; pixel_acres serves as the weight,
#   reflecting how much landscape area each TM_ID/PLT_CN represents.
#
# OUTPUTS:
#   output/FL_5county_TreeMap_TMIDs.csv         -- one row per TM_ID;
#     used by subset_FIA_SQLite_multistateR.R to identify which FIA
#     databases contain the PLT_CNs assigned to this study area
#   output/FL_5county_TreeMap_summary.csv        -- five-county totals
#   output/FL_5county_TreeMap_ForestType_summary.csv
#   output/FL_5county_TreeMap_by_county.csv      -- per-county totals
# ============================================================

library(terra)    # raster handling: rast(), crop(), mask(), freq()
library(geodata)  # gadm() for county boundary download
library(dplyr)    # data manipulation

options(scipen = 999)

# ---- File paths and constants ----

output_path<- "output2020"
dir.create(output_path)
#tm_path <- "RDS-2025-0032/Data/TreeMap2022_CONUS.tif"
tm_path <- "RDS-2025-0031/Data/TreeMap2020_CONUS.tif"

# Pixel area conversion: 30m x 30m = 900 sq m; 1 acre = 4046.8564 sq m
ACRES_PER_PIXEL <- 900 / 4046.8564

# Target counties: FIPS codes as integers, matching FIADB convention
# (leading zeros are dropped in FIADB integer fields)
STATECD      <- 12
COUNTYCDS    <- c(3, 23, 47, 121, 125)
COUNTY_NAMES <- c("Baker", "Columbia", "Hamilton", "Suwannee", "Union")


# ============================================================
# SECTION 1: BUILD FIVE-COUNTY BOUNDARY
# ============================================================
#
# gadm() at level 2 provides county-level administrative boundaries
# for the USA. The resulting SpatVector is filtered to the five target
# counties by name. The boundary is later reprojected to match the
# TreeMap raster CRS (NAD83 / Conus Albers, EPSG:5070) before use.
#
# Alternative boundary sources if geodata is unavailable:
#   tigris: fl_boundary <- vect(tigris::counties(state="FL"))
#   local shapefile: fl_boundary <- vect("path/to/FL_counties.shp")
#
# Note on GADM FIPS storage: GADM stores county FIPS codes as
# zero-padded character strings (e.g., "003", "047") in the CC_2
# field. Here we filter by NAME_2 (county name) instead, which is
# more robust across GADM versions. If NAME_2 field names differ in
# your GADM version, check with: print(names(usa_counties))

cat("Loading county boundaries...\n")
usa_counties <- gadm(country = "USA", level = 2, path = tempdir())

# Format FIPS as zero-padded strings for reference
# (used for documentation; actual filter uses NAME_2)
target_fips <- sprintf("%03d", COUNTYCDS)   # "003", "023", "047", "121", "125"

county_boundary <- usa_counties[
  usa_counties$NAME_1 == "Florida" & usa_counties$NAME_2 %in% COUNTY_NAMES, ]

cat(paste0("Counties selected: ", nrow(county_boundary), "\n"))
print(county_boundary$NAME_2)   # verify all five counties loaded correctly


# ============================================================
# SECTION 2: CROP AND MASK TREEMAP TO FIVE-COUNTY AREA
# ============================================================
#
# The TreeMap raster covers the entire conterminous US (CONUS).
# Cropping to the five-county bounding box first (crop) reduces
# memory and processing time before the more expensive pixel-level
# boundary mask (mask) is applied.
#
# Both operations require the boundary to be in the same CRS as the
# raster (NAD83 / Conus Albers, EPSG:5070). project() reprojects
# the GADM SpatVector to match.
#
# After masking, pixels outside the county boundaries are set to NA.
# These NA pixels are excluded from freq() in Section 3 and from
# all subsequent summaries.

cat("\nLoading TreeMap raster...\n")
tm <- rast(tm_path)

# Reproject county boundary to NAD83 / Conus Albers to match raster
county_proj <- project(county_boundary, crs(tm))

cat("Cropping to five-county extent...\n")
tm_crop <- crop(tm, county_proj)

cat("Masking to five-county boundary...\n")
tm_county <- mask(tm_crop, county_proj)

cat("Five-county TreeMap raster:\n")
print(tm_county)
plot(tm_county)
writeRaster(tm_county,file.path(output_path,"clipped_TreeMap.tif"))

# ============================================================
# SECTION 3: EXTRACT PIXEL COUNTS BY TM_ID
# ============================================================
#
# freq() counts the number of pixels for each unique raster value.
# The raster value is the integer TM_ID, which serves as the join
# key to the VAT in Section 4.
#
# IMPORTANT: terra's active category is ForTypName (the last
# categorical column in the VAT), so freq() applied directly to
# tm_county returns forest type name strings rather than integer
# TM_IDs, with potentially duplicate rows for the same type name.
# as.int() strips the categorical VAT association, exposing the raw
# integer pixel values for correct freq() behavior.
#
# The layer column returned by freq() is dropped as it is always 1
# (single-layer raster). Names are assigned explicitly for clarity.
#
# Note: freq() on a county-sized raster typically completes in
# seconds to a few minutes depending on hardware.

cat("\nCalculating pixel frequencies...\n")
tm_county_int <- as.int(tm_county)    # strip VAT; expose raw TM_ID integers
pixel_freq    <- freq(tm_county_int, bylayer = FALSE)

# freq() returns columns: layer, value, count
# Rename to match VAT join key (Value) and convention used throughout
names(pixel_freq) <- c("Value", "pixel_count")
pixel_freq <- pixel_freq %>% select(Value, pixel_count)

cat(paste0("Unique TM_IDs in five-county area: ", nrow(pixel_freq), "\n"))
cat(paste0("Total forested pixels:             ", sum(pixel_freq$pixel_count), "\n"))


# ============================================================
# SECTION 4: JOIN TO VAT AND COMPUTE SUMMARIES
# ============================================================
#
# The VAT is retrieved from the original raster (tm), which retains
# the full attribute table including all 26 columns. The as.int()
# copy (tm_county_int) does not retain the VAT.
#
# JOIN STRUCTURE:
#   pixel_freq (Value = TM_ID, pixel_count)
#   LEFT JOIN vat (Value = TM_ID, PLT_CN, FORTYPCD, ForTypName,
#                  BALIVE, TPA_LIVE, CARBON_L, ...)
#
# Each TM_ID maps to exactly one PLT_CN (confirmed: n_TM_IDs = 1
# for all rows in the Florida subset). This means every row in
# county_data represents a unique FIA plot assignment, and
# pixel_count captures how many 30m pixels share that assignment.
#
# The filter(!is.na(BALIVE)) removes any pixels that joined to VAT
# rows without forest attributes -- these correspond to non-forest
# land cover pixels that may appear at the boundary of the forest
# mask.

vat <- cats(tm)[[1]]   # retrieve VAT from original raster

county_data <- pixel_freq %>%
  left_join(vat, by = "Value") %>%
  filter(!is.na(BALIVE)) %>%       # retain forested pixels only
  mutate(pixel_acres = pixel_count * ACRES_PER_PIXEL)

cat(paste0("Total forested acres (TreeMap): ",
           round(sum(county_data$pixel_acres), 0), "\n"))


# ---- Save TM_ID list for multistate PLT_CN search ----
# FL_5county_TreeMap_TMIDs.csv is the primary input to
# subset_FIA_SQLite_multistateR.R. It contains one row per unique
# TM_ID with:
#   Value       -- TM_ID integer (raster pixel value)
#   PLT_CN      -- FIA control number; links to PLOT.CN in FIADB
#   pixel_count -- number of 30m pixels assigned this TM_ID
#   pixel_acres -- area represented (pixel_count * ACRES_PER_PIXEL)
#   FORTYPCD    -- FIA forest type code
#   ForTypName  -- FIA forest type description
#   BALIVE      -- live basal area (sq ft/ac)
#   TPA_LIVE    -- live trees per acre
#   CARBON_L    -- live aboveground carbon (tons/ac)
#
# Because TreeMap draws FIA plots from the national database, PLT_CNs
# in this file may originate from states other than Florida. The
# multistate search script identifies which state database each
# PLT_CN resides in and creates subsetted SQLite databases accordingly.
# tmid_list <- county_data %>%
#   select(Value, PLT_CN, pixel_count, pixel_acres,
#          FORTYPCD, ForTypName, BALIVE, TPA_LIVE, CARBON_L)

tmid_list <- county_data %>%
  select(Value, PLT_CN, pixel_count, pixel_acres,
         FORTYPCD, ForTypName, BALIVE, TPA_LIVE, CARBON_L) %>%
  mutate(PLT_CN = as.character(PLT_CN))  # 

write.csv(tmid_list, file.path(output_path,"FL_5county_TreeMap_TMIDs.csv"), row.names = FALSE)
cat("TM_ID list saved to output/FL_5county_TreeMap_TMIDs.csv\n")


# ---- Five-county aggregate summary ----
# Area-weighted means for per-acre metrics; direct sums for totals.
# This summary is directly comparable to rFIA estimates derived from
# the subsetted FIA SQLite databases (after the PLT_CN search).
summary_5county <- county_data %>%
  summarise(
    AREA              = "FL_5county",
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

print("=== TreeMap Five-County Summary ===")
print(t(summary_5county))

write.csv(summary_5county, file.path(output_path, "FL_5county_TreeMap_summary.csv"),
          row.names = FALSE)


# ---- Forest type summary (five-county area) ----
# Groups pixels by FORTYPCD and ForTypName and computes area-weighted
# per-acre metrics for each forest type. PCT_ACRES expresses each
# type's share of total five-county forested acres.
#
# This table is used as the reference denominator (FORTYP_5COUNTY_ACRES)
# in subset_FIA_SQLite_multistateR.R when computing PCT_OF_FORTYP --
# the fraction of each forest type's five-county acreage attributable
# to plots from each state database.
fortyp_5county <- county_data %>%
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

print("=== TreeMap Five-County Forest Type Summary ===")
print(fortyp_5county, n = Inf)

write.csv(fortyp_5county, file.path(output_path, "FL_5county_TreeMap_ForestType_summary.csv"),
          row.names = FALSE)
cat("Five-county outputs saved.\n")


# ============================================================
# SECTION 5: INDIVIDUAL COUNTY SUMMARIES
# ============================================================
#
# Repeats the crop/mask/freq/join workflow for each county
# individually. This enables direct comparison of TreeMap estimates
# against FIA county-level estimates derived from the subsetted
# SQLite databases (one county at a time via rFIA).
#
# The loop reuses the original raster (tm) and the full VAT (vat)
# already loaded in Sections 2 and 4 -- no need to reload.
#
# county_boundary[i, ] extracts a single-county SpatVector; project()
# reprojects it to the raster CRS before crop() and mask().
#
# as.int() is applied to each county raster for the same reason as
# in Section 3: to ensure freq() returns integer TM_IDs rather than
# ForTypName strings.

county_summaries <- list()

for (i in seq_len(nrow(county_boundary))) {

  cname <- county_boundary$NAME_2[i]
  cat(paste0("\nProcessing county: ", cname, "...\n"))

  # Reproject single-county boundary and extract pixels
  c_proj <- project(county_boundary[i, ], crs(tm))
  c_crop <- crop(tm, c_proj)
  c_mask <- mask(c_crop, c_proj)
  c_int  <- as.int(c_mask)         # strip VAT for correct freq() behavior
  c_freq <- freq(c_int, bylayer = FALSE)

  # freq() returns layer, value, count; drop layer column
  names(c_freq) <- c("Value", "pixel_count")
  c_freq <- c_freq %>% select(Value, pixel_count)

  # Join to VAT and filter to forested pixels
  c_data <- c_freq %>%
    left_join(vat, by = "Value") %>%
    filter(!is.na(BALIVE)) %>%
    mutate(pixel_acres = pixel_count * ACRES_PER_PIXEL)

  # Guard against counties with no forested pixels (unlikely for
  # these counties but included for robustness)
  if (nrow(c_data) == 0) {
    cat(paste0("  No forested pixels found for ", cname, "\n"))
    next
  }

  # Per-county aggregate summary -- same metrics as five-county summary
  c_summary <- c_data %>%
    summarise(
      COUNTY            = cname,
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

  county_summaries[[cname]] <- c_summary
}

# Combine individual county summaries into a single table
county_summary_table <- bind_rows(county_summaries)

print("=== TreeMap Individual County Summaries ===")
print(county_summary_table)

write.csv(county_summary_table, file.path(output_path, "FL_5county_TreeMap_by_county.csv"),
          row.names = FALSE)
cat("\nIndividual county summaries saved to output/FL_5county_TreeMap_by_county.csv\n")
cat("\n=== Analysis complete. ===\n")
