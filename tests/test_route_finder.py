import os
import sys
from pathlib import Path

import pandas as pd
import pytest

# Add the project root to sys.path so tests can import route_finder.py directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import route_finder as rf


SAMPLE_CSV = os.path.join(os.path.dirname(__file__), "data", "sample_svi.csv")


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


def test_build_nodes_from_sample_csv():
    # Build nodes from a small local CSV to avoid network calls.
    nodes = rf.build_nodes(
        svi_csv=SAMPLE_CSV,
        state_name="Massachusetts",
        svi_weight=1.0,
        sleep_s=0.0,
        use_centroid_if_missing=False,
        cache_csv=None,
    )
    assert len(nodes) == 3
    assert all(n.parking_pts for n in nodes)
    assert {n.county for n in nodes} == {"Suffolk", "Essex", "Berkshire"}


def test_find_route_basic():
    # Minimal routing test using in-memory nodes and haversine distances.
    nodes = [
        rf.CountyNode(
            state="Massachusetts",
            county="Suffolk",
            fips="25025",
            svi=0.9,
            weighted_svi=0.9,
            parking_pts=[(42.3557, -71.0562)],
        ),
        rf.CountyNode(
            state="Massachusetts",
            county="Essex",
            fips="25009",
            svi=0.5,
            weighted_svi=0.5,
            parking_pts=[(42.5584, -70.8790)],
        ),
        rf.CountyNode(
            state="Massachusetts",
            county="Berkshire",
            fips="25003",
            svi=0.2,
            weighted_svi=0.2,
            parking_pts=[(42.3732, -73.3284)],
        ),
    ]

    df = rf.find_route(nodes=nodes, num_places=2, start_county=None, improve_2opt=False)

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert df["order"].tolist() == [1, 2]
    assert df["total_km"].iloc[-1] >= 0


@pytest.mark.skipif(rf.Flask is None, reason="Flask is not installed")
def test_api_route_haversine(monkeypatch):
    # Exercise Flask API with haversine distance mode and verify CORS headers.
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "http://localhost:5173")
    app = rf.create_app()
    client = app.test_client()

    health = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert health.status_code == 200
    assert health.headers.get("Access-Control-Allow-Origin") == "http://localhost:5173"

    payload = {
        "svi_csv": "tests/data/sample_svi.csv",
        "state": "Massachusetts",
        "num_places": 2,
        "distance_mode": "haversine",
        "parking_distance_mode": "haversine",
    }
    resp = client.post("/api/route", json=payload, headers={"Origin": "http://localhost:5173"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["summary"]["num_stops"] == 2
    assert len(data["stops"]) == 2
    assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost:5173"
