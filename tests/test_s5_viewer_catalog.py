"""Tests for the viewer bridge (pipeline/s5_imagery/viewer_catalog.py)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s5_imagery import viewer_catalog as vc


def _year(year, export=None, tile_url="https://ee.example/{z}/{x}/{y}", complete=True):
    return {
        "year": year,
        "coverage": 0.9999 if complete else 0.5,
        "complete": complete,
        "contributing_years": [year],
        "image_count": 8,
        "bounds": [-82.62, 30.08, -82.57, 30.13],
        "tile_url": tile_url,
        "tile_url_generated_utc": "2026-08-10T12:00:00+00:00",
        "export": export,
    }


def _manifest(years):
    return {
        "schema": "artemis.naip.manifest/1",
        "name": "Test Stands",
        "slug": "test_stands",
        "collection": "USDA/NAIP/DOQQ",
        "bands": ["R", "G", "B"],
        "scale_m": 1.0,
        "coverage": {"mode": "fill", "min_coverage": 0.999},
        "extent": {"bounds": [-82.62, 30.08, -82.57, 30.13], "area_ha": 2500.0},
        "aoi": {"bounds": [-82.60, 30.10, -82.59, 30.11], "area_ha": 100.0,
                "containment_in_extent": 1.0},
        "years": years,
        "incomplete_years": [entry["year"] for entry in years if not entry["complete"]],
    }


def _run(method="kmeans", label="k-means (Euclidean)", k=4, auto_k=False, export=None):
    return {
        "method": method,
        "label": label,
        "description": "…",
        "auto_k": auto_k,
        "k_requested": None if auto_k else k,
        "k_observed": k,
        "palette": ["#e69f00", "#56b4e9", "#009e73", "#f0e442"],
        "clusters": [],
        "separability": {"jensen_shannon_divergence": 0.3, "interpretation": "…"},
        "cluster_by_point": [],
        "layers": {"cluster_tile_url": "https://ee.example/c/{z}/{x}/{y}", "export": export},
    }


def _clusters(export=None, runs=None):
    return {
        "schema": "artemis.embeddings.clusters/2",
        "name": "Test Stands",
        "slug": "test_stands",
        "collection": "GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL",
        "year": 2024,
        "default_method": "kmeans",
        "runs": runs if runs is not None else [_run(export=export)],
        "scatter": {"points": [], "explained_variance_ratio": []},
        "extent": {"bounds": [-82.62, 30.08, -82.57, 30.13]},
        "aoi": {"bounds": [-82.60, 30.10, -82.59, 30.11]},
    }


DRIVE_EXPORT = {"target": "drive", "description": "naip_test_2021", "filename": "naip_test_2021.tif",
                "folder": "artemis_naip"}
GCS_EXPORT = {"target": "gcs", "description": "naip_test_2021", "filename": "naip_test_2021.tif",
              "bucket": "my-bucket", "object": "naip/naip_test_2021.tif"}


# ---- resolve_cog_url ----


def test_resolve_cog_url_gcs_resolves_itself():
    assert (
        vc.resolve_cog_url(GCS_EXPORT, None)
        == "https://storage.googleapis.com/my-bucket/naip/naip_test_2021.tif"
    )


def test_resolve_cog_url_drive_needs_a_public_base():
    assert vc.resolve_cog_url(DRIVE_EXPORT, None) is None


def test_resolve_cog_url_public_base_wins_and_normalizes_slash():
    assert (
        vc.resolve_cog_url(DRIVE_EXPORT, "https://cdn.example.com/naip/")
        == "https://cdn.example.com/naip/naip_test_2021.tif"
    )


def test_resolve_cog_url_none_export():
    assert vc.resolve_cog_url(None, "https://cdn.example.com") is None


def test_resolve_cog_url_export_without_filename():
    assert vc.resolve_cog_url({"target": "drive"}, "https://cdn.example.com") is None


# ---- build_layers_json ----


def test_layers_json_always_carries_both_vector_roles():
    catalog = vc.build_layers_json(_manifest([_year(2021)]), None, None, None, "Test Stands")
    geojson_layers = [layer for layer in catalog["layers"] if layer["type"] == "geojson"]

    assert len(geojson_layers) == 2
    urls = {layer["url"] for layer in geojson_layers}
    assert urls == {"artemis/extent.geojson", "artemis/aoi.geojson"}
    # The two roles must be visually distinguishable on the map.
    assert geojson_layers[0]["style"]["color"] != geojson_layers[1]["style"]["color"]


def test_layers_json_omits_ephemeral_tile_urls():
    # Tile URLs expire; layers.json is durable configuration, so they stay out.
    catalog = vc.build_layers_json(_manifest([_year(2021)]), None, None, None, "Test Stands")
    assert not any(layer["type"] == "cog" for layer in catalog["layers"])
    assert "ee.example" not in json.dumps(catalog)


def test_layers_json_builds_a_time_series_for_multiple_exported_years():
    manifest = _manifest([_year(2019, GCS_EXPORT), _year(2021, GCS_EXPORT)])
    catalog = vc.build_layers_json(manifest, None, None, None, "Test Stands")

    cog_layers = [layer for layer in catalog["layers"] if layer["type"] == "cog"]
    assert len(cog_layers) == 1
    assert len(cog_layers[0]["times"]) == 2
    assert cog_layers[0]["times"][0]["label"] == "2019"


def test_layers_json_single_year_is_not_a_time_series():
    manifest = _manifest([_year(2021, GCS_EXPORT)])
    catalog = vc.build_layers_json(manifest, None, None, None, "Test Stands")

    cog_layer = next(layer for layer in catalog["layers"] if layer["type"] == "cog")
    assert "times" not in cog_layer
    assert cog_layer["url"].endswith("naip_test_2021.tif")


def test_layers_json_skips_years_without_a_resolvable_url():
    manifest = _manifest([_year(2019, GCS_EXPORT), _year(2021, DRIVE_EXPORT)])
    catalog = vc.build_layers_json(manifest, None, None, None, "Test Stands")

    cog_layers = [layer for layer in catalog["layers"] if layer["type"] == "cog"]
    assert len(cog_layers) == 1
    assert "2021" not in cog_layers[0]["name"]


def test_layers_json_includes_exported_cluster_raster():
    cluster_export = {"target": "gcs", "filename": "clusters.tif", "bucket": "b",
                      "object": "e/clusters.tif"}
    catalog = vc.build_layers_json(None, _clusters(cluster_export), None, None, "Test Stands")

    cluster_layer = next(layer for layer in catalog["layers"] if layer["type"] == "cog")
    assert cluster_layer["style"]["max"] == 3  # k=4 → cluster ids 0..3


def test_layers_json_cluster_base_overrides_public_base():
    export = {"target": "drive", "filename": "clusters.tif"}
    catalog = vc.build_layers_json(
        None, _clusters(export), "https://naip.example", "https://clusters.example", "Test"
    )
    cluster_layer = next(layer for layer in catalog["layers"] if layer["type"] == "cog")
    assert cluster_layer["url"] == "https://clusters.example/clusters.tif"


def test_layers_json_gives_each_clustering_method_its_own_layer():
    # Separate rasters, not timesteps — they should be toggleable against each other.
    runs = [
        _run("kmeans", "k-means (Euclidean)", k=4,
             export={"target": "gcs", "filename": "k.tif", "bucket": "b", "object": "e/k.tif"}),
        _run("xmeans", "X-means (auto k, BIC)", k=7, auto_k=True,
             export={"target": "gcs", "filename": "x.tif", "bucket": "b", "object": "e/x.tif"}),
    ]
    catalog = vc.build_layers_json(None, _clusters(runs=runs), None, None, "Test Stands")

    cog_layers = [layer for layer in catalog["layers"] if layer["type"] == "cog"]
    assert len(cog_layers) == 2
    assert "k-means" in cog_layers[0]["name"]
    assert "X-means" in cog_layers[1]["name"]
    assert cog_layers[1]["style"]["max"] == 6
    assert "(auto)" in cog_layers[1]["description"]


def test_layers_json_skips_methods_without_an_export():
    runs = [
        _run("kmeans", export={"target": "gcs", "filename": "k.tif", "bucket": "b",
                               "object": "e/k.tif"}),
        _run("lvq", "Learning vector quantization", export=None),
    ]
    catalog = vc.build_layers_json(None, _clusters(runs=runs), None, None, "Test Stands")
    cog_layers = [layer for layer in catalog["layers"] if layer["type"] == "cog"]
    assert len(cog_layers) == 1


# ---- build_catalog ----


def test_catalog_carries_coverage_provenance_per_year():
    manifest = _manifest([_year(2021)])
    manifest["years"][0]["contributing_years"] = [2020, 2021]
    catalog = vc.build_catalog(manifest, None, None, None, "Test Stands")

    assert catalog["schema"] == vc.CATALOG_SCHEMA
    year = catalog["naip"]["years"][0]
    assert year["contributing_years"] == [2020, 2021]
    assert year["tile_url"] == "https://ee.example/{z}/{x}/{y}"
    assert year["cog_url"] is None


def test_catalog_resolves_cog_urls_when_hosted():
    manifest = _manifest([_year(2021, DRIVE_EXPORT)])
    catalog = vc.build_catalog(manifest, None, "https://cdn.example.com/naip", None, "Test")
    assert catalog["naip"]["years"][0]["cog_url"] == (
        "https://cdn.example.com/naip/naip_test_2021.tif"
    )


def test_catalog_copies_vector_geometry_facts():
    catalog = vc.build_catalog(_manifest([_year(2021)]), None, None, None, "Test Stands")
    assert catalog["vectors"]["extent"]["area_ha"] == 2500.0
    assert catalog["vectors"]["aoi"]["containment_in_extent"] == 1.0


def test_catalog_embeds_the_cluster_payload():
    catalog = vc.build_catalog(None, _clusters(), None, None, "Test Stands")
    assert catalog["embeddings"]["default_method"] == "kmeans"
    assert catalog["embeddings"]["runs"][0]["k_observed"] == 4
    assert catalog["embeddings"]["runs"][0]["layers"]["cluster_cog_url"] is None
    assert catalog["naip"] is None


def test_catalog_resolves_a_cog_url_per_method():
    runs = [
        _run("kmeans", export={"target": "gcs", "filename": "k.tif", "bucket": "b",
                               "object": "e/k.tif"}),
        _run("cobweb", "Cobweb (hierarchical, emergent k)", k=9, auto_k=True, export=None),
    ]
    catalog = vc.build_catalog(None, _clusters(runs=runs), None, None, "Test Stands")

    resolved = {run["method"]: run["layers"]["cluster_cog_url"] for run in catalog["embeddings"]["runs"]}
    assert resolved["kmeans"] == "https://storage.googleapis.com/b/e/k.tif"
    assert resolved["cobweb"] is None


def test_catalog_preserves_every_method_run():
    runs = [_run("kmeans"), _run("xmeans", "X-means", k=7, auto_k=True), _run("lvq", "LVQ")]
    catalog = vc.build_catalog(None, _clusters(runs=runs), None, None, "Test Stands")
    assert [run["method"] for run in catalog["embeddings"]["runs"]] == ["kmeans", "xmeans", "lvq"]


def test_catalog_falls_back_to_cluster_bounds_without_a_manifest():
    catalog = vc.build_catalog(None, _clusters(), None, None, "Test Stands")
    assert catalog["vectors"]["extent"]["bounds"] == [-82.62, 30.08, -82.57, 30.13]


def test_catalog_reports_incomplete_years():
    manifest = _manifest([_year(2019, complete=False), _year(2021)])
    catalog = vc.build_catalog(manifest, None, None, None, "Test Stands")
    assert catalog["naip"]["incomplete_years"] == [2019]


# ---- main ----


def test_main_writes_catalog_files(tmp_path):
    source = tmp_path / "naip"
    source.mkdir()
    (source / "naip_manifest.json").write_text(json.dumps(_manifest([_year(2021, GCS_EXPORT)])))
    (source / "extent.geojson").write_text('{"type":"FeatureCollection","features":[]}')
    (source / "aoi.geojson").write_text('{"type":"FeatureCollection","features":[]}')

    out_dir = tmp_path / "out"
    exit_code = vc.main(
        [
            "--naip-manifest",
            str(source / "naip_manifest.json"),
            "--out-dir",
            str(out_dir),
        ]
    )

    assert exit_code == 0
    layers = json.loads((out_dir / "layers.json").read_text())
    catalog = json.loads((out_dir / "artemis" / "catalog.json").read_text())

    assert layers["layers"]
    assert catalog["name"] == "Test Stands"
    assert (out_dir / "artemis" / "extent.geojson").exists()
    assert (out_dir / "artemis" / "aoi.geojson").exists()


def test_main_requires_an_input():
    with pytest.raises(SystemExit, match="naip-manifest"):
        vc.main([])


def test_main_rejects_a_missing_manifest(tmp_path):
    with pytest.raises(SystemExit, match="Not found"):
        vc.main(["--naip-manifest", str(tmp_path / "absent.json")])
