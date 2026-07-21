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


def run_fixture(output_directory):
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    gdb = str(output_directory / "leto_fixture.gdb")
    if arcpy.Exists(gdb):
        arcpy.management.Delete(gdb)
    arcpy.management.CreateFileGDB(str(output_directory), Path(gdb).name)
    arcpy.env.overwriteOutput = True

    spatial_reference = arcpy.SpatialReference(5070)
    large = _box(0, 0, 200, 200, spatial_reference)
    small = _box(300, 0, 310, 10, spatial_reference)
    multipart = large.union(small)
    parcels = _create_polygon_feature_class(
        gdb, "parcels", spatial_reference, [large]
    )
    units = _create_polygon_feature_class(
        gdb, "units", spatial_reference, [multipart]
    )
    stream = arcpy.Polyline(
        arcpy.Array([arcpy.Point(0, 100), arcpy.Point(200, 100)]),
        spatial_reference,
    )
    streams = _create_line_feature_class(
        gdb, "streams", spatial_reference, [stream]
    )

    treemap = _save_raster(
        np.array([[10, 20], [10, 20]], dtype="int32"),
        os.path.join(gdb, "treemap"),
        spatial_reference,
    )
    ownership = _save_raster(
        np.array([[4, 3], [3, 4]], dtype="int16"),
        os.path.join(gdb, "ownership"),
        spatial_reference,
    )

    domain = os.path.join(gdb, "domain")
    arcpy.ddd.RasterDomain(treemap, domain, "POLYGON")
    clipped_domain = os.path.join(gdb, "domain_clipped")
    arcpy.analysis.Clip(domain, parcels, clipped_domain)

    singlepart = os.path.join(gdb, "singlepart")
    arcpy.management.MultipartToSinglepart(units, singlepart)
    _calculate_acres(singlepart)
    layer = arcpy.management.MakeFeatureLayer(singlepart, "small_parts", "Acres < 5")
    arcpy.management.DeleteRows(layer)
    cleaned = os.path.join(gdb, "cleaned")
    arcpy.analysis.PairwiseClip(singlepart, parcels, cleaned)
    _calculate_acres(cleaned)
    arcpy.management.AddField(cleaned, "MU_ID", "LONG")
    arcpy.management.CalculateField(cleaned, "MU_ID", "!OBJECTID!", "PYTHON3")

    zonal = os.path.join(gdb, "ownership_zonal")
    arcpy.sa.ZonalStatisticsAsTable(
        cleaned, "MU_ID", ownership, zonal, "DATA", "MAJORITY"
    )
    ownership_codes = sorted(
        int(row[0]) for row in arcpy.da.SearchCursor(zonal, ["MAJORITY"])
    )

    smz_buffer = os.path.join(gdb, "smz_buffer")
    arcpy.analysis.Buffer(streams, smz_buffer, "35 Feet", dissolve_option="ALL")
    smz_intersection = os.path.join(gdb, "smz_intersection")
    arcpy.analysis.Intersect([cleaned, smz_buffer], smz_intersection)
    smz_area_by_mu = {}
    with arcpy.da.SearchCursor(smz_intersection, ["MU_ID", "SHAPE@AREA"]) as cursor:
        for mu_id, area in cursor:
            smz_area_by_mu[mu_id] = smz_area_by_mu.get(mu_id, 0.0) + area
    smz_pct = []
    with arcpy.da.SearchCursor(cleaned, ["MU_ID", "SHAPE@AREA"]) as cursor:
        for mu_id, unit_area in cursor:
            smz_pct.append(smz_area_by_mu.get(mu_id, 0.0) / unit_area * 100)

    return {
        "domain_count": int(arcpy.management.GetCount(clipped_domain)[0]),
        "cleanup_count": int(arcpy.management.GetCount(cleaned)[0]),
        "ownership_codes": ownership_codes,
        "smz_pct": sorted(smz_pct),
    }


if __name__ == "__main__":
    print(json.dumps(run_fixture(sys.argv[1]), sort_keys=True))
