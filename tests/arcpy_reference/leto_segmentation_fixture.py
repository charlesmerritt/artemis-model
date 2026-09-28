"""Run tiny LETO stage fixtures with ArcPy and emit invariant results as JSON."""

import json
import os
from pathlib import Path
import sys

import arcpy
import numpy as np


def _create_polygon_feature_class(gdb, name, spatial_reference, geometries):
    path = arcpy.management.CreateFeatureclass(
        gdb, name, "POLYGON", spatial_reference=spatial_reference
    )[0]
    with arcpy.da.InsertCursor(path, ["SHAPE@"]) as cursor:
        for geometry in geometries:
            cursor.insertRow([geometry])
    return path


def _create_line_feature_class(gdb, name, spatial_reference, geometries):
    path = arcpy.management.CreateFeatureclass(
        gdb, name, "POLYLINE", spatial_reference=spatial_reference
    )[0]
    with arcpy.da.InsertCursor(path, ["SHAPE@"]) as cursor:
        for geometry in geometries:
            cursor.insertRow([geometry])
    return path


def _box(xmin, ymin, xmax, ymax, spatial_reference):
    points = [
        arcpy.Point(xmin, ymin),
        arcpy.Point(xmin, ymax),
        arcpy.Point(xmax, ymax),
        arcpy.Point(xmax, ymin),
        arcpy.Point(xmin, ymin),
    ]
    return arcpy.Polygon(arcpy.Array(points), spatial_reference)


def _save_raster(array, path, spatial_reference):
    raster = arcpy.NumPyArrayToRaster(
        array,
        lower_left_corner=arcpy.Point(0, 0),
        x_cell_size=100,
        y_cell_size=100,
        value_to_nodata=-9999,
    )
    raster.save(path)
    arcpy.management.DefineProjection(path, spatial_reference)
    return path


def _calculate_acres(feature_class):
    if "Acres" not in {field.name for field in arcpy.ListFields(feature_class)}:
        arcpy.management.AddField(feature_class, "Acres", "DOUBLE")
    arcpy.management.CalculateGeometryAttributes(
        feature_class, [["Acres", "AREA"]], area_unit="ACRES_US"
    )


def _geometries(feature_class):
    return [row[0] for row in arcpy.da.SearchCursor(feature_class, ["SHAPE@"])]


def _coverage_metrics(feature_class, parent_geometry):
    geometries = _geometries(feature_class)
    coverage = geometries[0]
    for geometry in geometries[1:]:
        coverage = coverage.union(geometry)
    check_table = os.path.join(os.path.dirname(feature_class), "geometry_check")
    arcpy.management.CheckGeometry(feature_class, check_table)
    return {
        "pre_cleanup_coverage_ratio": coverage.area / parent_geometry.area,
        "pre_cleanup_overlap_acres": (
            sum(geometry.area for geometry in geometries) - coverage.area
        )
        / 4_046.872609874251,
        "pre_cleanup_children_valid": int(arcpy.management.GetCount(check_table)[0])
        == 0,
    }


def _treemap_attribution(feature_class, values, spatial_reference):
    weights = []
    modal_in_donors = []
    with arcpy.da.SearchCursor(feature_class, ["MU_ID", "SHAPE@"]) as cursor:
        for mu_id, geometry in cursor:
            counts = {}
            for row in range(values.shape[0]):
                for column in range(values.shape[1]):
                    point = arcpy.PointGeometry(
                        arcpy.Point(column * 100 + 50, row * 100 + 50),
                        spatial_reference,
                    )
                    if geometry.contains(point):
                        value = int(values[values.shape[0] - row - 1, column])
                        counts[value] = counts.get(value, 0) + 1
            if not counts:
                raise RuntimeError("ArcPy child received no TreeMap donor cells")
            total = sum(counts.values())
            donor_rows = [
                {
                    "TM_VALUE": value,
                    "PLT_CN": "plot-{}".format(value),
                    "CELL_COUNT": count,
                    "WEIGHT": count / total,
                }
                for value, count in sorted(counts.items())
            ]
            modal = sorted(
                donor_rows, key=lambda row: (-row["CELL_COUNT"], row["TM_VALUE"])
            )[0]["PLT_CN"]
            weights.append(sum(row["WEIGHT"] for row in donor_rows))
            modal_in_donors.append(modal in {row["PLT_CN"] for row in donor_rows})
    return weights, modal_in_donors


def run_fixture(output_directory):
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    gdb = str(output_directory / "leto_fixture.gdb")
    if arcpy.Exists(gdb):
        arcpy.management.Delete(gdb)
    arcpy.management.CreateFileGDB(str(output_directory), Path(gdb).name)
    arcpy.env.overwriteOutput = True

    spatial_reference = arcpy.SpatialReference(5070)
    large = _box(0, 0, 1_200, 900, spatial_reference)
    parcels = _create_polygon_feature_class(gdb, "parcels", spatial_reference, [large])
    treemap_values = np.tile([10] * 6 + [20] * 6, (9, 1)).astype("int32")
    treemap = _save_raster(
        treemap_values,
        os.path.join(gdb, "treemap"),
        spatial_reference,
    )

    domain = os.path.join(gdb, "domain")
    arcpy.ddd.RasterDomain(treemap, domain, "POLYGON")
    clipped_domain = os.path.join(gdb, "domain_clipped")
    arcpy.analysis.Clip(domain, parcels, clipped_domain)
    clipped_parent = _geometries(clipped_domain)[0]

    random_points = os.path.join(gdb, "random_points")
    arcpy.management.CreateRandomPoints(
        gdb,
        "random_points",
        constraining_feature_class=clipped_domain,
        number_of_points_or_field=3,
        minimum_allowed_distance="1000 Feet",
    )
    thiessen = os.path.join(gdb, "thiessen")
    arcpy.env.extent = clipped_domain
    arcpy.analysis.CreateThiessenPolygons(random_points, thiessen, "ALL")
    subdivided = os.path.join(gdb, "subdivided")
    arcpy.analysis.PairwiseClip(thiessen, clipped_domain, subdivided)
    coverage_metrics = _coverage_metrics(subdivided, clipped_parent)

    singlepart = os.path.join(gdb, "singlepart")
    arcpy.management.MultipartToSinglepart(subdivided, singlepart)
    _calculate_acres(singlepart)
    layer = arcpy.management.MakeFeatureLayer(singlepart, "small_parts", "Acres < 5")
    arcpy.management.DeleteRows(layer)
    cleaned = os.path.join(gdb, "cleaned")
    arcpy.analysis.PairwiseClip(singlepart, parcels, cleaned)
    _calculate_acres(cleaned)
    arcpy.management.AddField(cleaned, "MU_ID", "LONG")
    arcpy.management.CalculateField(cleaned, "MU_ID", "!OBJECTID!", "PYTHON3")
    cleanup_acres = sorted(
        float(row[0]) for row in arcpy.da.SearchCursor(cleaned, ["Acres"])
    )
    weight_sums, modal_in_donors = _treemap_attribution(
        cleaned, treemap_values, spatial_reference
    )

    return {
        "domain_count": int(arcpy.management.GetCount(clipped_domain)[0]),
        "parent_acres": clipped_parent.area / 4_046.872609874251,
        "parent_wkt": clipped_parent.WKT,
        **coverage_metrics,
        "cleanup_count": int(arcpy.management.GetCount(cleaned)[0]),
        "cleanup_acres": cleanup_acres,
        "sliver_count": sum(acres < 5 for acres in cleanup_acres),
        "oversized_count": sum(acres > 200 for acres in cleanup_acres),
        "weight_sums": weight_sums,
        "modal_in_donors": modal_in_donors,
    }


if __name__ == "__main__":
    print(json.dumps(run_fixture(sys.argv[1]), sort_keys=True))
