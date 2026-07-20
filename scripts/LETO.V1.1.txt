import pandas as pd
import arcpy
import math
import os
import traceback
import numpy as np

# -----------------------------------------------------------
# USER PARAMETERS
# -----------------------------------------------------------

# Fill in proper paths to data used. 

#First your gdb you will be working out of:

WORKSPACE = r"D:\forest_condition_2026\Stand_delineation\Attempt3\Attempt3.gdb"

# Also, create a folder for the weighted plot cn table and add the path here, this folder will be used again in future scripts.

out_folder = r"D:\forest_condition_2026\FVS\FVS_Inputs"

#Then TreeMap

TREEMAP = r"D:\forest_condition_2026\Stand_delineation\TreeMap_2022\TreeMap_2022.tif"

#The RDS 2025 Forest Service publication ownership raster layer @ https://www.fs.usda.gov/rds/archive/catalog/RDS-2025-0045?

OWNERSHIP_RASTER = r"D:\forest_condition_2026\Stand_delineation\RDS-2025-0045 (1)\Data\US_forest_ownership.tif"

OWNERSHIP_LOOKUP = {
    0: "Unknown Forest",
    1: "Non-Forest",
    2: "Water",
    3: "Family Forest",
    4: "Corporate/Other Private Forest",
    5: "Tribal Forest",
    6: "Federal Forest",
    7: "State Forest",
    8: "Local Forest"
}

#Finally a confining layer for the output, for example, forest parcels in the Florida five county area. This must be inside the gdb above.

CLIP_FEATURES = "FL_5_Co_Parcels"

OUTPUT_NAME = "ManagementUnits"

#Edit the 3 parameter below to alter Voronoi tile sizes

MAX_ACRES = 200

ACRES_PER_POINT = 100

MIN_DISTANCE = "1000 Feet"

# The next two alter stream buffers

#SMZ_FEATURES Should again be in your .gdb

SMZ_FEATURES = "FL_5_Co_Streams"

SMZ_BUFFER = "35 Feet"


SPATIAL_REFERENCE = arcpy.SpatialReference(26917)


# -----------------------------------------------------------
# ENVIRONMENT
# -----------------------------------------------------------

arcpy.env.workspace = WORKSPACE
arcpy.env.overwriteOutput = True

arcpy.CheckOutExtension("Spatial")
arcpy.CheckOutExtension("ImageAnalyst")

# Helper function

def message(text):

    arcpy.AddMessage(text)

    print(text)

# calculate acreage -------------------------------------------------------

def calculate_acres(fc):

    fields = [f.name for f in arcpy.ListFields(fc)]

    if "Acres" not in fields:

        arcpy.management.AddField(
            fc,
            "Acres",
            "DOUBLE"
        )

    arcpy.management.CalculateGeometryAttributes(
        fc,
        [["Acres", "AREA"]],
        area_unit="ACRES_US"
    )


# Count large polygons -------------------------------------------------------

def count_large_polygons(fc):

    where = f"Acres > {MAX_ACRES}"

    layer = arcpy.management.MakeFeatureLayer(
        fc,
        "large_layer",
        where
    )

    return int(arcpy.management.GetCount(layer)[0])


# Create initial polygon layer -------------------------------------------------------

def create_initial_polygons():

    message("Creating raster domain...")

    domain = os.path.join("memory", "domain")

    arcpy.ddd.RasterDomain(
        TREEMAP,
        domain,
        "POLYGON"
    )

    message("Clipping to county boundary...")

    clipped = os.path.join(WORKSPACE, OUTPUT_NAME)

    arcpy.analysis.Clip(
        domain,
        CLIP_FEATURES,
        clipped
    )

    calculate_acres(clipped)

    calculate_points(clipped)

    return clipped


# Create random points -------------------------------------------------------

def create_random_points(fc):

    message("Creating random points...")

    pts = os.path.join("memory", "random_points")

    if arcpy.Exists(pts):
        arcpy.management.Delete(pts)

    arcpy.management.CreateRandomPoints(
        out_path="memory",
        out_name="random_points",
        constraining_feature_class=fc,
        number_of_points_or_field="Points",
        minimum_allowed_distance=MIN_DISTANCE
    )

    return pts

# Thiessen Polygons -------------------------------------------------------

def create_thiessen(points):

    message("Creating Voronoi polygons...")

    thiessen = os.path.join("memory", "thiessen")

    if arcpy.Exists(thiessen):
        arcpy.management.Delete(thiessen)

    arcpy.analysis.CreateThiessenPolygons(
        points,
        thiessen
    )

    return thiessen

# Clip back to parent polygon -------------------------------------------------------

def clip_voronoi(thiessen, parent):

    clipped = os.path.join("memory", "thiessen_clip")

    if arcpy.Exists(clipped):
        arcpy.management.Delete(clipped)

    arcpy.analysis.Clip(
        thiessen,
        parent,
        clipped
    )

    calculate_acres(clipped)

    return clipped

#Select large polygons

def get_large_polygons(fc):

    layer = arcpy.management.MakeFeatureLayer(
        fc,
        "large_layer",
        f"Acres > {MAX_ACRES}"
    )

    return layer

#Export large polygons-------------------------------------------------------

def export_large(layer):

    out_fc = os.path.join("memory", "large")

    if arcpy.Exists(out_fc):
        arcpy.management.Delete(out_fc)

    arcpy.conversion.ExportFeatures(
        layer,
        out_fc
    )

    return out_fc

# Split working layer ---------------------------------------------------------

def split_working_fc(fc):
    """
    Split the feature class into
    1) polygons <= MAX_ACRES
    2) polygons > MAX_ACRES
    """

    small = r"memory\small"
    large = r"memory\large"

    for dataset in (small, large):
        if arcpy.Exists(dataset):
            arcpy.management.Delete(dataset)

    arcpy.analysis.Select(
        fc,
        small,
        f"Acres <= {MAX_ACRES}"
    )

    arcpy.analysis.Select(
        fc,
        large,
        f"Acres > {MAX_ACRES}"
    )

    return small, large

# count large polygons

def large_count(fc):

    return int(arcpy.management.GetCount(fc)[0])

# Tessellate one feature class -------------------------------------------------------------------

def tessellate(fc):

    calculate_points(fc)

    points = create_random_points(fc)

    thiessen = create_thiessen(points)

    clipped = clip_voronoi(thiessen, fc)

    calculate_acres(clipped)

    return clipped

def calculate_points(fc):
    fields = [f.name for f in arcpy.ListFields(fc)]

    if "Points" not in fields:
        arcpy.management.AddField(fc, "Points", "LONG")

    arcpy.management.CalculateField(
        fc,
        "Points",
        f"max(2, math.ceil(!Acres! / {ACRES_PER_POINT}))",
        "PYTHON3",
        "import math"
    )


def initialize_parent_id(fc):
    fields = [f.name for f in arcpy.ListFields(fc)]

    if "ParentID" not in fields:
        arcpy.management.AddField(fc, "ParentID", "LONG")

    arcpy.management.CalculateField(
        fc,
        "ParentID",
        "!OBJECTID!",
        "PYTHON3"
    )


def merge_iteration(small, replacement, iteration):
    out_fc = os.path.join(WORKSPACE, f"working_iter_{iteration}")

    if arcpy.Exists(out_fc):
        arcpy.management.Delete(out_fc)

    arcpy.management.Merge(
        [small, replacement],
        out_fc
    )

    calculate_acres(out_fc)
    calculate_points(out_fc)

    return out_fc

#Break up multipart polygons and delete polygons less than 5 acres -------------------------------------------

def multipart_to_singlepart_and_delete_small(fc, min_acres=5):
    message("Converting multipart polygons to singlepart...")

    singlepart = os.path.join(WORKSPACE, "working_singlepart")

    if arcpy.Exists(singlepart):
        arcpy.management.Delete(singlepart)

    arcpy.management.MultipartToSinglepart(
        in_features=fc,
        out_feature_class=singlepart
    )

    calculate_acres(singlepart)

    message(f"Deleting polygons smaller than {min_acres} acres...")

    layer = arcpy.management.MakeFeatureLayer(
        singlepart,
        "singlepart_layer"
    )

    arcpy.management.SelectLayerByAttribute(
        layer,
        "NEW_SELECTION",
        f"Acres < {min_acres}"
    )

    delete_count = int(arcpy.management.GetCount(layer)[0])
    message(f"Small polygons selected for deletion: {delete_count}")

    if delete_count > 0:
        arcpy.management.DeleteRows(layer)

    arcpy.management.SelectLayerByAttribute(
        layer,
        "CLEAR_SELECTION"
    )

    calculate_acres(singlepart)

    return singlepart

# Clip to parcel layer ------------------------------------------------------------------------

def clip_to_parcels(fc):
    message("Clipping management units back to parcel boundaries...")

    clipped = os.path.join(WORKSPACE, "working_parcel_clipped")

    if arcpy.Exists(clipped):
        arcpy.management.Delete(clipped)

    arcpy.analysis.PairwiseClip(
        in_features=fc,
        clip_features=CLIP_FEATURES,
        out_feature_class=clipped
    )

    calculate_acres(clipped)

    return clipped


# create a permanent ID ------------------------------------------------------------------------

def initialize_mu_id(fc):
    fields = [f.name for f in arcpy.ListFields(fc)]

    if "MU_ID" not in fields:
        arcpy.management.AddField(fc, "MU_ID", "LONG")

    arcpy.management.CalculateField(
        fc,
        "MU_ID",
        "!OBJECTID!",
        "PYTHON3"
    )

    return fc



#Assign FIA plot CNs -----------------------------------------------------------------


def assign_plt_cn(fc):
    message("Building weighted MU × PLT_CN table using raster arrays...")

    os.makedirs(out_folder, exist_ok=True)

    weights_csv = os.path.join(out_folder, "MU_PLT_CN_Weights.csv")

    mu_raster = os.path.join(WORKSPACE, "MU_ID_Raster")

    if arcpy.Exists(mu_raster):
        arcpy.management.Delete(mu_raster)

    # Match TreeMap grid
    arcpy.env.snapRaster = TREEMAP
    arcpy.env.cellSize = TREEMAP
    arcpy.env.extent = fc

    # Convert management units to raster using MU_ID
    arcpy.conversion.PolygonToRaster(
        in_features=fc,
        value_field="MU_ID",
        out_rasterdataset=mu_raster,
        cell_assignment="MAXIMUM_AREA",
        priority_field="NONE",
        cellsize=TREEMAP
    )

    # Get raster geometry
    desc = arcpy.Describe(mu_raster)
    extent = desc.extent
    cell_size = desc.meanCellWidth

    lower_left = arcpy.Point(extent.XMin, extent.YMin)
    ncols = int(desc.width)
    nrows = int(desc.height)

    nodata = -999999999

    # Convert both rasters to aligned arrays
    mu_arr = arcpy.RasterToNumPyArray(
        mu_raster,
        lower_left_corner=lower_left,
        ncols=ncols,
        nrows=nrows,
        nodata_to_value=nodata
    )

    tm_arr = arcpy.RasterToNumPyArray(
        TREEMAP,
        lower_left_corner=lower_left,
        ncols=ncols,
        nrows=nrows,
        nodata_to_value=nodata
    )

    # Keep only cells with both MU_ID and TreeMap value
    valid = (mu_arr != nodata) & (tm_arr != nodata)

    mu_flat = mu_arr[valid].astype("int64")
    tm_flat = tm_arr[valid].astype("int64")

    cell_df = pd.DataFrame({
        "MU_ID": mu_flat,
        "TM_VALUE": tm_flat
    })

    counts = (
        cell_df
        .groupby(["MU_ID", "TM_VALUE"])
        .size()
        .reset_index(name="CELL_COUNT")
    )

    # Build TreeMap Value -> PLT_CN lookup
    tm_lookup = {}

    tm_fields = [f.name for f in arcpy.ListFields(TREEMAP)]

    if "PLT_CN" not in tm_fields:
        raise RuntimeError("PLT_CN field not found in TreeMap raster attribute table.")

    with arcpy.da.SearchCursor(TREEMAP, ["Value", "PLT_CN"]) as cur:
        for value, plt_cn in cur:
            if value is not None and plt_cn is not None:
                tm_lookup[int(value)] = str(int(plt_cn))

    counts["PLT_CN"] = counts["TM_VALUE"].map(tm_lookup)

    counts = counts.dropna(subset=["PLT_CN"]).copy()

    if counts.empty:
        raise RuntimeError("Weighted MU × PLT_CN table came back empty.")

    counts["TOTAL_CELLS"] = counts.groupby("MU_ID")["CELL_COUNT"].transform("sum")
    counts["WEIGHT"] = counts["CELL_COUNT"] / counts["TOTAL_CELLS"]

    counts["MU_ID"] = counts["MU_ID"].astype(str)
    counts["PLT_CN"] = counts["PLT_CN"].astype(str)

    counts.to_csv(weights_csv, index=False)

    message(f"Created weighted PLT_CN table: {weights_csv}")
    message(f"Weighted rows: {len(counts)}")
    message(f"Unique MUs in weights: {counts['MU_ID'].nunique()}")
    message(f"Unique PLT_CNs in weights: {counts['PLT_CN'].nunique()}")

    # Keep majority PLT_CN on polygons for QA / mapping
    majority = (
        counts.sort_values(["MU_ID", "CELL_COUNT"], ascending=[True, False])
        .drop_duplicates(subset=["MU_ID"])
        [["MU_ID", "PLT_CN", "TM_VALUE"]]
        .copy()
    )

    majority_lookup = dict(zip(majority["MU_ID"], majority["PLT_CN"]))
    tm_value_lookup = dict(zip(majority["MU_ID"], majority["TM_VALUE"]))

    fields = [f.name for f in arcpy.ListFields(fc)]

    if "PLT_CN" not in fields:
        arcpy.management.AddField(fc, "PLT_CN", "DOUBLE")

    if "TM_Value" not in fields:
        arcpy.management.AddField(fc, "TM_Value", "LONG")

    with arcpy.da.UpdateCursor(fc, ["MU_ID", "PLT_CN", "TM_Value"]) as cur:
        for mu_id, plt_cn, tm_value in cur:
            key = str(int(mu_id))

            if key in majority_lookup:
                cur.updateRow([
                    mu_id,
                    float(majority_lookup[key]),
                    int(tm_value_lookup[key])
                ])
            else:
                cur.updateRow([mu_id, None, None])

    calculate_acres(fc)

    return fc



# Calculate majority ownership-------------------------------------------------------------

def assign_majority_raster(fc, raster, out_code_field, out_text_field=None, lookup=None):
    message(f"Assigning {out_code_field} from majority raster value...")
    
    fields = [f.name for f in arcpy.ListFields(fc)]

    for field in fields:
        if field.upper().startswith("MAJORITY"):
            arcpy.management.DeleteField(fc, field)

    zonal_table = os.path.join(
        arcpy.env.scratchGDB,
        f"zonal_{out_code_field}"
    )

    if arcpy.Exists(zonal_table):
        arcpy.management.Delete(zonal_table)

    arcpy.sa.ZonalStatisticsAsTable(
        in_zone_data=fc,
        zone_field="MU_ID",
        in_value_raster=raster,
        out_table=zonal_table,
        ignore_nodata="DATA",
        statistics_type="MAJORITY"
    )

    arcpy.management.JoinField(
        fc,
        "MU_ID",
        zonal_table,
        "MU_ID",
        ["MAJORITY"]
    )

    existing = [f.name for f in arcpy.ListFields(fc)]

    if out_code_field not in existing:
        arcpy.management.AddField(fc, out_code_field, "LONG")

    if out_text_field and out_text_field not in existing:
        arcpy.management.AddField(fc, out_text_field, "TEXT", field_length=60)

    if out_text_field:
        fields = ["MAJORITY", out_code_field, out_text_field]

        with arcpy.da.UpdateCursor(fc, fields) as cur:
            for majority, code_val, text_val in cur:
                if majority is None:
                    cur.updateRow([majority, None, None])
                else:
                    code = int(majority)
                    text = lookup.get(code, "Unknown") if lookup else str(code)
                    cur.updateRow([majority, code, text])

    else:
        fields = ["MAJORITY", out_code_field]

        with arcpy.da.UpdateCursor(fc, fields) as cur:
            for majority, code_val in cur:
                if majority is None:
                    cur.updateRow([majority, None])
                else:
                    cur.updateRow([majority, int(majority)])

    arcpy.management.DeleteField(fc, ["MAJORITY"])

    return fc

# Buffer perennial waters, and calculate percentage of polygons ---------------------------------------

def calculate_smz_percent(fc):
    message("Calculating SMZ percentage using vector overlay...")

    smz_buffer = os.path.join("memory", "smz_buffer")
    smz_intersect = os.path.join("memory", "smz_intersect")
    smz_stats = os.path.join(WORKSPACE, "MU_SMZ_Stats")

    for dataset in [smz_buffer, smz_intersect, smz_stats]:
        if arcpy.Exists(dataset):
            arcpy.management.Delete(dataset)


    # Buffer streams/perennial water
    arcpy.analysis.Buffer(
        in_features=SMZ_FEATURES,
        out_feature_class=smz_buffer,
        buffer_distance_or_field=SMZ_BUFFER,
        dissolve_option="ALL"
    )

    # Intersect management units with SMZ buffer
    arcpy.analysis.Intersect(
        in_features=[fc, smz_buffer],
        out_feature_class=smz_intersect,
        join_attributes="ALL",
        output_type="INPUT"
    )

    # Calculate intersected SMZ acreage
    if "SMZ_IntAc" not in [f.name for f in arcpy.ListFields(smz_intersect)]:
        arcpy.management.AddField(smz_intersect, "SMZ_IntAc", "DOUBLE")

    arcpy.management.CalculateGeometryAttributes(
        smz_intersect,
        [["SMZ_IntAc", "AREA"]],
        area_unit="ACRES_US"
    )

    # Sum SMZ acres by management unit ID
    arcpy.analysis.Statistics(
        in_table=smz_intersect,
        out_table=smz_stats,
        statistics_fields=[["SMZ_IntAc", "SUM"]],
        case_field="MU_ID"
    )

    # Add output fields
    fields = [f.name for f in arcpy.ListFields(fc)]

    if "SMZ_Acres" not in fields:
        arcpy.management.AddField(fc, "SMZ_Acres", "DOUBLE")

    if "SMZ_Pct" not in fields:
        arcpy.management.AddField(fc, "SMZ_Pct", "DOUBLE")

    # Join SMZ acreage back to management units
    arcpy.management.JoinField(
        in_data=fc,
        in_field="MU_ID",
        join_table=smz_stats,
        join_field="MU_ID",
        fields=["SUM_SMZ_IntAc"]
    )

    # Safely calculate SMZ_Acres
    acres_code = """
def calc_smz_acres(value):
    if value is None:
        return 0
    return value
"""

    arcpy.management.CalculateField(
        fc,
        "SMZ_Acres",
        "calc_smz_acres(!SUM_SMZ_IntAc!)",
        "PYTHON3",
        acres_code
    )

    # Safely calculate SMZ percent
    pct_code = """
def calc_smz_pct(smz_acres, total_acres):
    if smz_acres is None:
        smz_acres = 0
    if total_acres is None or total_acres == 0:
        return 0
    return (smz_acres / total_acres) * 100
"""

    arcpy.management.CalculateField(
        fc,
        "SMZ_Pct",
        "calc_smz_pct(!SMZ_Acres!, !Acres!)",
        "PYTHON3",
        pct_code
    )

    message("SMZ vector percentage calculation complete.")

    return fc

#Final Cleanup and export -------------------------------

def export_final(fc):

    final_fc = os.path.join(WORKSPACE, OUTPUT_NAME + "_Final")

    if arcpy.Exists(final_fc):
        arcpy.management.Delete(final_fc)

    arcpy.management.CopyFeatures(fc, final_fc)

    calculate_acres(final_fc)

    # Rename MAJORITY to something meaningful
    fields = [f.name for f in arcpy.ListFields(final_fc)]

    if "MAJORITY" in fields:
        arcpy.management.AlterField(
            final_fc,
            "MAJORITY",
            "TM_Value",
            "TreeMap_Value"
        )

    # Remove temporary fields
    drop_fields = []

    fields = [f.name for f in arcpy.ListFields(final_fc)]

    for field in ["RASTERVALU", "Input_FID", "SUM", "VALUE_1", "SUM_SMZ_IntAc", "ParentID"]:
        if field in fields:
            drop_fields.append(field)

    if drop_fields:
        arcpy.management.DeleteField(final_fc, drop_fields)

    message(f"Final output created: {final_fc}")

    return final_fc


# Main loop ---------------------------------------------------------------------------

working = create_initial_polygons()

# Assign each original polygon a permanent ID
initialize_parent_id(working)

iteration = 1

while True:

    message(f"Iteration {iteration}")

    small, large = split_working_fc(working)

    if large_count(large) == 0:
        message("Subdivision complete.")
        break

    replacement = tessellate(large)

    working = merge_iteration(
        small,
        replacement,
        iteration
    )

    iteration += 1

#Closing out ---------------------------------------------------------------------------------

message(f"Final working feature class: {working}")

calculate_acres(working)


message(f"Final polygon count before singlepart cleanup: {arcpy.management.GetCount(working)[0]}")
message(f"Remaining polygons over {MAX_ACRES} acres: {count_large_polygons(working)}")

working = multipart_to_singlepart_and_delete_small(working, 5)

working = clip_to_parcels(working)

working = initialize_mu_id(working)

message(f"Final polygon count after deleting polygons under 5 acres: {arcpy.management.GetCount(working)[0]}")

working = assign_plt_cn(working)

message("PLT_CN assignment complete.")

working = assign_majority_raster(
    working,
    OWNERSHIP_RASTER,
    "OWN_CODE",
    "OWN_TYPE",
    OWNERSHIP_LOOKUP
)

message("Ownership assignment complete.")

working = calculate_smz_percent(working)

message("SMZ calculation complete.")

final_output = export_final(working)

message(f"Final output: {final_output}")
message("Landscape sample extracted successfully.")