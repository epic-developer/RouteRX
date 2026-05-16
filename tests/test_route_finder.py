import os
import sys
from pathlib import Path

import pandas as pd
import pytest

# Add the project root to sys.path so tests can import route_finder.py directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import route_finder as rf


SAMPLE_SVI_CSV = os.path.join(os.path.dirname(__file__), "data", "sample_svi.csv")
SAMPLE_ZIP_CATALOG_CSV = os.path.join(os.path.dirname(__file__), "data", "sample_zip_catalog.csv")


def test_parse_latlon_list():
    # Handles empty/null input and parses semicolon-separated lat/lon pairs.
    assert rf.parse_latlon_list(None) == []
    assert rf.parse_latlon_list("") == []
    assert rf.parse_latlon_list("1,2; 3,4") == [(1.0, 2.0), (3.0, 4.0)]


def test_representative_point():
    # Representative point should be one of the provided points.
    pts = [(0.0, 0.0), (0.0, 2.0), (2.0, 0.0)]
    rep = rf.representative_point(pts)
    assert rep in pts


def test_build_zip_nodes_from_sample_csv():
    # Build ZIP nodes from local fixture data so the test stays offline and deterministic.
    nodes = rf.build_zip_nodes(
        svi_csv=SAMPLE_SVI_CSV,
        zip_catalog_csv=SAMPLE_ZIP_CATALOG_CSV,
        state_name="Massachusetts",
        county_name="Suffolk",
        svi_weight=1.0,
        sleep_s=0.0,
        use_centroid_if_missing=False,
        cache_csv=None,
    )
    assert len(nodes) == 4
    assert {node.zip_code for node in nodes} == {"02108", "02109", "02110", "02111"}
    assert all(node.parking_pts for node in nodes)
    assert all(node.county == "Suffolk" for node in nodes)
    score_by_zip = {node.zip_code: node.svi for node in nodes}
    assert score_by_zip["02111"] > score_by_zip["02110"] > score_by_zip["02109"] > score_by_zip["02108"]


def test_find_route_basic():
    # The selector should choose the highest-vulnerability ZIPs, then order them by distance.
    nodes = [
        rf.ZipNode(
            state="Massachusetts",
            state_abbr="MA",
            county="Suffolk",
            zip_code="02111",
            svi=0.9,
            weighted_svi=0.9,
            hospitals=0,
            nursing_homes=0,
            public_health_departments=0,
            pharmacies=1,
            resource_sites=1,
            representative_pt=(42.3505, -71.0596),
            parking_pts=[(42.3506, -71.0597)],
        ),
        rf.ZipNode(
            state="Massachusetts",
            state_abbr="MA",
            county="Suffolk",
            zip_code="02110",
            svi=0.5,
            weighted_svi=0.5,
            hospitals=0,
            nursing_homes=0,
            public_health_departments=1,
            pharmacies=1,
            resource_sites=2,
            representative_pt=(42.3588, -71.0518),
            parking_pts=[(42.3589, -71.0519)],
        ),
        rf.ZipNode(
            state="Massachusetts",
            state_abbr="MA",
            county="Suffolk",
            zip_code="02108",
            svi=0.2,
            weighted_svi=0.2,
            hospitals=1,
            nursing_homes=0,
            public_health_departments=1,
            pharmacies=3,
            resource_sites=5,
            representative_pt=(42.3570, -71.0637),
            parking_pts=[(42.3571, -71.0638)],
        ),
    ]

    df = rf.find_route(
        nodes=nodes,
        num_places=2,
        improve_2opt=False,
        use_clustering=False,
        distance_provider=rf.HaversineDistanceProvider(),
        parking_distance_provider=rf.HaversineDistanceProvider(),
    )

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert df["order"].tolist() == [1, 2]
    assert df["zip_code"].tolist() == ["02111", "02110"]
    assert df["total_km"].iloc[-1] >= 0


def test_svi_weight_scales_vulnerability_scores():
    nodes = [
        rf.ZipNode(
            state="Massachusetts",
            state_abbr="MA",
            county="Suffolk",
            zip_code="00001",
            svi=0.25,
            weighted_svi=0.25,
            hospitals=1,
            nursing_homes=1,
            public_health_departments=1,
            pharmacies=1,
            resource_sites=4,
            representative_pt=(42.0, -71.0),
            parking_pts=[(42.0, -71.0)],
        ),
        rf.ZipNode(
            state="Massachusetts",
            state_abbr="MA",
            county="Suffolk",
            zip_code="00002",
            svi=0.75,
            weighted_svi=1.50,
            hospitals=0,
            nursing_homes=0,
            public_health_departments=0,
            pharmacies=1,
            resource_sites=1,
            representative_pt=(42.01, -71.0),
            parking_pts=[(42.01, -71.0)],
        ),
    ]

    df = rf.find_route(
        nodes=nodes,
        num_places=1,
        improve_2opt=False,
        use_clustering=False,
        distance_provider=rf.HaversineDistanceProvider(),
        parking_distance_provider=rf.HaversineDistanceProvider(),
    )

    assert df["zip_code"].tolist() == ["00002"]


def test_clustering_selects_top_zip_in_each_region():
    nodes = [
        rf.ZipNode(
            state="Massachusetts",
            state_abbr="MA",
            county="Suffolk",
            zip_code="A1",
            svi=0.95,
            weighted_svi=0.95,
            hospitals=0,
            nursing_homes=0,
            public_health_departments=0,
            pharmacies=0,
            resource_sites=0,
            representative_pt=(42.0000, -71.0000),
            parking_pts=[(42.0000, -71.0000)],
        ),
        rf.ZipNode(
            state="Massachusetts",
            state_abbr="MA",
            county="Suffolk",
            zip_code="A2",
            svi=0.90,
            weighted_svi=0.90,
            hospitals=0,
            nursing_homes=0,
            public_health_departments=0,
            pharmacies=1,
            resource_sites=1,
            representative_pt=(42.0005, -71.0000),
            parking_pts=[(42.0005, -71.0000)],
        ),
        rf.ZipNode(
            state="Massachusetts",
            state_abbr="MA",
            county="Suffolk",
            zip_code="B1",
            svi=0.70,
            weighted_svi=0.70,
            hospitals=1,
            nursing_homes=0,
            public_health_departments=0,
            pharmacies=1,
            resource_sites=2,
            representative_pt=(42.1000, -71.0000),
            parking_pts=[(42.1000, -71.0000)],
        ),
        rf.ZipNode(
            state="Massachusetts",
            state_abbr="MA",
            county="Suffolk",
            zip_code="B2",
            svi=0.60,
            weighted_svi=0.60,
            hospitals=1,
            nursing_homes=1,
            public_health_departments=0,
            pharmacies=1,
            resource_sites=3,
            representative_pt=(42.1005, -71.0000),
            parking_pts=[(42.1005, -71.0000)],
        ),
    ]

    unclustered = rf.find_route(
        nodes=nodes,
        num_places=2,
        improve_2opt=False,
        use_clustering=False,
        distance_provider=rf.HaversineDistanceProvider(),
        parking_distance_provider=rf.HaversineDistanceProvider(),
    )
    clustered = rf.find_route(
        nodes=nodes,
        num_places=2,
        improve_2opt=False,
        use_clustering=True,
        distance_provider=rf.HaversineDistanceProvider(),
        parking_distance_provider=rf.HaversineDistanceProvider(),
    )

    assert set(unclustered["zip_code"]) == {"A1", "A2"}
    assert set(clustered["zip_code"]) == {"A1", "B1"}


@pytest.mark.skipif(rf.Flask is None, reason="Flask is not installed")
def test_api_route_haversine(monkeypatch):
    # Exercise the Flask API with local ZIP fixtures and verify CORS headers.
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "http://localhost:5173")
    app = rf.create_app()
    client = app.test_client()

    health = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert health.status_code == 200
    assert health.headers.get("Access-Control-Allow-Origin") == "http://localhost:5173"

    payload = {
        "svi_csv": "tests/data/sample_svi.csv",
        "zip_catalog_csv": "tests/data/sample_zip_catalog.csv",
        "state": "Massachusetts",
        "county": "Suffolk",
        "num_places": 2,
        "distance_mode": "haversine",
        "parking_distance_mode": "haversine",
    }
    resp = client.post("/api/route", json=payload, headers={"Origin": "http://localhost:5173"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["summary"]["num_stops"] == 2
    assert len(data["stops"]) == 2
    assert data["stops"][0]["zip_code"] == "02111"
    assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost:5173"

    csv_resp = client.post("/api/route.csv", json=payload)
    assert csv_resp.status_code == 200
    assert "text/csv" in (csv_resp.headers.get("Content-Type") or "")
    assert "order,zip_code,county,state" in csv_resp.get_data(as_text=True)

    preview_resp = client.post("/api/route/preview", json=payload)
    assert preview_resp.status_code == 200
    preview = preview_resp.get_json()
    assert preview["summary"]["num_stops"] == 2


@pytest.mark.skipif(rf.folium is None, reason="folium is not installed")
def test_api_route_map(monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "http://localhost:5173")
    app = rf.create_app()
    client = app.test_client()

    payload = {
        "svi_csv": "tests/data/sample_svi.csv",
        "zip_catalog_csv": "tests/data/sample_zip_catalog.csv",
        "state": "Massachusetts",
        "county": "Suffolk",
        "num_places": 2,
        "distance_mode": "haversine",
        "parking_distance_mode": "haversine",
    }
    resp = client.post("/api/route/map", json=payload)
    assert resp.status_code == 200
    assert "text/html" in (resp.headers.get("Content-Type") or "")
