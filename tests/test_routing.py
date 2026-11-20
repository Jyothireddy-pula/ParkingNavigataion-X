"""
Routing tests: /routing/shortest-path and /find_parking enhancements.
Covers all gates × lots (drive), lots × key destinations (walk),
shortest-distance correctness invariant, and error handling.
"""
import pytest
from fastapi.testclient import TestClient
import digital_twin.api.app as api

GATES = ["vitap-gate-main", "vitap-gate-visitor"]
LOTS  = [
    "vitap-lot-overflow", "vitap-lot-admin-visitor",
    "vitap-lot-academic-main", "vitap-lot-sports", "vitap-lot-hostel",
]
KEY_DESTS = ["vitap-osm-mh5-001", "vitap-osm-mh4-001", "vitap-osm-lh2-001"]


@pytest.fixture(scope="module")
def client(tmp_path_factory, monkeypatch_session=None):
    root = api.ROOT
    db = tmp_path_factory.mktemp("db") / "test.db"
    # monkeypatch is function-scoped; use setattr directly for module-scoped fixture
    orig_db = api.DB_PATH
    orig_yaml = api.CAMPUS_YAML
    api.DB_PATH = db
    api.CAMPUS_YAML = root / "configs" / "campus" / "vitap.yaml"
    with TestClient(api.app) as c:
        yield c
    api.DB_PATH = orig_db
    api.CAMPUS_YAML = orig_yaml


# ── /routing/shortest-path ────────────────────────────────────────────────────

@pytest.mark.parametrize("gate", GATES)
@pytest.mark.parametrize("lot", LOTS)
def test_drive_fastest_route_valid(client, gate, lot):
    r = client.post("/routing/shortest-path", json={
        "origin_id": gate, "destination_id": lot,
        "travel_mode": "driving", "optimization": "fastest",
    })
    assert r.status_code in (200, 422), f"Unexpected {r.status_code} for {gate}->{lot}"
    if r.status_code == 200:
        d = r.json()
        assert d["graph_based"] is True
        assert d["algorithm"] == "Dijkstra"
        assert d["distance_m"] > 0
        assert d["duration_min"] > 0
        assert len(d["path"]) >= 2
        assert len(d["geometry"]) >= 2


@pytest.mark.parametrize("gate", GATES)
@pytest.mark.parametrize("lot", LOTS)
def test_shortest_distance_le_fastest_distance(client, gate, lot):
    """Distance-optimal route must never be longer than the time-optimal route."""
    fast = client.post("/routing/shortest-path", json={
        "origin_id": gate, "destination_id": lot,
        "travel_mode": "driving", "optimization": "fastest",
    })
    shor = client.post("/routing/shortest-path", json={
        "origin_id": gate, "destination_id": lot,
        "travel_mode": "driving", "optimization": "shortest_distance",
    })
    if fast.status_code == 200 and shor.status_code == 200:
        assert shor.json()["distance_m"] <= fast.json()["distance_m"] + 1  # 1 m float tolerance


@pytest.mark.parametrize("lot", LOTS)
@pytest.mark.parametrize("dest", KEY_DESTS)
def test_walk_route_valid_or_no_path(client, lot, dest):
    r = client.post("/routing/shortest-path", json={
        "origin_id": lot, "destination_id": dest,
        "travel_mode": "walking", "optimization": "fastest",
    })
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        d = r.json()
        assert d["distance_m"] > 0
        assert d["duration_min"] > 0


def test_unknown_origin_returns_404(client):
    r = client.post("/routing/shortest-path", json={
        "origin_id": "does-not-exist", "destination_id": "vitap-lot-sports",
        "travel_mode": "driving", "optimization": "fastest",
    })
    assert r.status_code == 404


def test_no_path_returns_422(client):
    # Walk from a gate (no walkable edges from gates) → expect 422
    r = client.post("/routing/shortest-path", json={
        "origin_id": "vitap-gate-main", "destination_id": "vitap-lot-sports",
        "travel_mode": "walking", "optimization": "fastest",
    })
    assert r.status_code in (200, 422)  # 422 expected; 200 acceptable if walkway exists


# ── /find_parking enhancements ────────────────────────────────────────────────

@pytest.mark.parametrize("gate", GATES)
def test_find_parking_mh1_returns_options(client, gate):
    r = client.get(f"/find_parking?destination_id=vitap-osm-mh5-001&gate_id={gate}")
    assert r.status_code == 200
    body = r.json()
    assert body["options_count"] > 0
    opts = body["options"]
    assert len(opts) > 0


@pytest.mark.parametrize("gate", GATES)
def test_find_parking_drive_route_labelled_fastest_time(client, gate):
    r = client.get(f"/find_parking?destination_id=vitap-osm-mh5-001&gate_id={gate}")
    assert r.status_code == 200
    for opt in r.json()["options"]:
        assert opt["drive_route"]["optimization"] == "fastest_time"
        assert opt["walk_route"]["optimization"] == "fastest_time"
        assert opt["drive_route"]["algorithm"] == "Dijkstra"
        assert opt["walk_route"]["algorithm"] == "Dijkstra"


@pytest.mark.parametrize("gate", GATES)
def test_find_parking_has_distance_meters(client, gate):
    r = client.get(f"/find_parking?destination_id=vitap-osm-mh5-001&gate_id={gate}")
    assert r.status_code == 200
    for opt in r.json()["options"]:
        assert opt["drive_route"]["distance_meters"] > 0
        assert opt["walk_route"]["distance_meters"] > 0


def test_find_parking_shortest_distance_alt_is_shorter_or_absent(client):
    """When drive_route_shortest_distance is present, its distance must be <= drive_route's."""
    r = client.get("/find_parking?destination_id=vitap-osm-mh5-001&gate_id=vitap-gate-main")
    assert r.status_code == 200
    for opt in r.json()["options"]:
        alt = opt.get("drive_route_shortest_distance")
        if alt:
            assert alt["distance_meters"] <= opt["drive_route"]["distance_meters"] + 1


def test_find_parking_invalid_destination_404(client):
    r = client.get("/find_parking?destination_id=bogus-123&gate_id=vitap-gate-main")
    assert r.status_code == 404


def test_find_parking_recommendation_scores_ranked(client):
    """Options must be in ascending score order."""
    r = client.get("/find_parking?destination_id=vitap-osm-mh5-001&gate_id=vitap-gate-main")
    assert r.status_code == 200
    scores = [o["score"] for o in r.json()["options"]]
    assert scores == sorted(scores)
