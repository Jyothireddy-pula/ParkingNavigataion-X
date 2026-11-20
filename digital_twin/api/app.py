"""
ParkingNav-X REST API
VIT-AP University Smart Parking Digital Twin
"""
import hmac
import json
import math
import os
import re
import statistics
import threading
import uuid
from pathlib import Path
from typing import List, Optional

import networkx as nx
from fastapi import Depends, FastAPI, Header, HTTPException, Path as PathParameter, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel

from digital_twin.config_loader import load_campus_config
from digital_twin.db import apply_migrations, get_connection, db_backend
from digital_twin.graph_service import CampusGraphService
from digital_twin.simulation.demo_strategies import ParkingNavXFullStrategy, PredictionOnlyStrategy
from digital_twin.simulation.engine import SimulationEngine
from digital_twin.simulation.scenario import ScenarioLoader
from digital_twin.simulation.strategy import FirstAvailableStrategy, NearestAvailableStrategy

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "digital_twin.db"
CAMPUS_YAML = ROOT / "configs" / "campus" / "vitap.yaml"
SCENARIO_DIR = ROOT / "configs" / "scenarios" / "vitap"
RESULTS_FILE = ROOT / "experiments" / "results" / "all_results.json"
SUMMARY_FILE = ROOT / "experiments" / "results" / "results_summary.json"

_DESCRIPTION = """
## VIT-AP University Smart Parking Digital Twin

**ParkingNav-X** is a live digital twin of the VIT-AP campus parking system.
Use the **Try it out** button on any endpoint to send a real request.

### Quick-start values for Try it out

| Field | Example value |
|---|---|
| `scenario` | `E1_normal_day` |
| `strategy` | `P` |
| `gate_id` | `vitap-gate-main` |
| `destination_id` | `vitap-osm-mh5-001` |
| `lot_id` | `vitap-lot-hostel` |
| `origin_id` | `vitap-gate-main` |
| `destination_id` | `vitap-lot-hostel` |

### Tag groups
- **Info** — health check and API metadata
- **Campus** — gates, parking lots, roads, destinations, observations
- **Simulation** — run & compare parking strategies
- **Routing** — point-to-point routes and find-parking recommendations
- **Prediction** — ML occupancy forecasts
- **Risk** — overflow risk assessment
- **Optimization** — gate/lot assignment recommendations
- **Experiments** — stored multi-seed experiment results
"""

app = FastAPI(
    title="ParkingNav-X API",
    description=_DESCRIPTION,
    version="2.0.0",
    openapi_tags=[
        {"name": "Info",         "description": "Health check and API metadata"},
        {"name": "Campus",       "description": "Gates, parking lots, roads, destinations and live observations"},
        {"name": "Operator",     "description": "Real-time lot overrides (close / reopen / capacity change)"},
        {"name": "Simulation",   "description": "Run parking simulations and compare strategies B1–B3 vs P"},
        {"name": "Routing",      "description": "Campus routing: point-to-point paths and smart parking finder"},
        {"name": "Prediction",   "description": "ML-based occupancy forecasts per lot"},
        {"name": "Risk",         "description": "Overflow risk scores and forecasts"},
        {"name": "Optimization", "description": "Gate assignment recommendations with cost breakdown"},
        {"name": "Experiments",  "description": "Stored multi-seed experiment results (E1–E6)"},
    ],
)


def _swagger_choice_values():
    """Return live values used to populate Swagger's enum dropdowns."""
    choices = {
        "scenario": sorted(path.stem for path in SCENARIO_DIR.glob("*.yaml")),
        "strategy": ["B1", "B2", "B3", "P"],
        "strategies": ["B1", "B2", "B3", "P"],
        "weight_profile": ["P1", "P2", "P3"],
        "mode": ["drive", "walk"],
        "travel_mode": ["driving", "walking"],
        "optimization": ["fastest", "shortest_distance"],
        "action": ["close", "open", "cap_override"],
        "source": ["sensor", "manual", "synthetic"],
        "horizon_minutes": [5, 15, 30, 60],
        "horizon": [5, 15, 30, 60],
    }
    try:
        conn = get_conn()
        choices["lot_id"] = [row[0] for row in conn.execute(
            "SELECT parking_lot_id FROM parking_lots WHERE campus_id='vitap' ORDER BY parking_lot_id"
        ).fetchall()]
        choices["gate_id"] = [row[0] for row in conn.execute(
            "SELECT gate_id FROM gates WHERE campus_id='vitap' ORDER BY gate_id"
        ).fetchall()]
        choices["destination_id"] = [row[0] for row in conn.execute(
            "SELECT destination_id FROM destinations WHERE campus_id='vitap' ORDER BY destination_id"
        ).fetchall()]
        nodes = conn.execute(
            "SELECT gate_id FROM gates WHERE campus_id='vitap' UNION "
            "SELECT parking_lot_id FROM parking_lots WHERE campus_id='vitap' UNION "
            "SELECT destination_id FROM destinations WHERE campus_id='vitap' ORDER BY 1"
        ).fetchall()
        choices["origin"] = [row[0] for row in nodes]
        choices["destination"] = [row[0] for row in nodes]
        choices["origin_id"] = [row[0] for row in nodes]
    except Exception:
        # Keep the docs usable during initial DB setup; choices will be refreshed
        # on the next process start after the campus data is available.
        choices.setdefault("lot_id", ["vitap-lot-hostel", "vitap-lot-academic-main"])
        choices.setdefault("gate_id", ["vitap-gate-main", "vitap-gate-visitor"])
        choices.setdefault("destination_id", ["vitap-osm-mh5-001", "vitap-lib-001", "vitap-gym-001"])
        nodes = ["vitap-gate-main", "vitap-gate-visitor", "vitap-lot-hostel", "vitap-lot-academic-main"]
        choices.setdefault("origin", nodes)
        choices.setdefault("destination", nodes)
        choices.setdefault("origin_id", nodes)
    return {key: value for key, value in choices.items() if value}


def _custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
    )
    choices = _swagger_choice_values()

    def annotate(field_name, field_schema):
        values = choices.get(field_name)
        if values:
            target_schema = field_schema
            if field_schema.get("type") == "array":
                target_schema = field_schema.setdefault("items", {})
            target_schema["enum"] = values
            field_schema.setdefault("example", values[0] if target_schema is field_schema else [values[0]])
        elif field_name == "as_of":
            field_schema.setdefault("example", "2026-11-19T09:30:00")
            field_schema.setdefault("description", "Optional ISO-8601 timestamp; omit to use the current time")
        elif field_name == "timestamp":
            field_schema.setdefault("format", "date-time")
            field_schema.setdefault("example", "2026-11-19T09:30:00")
        elif field_name == "reason":
            field_schema.setdefault("example", "Maintenance")
        elif field_name == "x-api-key":
            field_schema.setdefault("example", "your-api-key")
            field_schema.setdefault("description", "Required only when PARKINGNAV_API_KEY is configured")
        elif field_name == "override_capacity":
            field_schema.setdefault("example", 80)
            field_schema.setdefault("minimum", 1)
        elif field_name == "occupied_spaces":
            field_schema.setdefault("example", 45)
            field_schema.setdefault("minimum", 0)
        elif field_name in {"vehicle_count", "limit"}:
            field_schema.setdefault("example", 48 if field_name == "vehicle_count" else 200)
            field_schema.setdefault("minimum", 0 if field_name == "vehicle_count" else 1)

    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            for parameter in operation.get("parameters", []):
                annotate(parameter.get("name"), parameter.setdefault("schema", {}))

    for model in schema.get("components", {}).get("schemas", {}).values():
        for name, field_schema in model.get("properties", {}).items():
            annotate(name, field_schema)

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = _custom_openapi

app.add_middleware(
    CORSMiddleware,
    # PARKINGNAV_ALLOWED_ORIGINS: comma-separated origins for a live deployment; unset = any origin (dev).
    allow_origins=[o.strip() for o in os.environ.get("PARKINGNAV_ALLOWED_ORIGINS", "*").split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

def require_write_key(x_api_key: Optional[str] = Header(default=None)):
    """Guard for endpoints that change data. If PARKINGNAV_API_KEY is set (live deployment), requests must send
    it as an X-API-Key header; if unset (local dev, tests) the endpoints stay open."""
    key = os.environ.get("PARKINGNAV_API_KEY")
    if key and not hmac.compare_digest(x_api_key or "", key):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")


STRATEGIES = {
    "B1": FirstAvailableStrategy(),
    "B2": NearestAvailableStrategy(),
    "B3": PredictionOnlyStrategy(),
    "P":  ParkingNavXFullStrategy(),
}

_graph_service = CampusGraphService()

# ── Cached DB connection (migrations run once at startup, not per-request) ────
_db_conn = None


def get_conn():
    global _db_conn
    if _db_conn is None:
        _db_conn = get_connection(DB_PATH)
        apply_migrations(_db_conn)
        load_campus_config(CAMPUS_YAML, _db_conn)
    return _db_conn


@app.on_event("startup")
def _startup_prewarm():
    """Pre-warm the prediction engine so the first API request is not slow."""
    try:
        from digital_twin.prediction.engine import retrain
        retrain(get_conn())  # fits all models once (real data where available); cached as singleton
    except Exception:
        pass  # non-fatal — predictions will fall back to MovingAverage


def _build_graph(conn):
    return _graph_service.build_graph("vitap", conn)


def _provenance(source, accuracy_m):
    """How a coordinate was obtained. exact == taken from an OpenStreetMap way."""
    return {
        "coordinate_source": source,
        "accuracy_m": accuracy_m,
        "exact": bool(source and source.startswith(("osm_way", "osm_relation"))),
    }


def _parse_boundary(raw):
    """Stored boundary_polygon (JSON string of lat-first POLYGON/MULTIPOLYGON text)
    -> list of rings, each a list of {"lat", "lng"}."""
    if not raw:
        return []
    text = json.loads(raw) if raw.startswith('"') else raw
    rings = []
    for ring in re.findall(r"\(([^()]+)\)", text):
        pts = []
        for pair in ring.split(","):
            lat, lng = pair.split()
            pts.append({"lat": float(lat), "lng": float(lng)})
        rings.append(pts)
    return rings


def _route_geometry(graph, path):
    """Polyline for a routed path, as [{"lat", "lng"}, ...].

    Follows each traversed road's own geometry (the same polyline /campus draws),
    so a highlighted route lies on the roads instead of cutting straight between
    node centres. Falls back to node coordinates where a road has no geometry.
    """
    points = []

    def _add(lat, lng):
        if lat is None or lng is None:
            return
        pt = {"lat": lat, "lng": lng}
        if not points or points[-1] != pt:
            points.append(pt)

    for i, node_id in enumerate(path):
        if i > 0:
            edge = graph.edges[path[i - 1], node_id]
            for p in edge.get("geometry") or []:
                _add(p["lat"], p["lng"])
        nd = graph.nodes[node_id]
        _add(nd.get("latitude"), nd.get("longitude"))
    return points


# ── Models ────────────────────────────────────────────────────────────────────

class SimulateRequest(BaseModel):
    scenario: str = "E1_normal_day"
    strategy: str = "P"

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"scenario": "E1_normal_day", "strategy": "P"},
                {"scenario": "E2_event_placement", "strategy": "B2"},
                {"scenario": "E4_gate_closure", "strategy": "B3"},
            ]
        }
    }


class PredictRequest(BaseModel):
    lot_id: str
    horizon_minutes: int = 15

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"lot_id": "vitap-lot-hostel", "horizon_minutes": 15},
                {"lot_id": "vitap-lot-academic-main", "horizon_minutes": 30},
            ]
        }
    }


class OptimizeRequest(BaseModel):
    scenario: str = "E1_normal_day"
    weight_profile: str = "P1"

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"scenario": "E1_normal_day", "weight_profile": "P1"},
                {"scenario": "E2_event_placement", "weight_profile": "P2"},
            ]
        }
    }


class RouteRequest(BaseModel):
    origin: str
    destination: str
    mode: str = "drive"  # drive | walk

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"origin": "vitap-gate-main", "destination": "vitap-lot-hostel", "mode": "drive"},
                {"origin": "vitap-lot-hostel", "destination": "vitap-osm-mh5-001", "mode": "walk"},
            ]
        }
    }


class OperatorOverrideRequest(BaseModel):
    lot_id: str
    action: str          # "close" | "open" | "cap_override"
    reason: str = ""
    override_capacity: Optional[int] = None  # used with action="cap_override"

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"lot_id": "vitap-lot-sports", "action": "close", "reason": "Maintenance"},
                {"lot_id": "vitap-lot-sports", "action": "open",  "reason": "Maintenance done"},
                {"lot_id": "vitap-lot-overflow", "action": "cap_override", "override_capacity": 80, "reason": "Reserved for event"},
            ]
        }
    }


class ObservationRecord(BaseModel):
    timestamp: str           # ISO-8601
    lot_id: str
    occupied_spaces: int
    gate_id: Optional[str] = None
    vehicle_count: Optional[int] = None
    source: str = "sensor"   # sensor | manual | synthetic

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "timestamp": "2024-11-16T09:30:00",
                    "lot_id": "vitap-lot-hostel",
                    "occupied_spaces": 45,
                    "gate_id": "vitap-gate-main",
                    "vehicle_count": 48,
                    "source": "sensor",
                },
                {
                    "timestamp": "2024-11-16T10:00:00",
                    "lot_id": "vitap-lot-academic-main",
                    "occupied_spaces": 120,
                    "source": "manual",
                },
            ]
        }
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get(
    "/health",
    tags=["Info"],
    summary="Health check",
    response_description="API status and DB reachability",
)
def health():
    """Liveness check — returns `200 ok` when the API is up and the DB is reachable.

    Try it out: click **Try it out → Execute**. No parameters needed.
    """
    try:
        conn = get_conn()
        conn.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
        "db_backend": db_backend(),
        "version": "2.0.0",
    }


@app.get("/", tags=["Info"])
def root():
    return {
        "project": "ParkingNav-X",
        "campus": "VIT-AP University, Amaravati, Andhra Pradesh",
        "version": "2.0.0",
        "docs": "/docs",
    }


@app.get(
    "/campus",
    tags=["Campus"],
    summary="Get campus layout",
    response_description="Gates, parking lots, roads and destinations",
)
def get_campus():
    """Return the full VIT-AP campus layout.

    Returns all **gates**, **parking lots** (with boundary polygons), **roads** (with geometry),
    and **destinations** (hostels, labs, library, gym, …).

    Try it out: click **Try it out → Execute**. No parameters needed.
    """
    conn = get_conn()
    gates = conn.execute(
        "SELECT gate_id, name, latitude, longitude, capacity, status, coordinate_source, coordinate_accuracy_m "
        "FROM gates WHERE campus_id='vitap'"
    ).fetchall()
    lots = conn.execute(
        "SELECT parking_lot_id, name, latitude, longitude, total_capacity, usable_capacity, status, "
        "coordinate_source, coordinate_accuracy_m, boundary_polygon, boundary_source "
        "FROM parking_lots WHERE campus_id='vitap'"
    ).fetchall()
    roads = conn.execute(
        "SELECT road_id, name, start_node_id, end_node_id, length_meters, expected_travel_time_seconds, is_walkable, is_driveable, status, geometry, geometry_source, access_start_m, access_end_m FROM roads WHERE campus_id='vitap'"
    ).fetchall()
    dests = conn.execute(
        "SELECT destination_id, name, category, latitude, longitude, nearest_parking_lot_ids, "
        "coordinate_source, coordinate_accuracy_m FROM destinations WHERE campus_id='vitap'"
    ).fetchall()

    return {
        "campus_id": "vitap",
        "name": "VIT-AP University",
        "location": "Amaravati, Andhra Pradesh, India",
        "gates": [
            {"id": r[0], "name": r[1], "lat": r[2], "lng": r[3], "capacity": r[4], "status": r[5],
             **_provenance(r[6], r[7])}
            for r in gates
        ],
        "parking_lots": [
            {"id": r[0], "name": r[1], "lat": r[2], "lng": r[3],
             "total_capacity": r[4], "usable_capacity": r[5], "status": r[6],
             **_provenance(r[7], r[8]),
             "boundary_source": r[10],
             # Only a real footprint is exposed as a boundary; a "placeholder" box is not drawn as one.
             "boundary": _parse_boundary(r[9]) if r[10] and r[10].startswith("osm_way") else []}
            for r in lots
        ],
        "roads": [
            {
                "id": r[0], "name": r[1],
                "start_node_id": r[2], "end_node_id": r[3],
                "length_meters": r[4], "travel_time_seconds": r[5],
                "is_walkable": bool(r[6]), "is_driveable": bool(r[7]),
                "status": r[8],
                "geometry": json.loads(r[9]) if r[9] else [],
                "geometry_source": r[10],
                # metres at each end that are not on a mapped road (drawn dotted by the UI)
                "access_start_m": r[11],
                "access_end_m": r[12],
            }
            for r in roads
        ],
        "destinations": [
            {
                "id": r[0], "name": r[1], "category": r[2],
                "lat": r[3], "lng": r[4],
                "nearest_lots": json.loads(r[5]) if r[5] else [],
                **_provenance(r[6], r[7]),
            }
            for r in dests
        ],
    }


@app.post("/operator/override", tags=["Operator"])
def operator_override(req: OperatorOverrideRequest):
    """
    Operator override: close/reopen a lot or change its usable capacity.
    Changes take effect immediately in the live Digital Twin state.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT usable_capacity, status FROM parking_lots WHERE campus_id='vitap' AND parking_lot_id=?",
        (req.lot_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Lot '{req.lot_id}' not found")

    allowed_actions = {"close", "open", "cap_override"}
    if req.action not in allowed_actions:
        raise HTTPException(status_code=400, detail=f"action must be one of {allowed_actions}")

    if req.action == "close":
        conn.execute(
            "UPDATE parking_lots SET status='closed' WHERE campus_id='vitap' AND parking_lot_id=?",
            (req.lot_id,)
        )
        new_status, new_cap = "closed", row[0]
    elif req.action == "open":
        conn.execute(
            "UPDATE parking_lots SET status='open' WHERE campus_id='vitap' AND parking_lot_id=?",
            (req.lot_id,)
        )
        new_status, new_cap = "open", row[0]
    else:  # cap_override
        if req.override_capacity is None or req.override_capacity <= 0:
            raise HTTPException(status_code=400, detail="override_capacity must be a positive integer")
        conn.execute(
            "UPDATE parking_lots SET usable_capacity=? WHERE campus_id='vitap' AND parking_lot_id=?",
            (req.override_capacity, req.lot_id)
        )
        new_status, new_cap = row[1], req.override_capacity

    conn.commit()
    return {
        "lot_id": req.lot_id,
        "action": req.action,
        "new_status": new_status,
        "new_usable_capacity": new_cap,
        "reason": req.reason,
        "note": "Override applied to live DB. Run /simulate to propagate into next simulation.",
    }


@app.post("/observations", tags=["Campus"], dependencies=[Depends(require_write_key)])
def post_observation(obs: ObservationRecord):
    """
    Record one occupancy reading. source=sensor|manual counts as real data (live forecasts, retraining);
    source=synthetic is stored but never treated as real. A naive timestamp is campus-local (IST).
    """
    from digital_twin.observations import record_observations
    res = record_observations(get_conn(), [obs.model_dump()])
    if res["rejected"]:
        err = res["rejected"][0]["error"]
        raise HTTPException(status_code=404 if err.startswith("unknown lot") else 400, detail=err)
    return {**res, "lot_id": obs.lot_id, "occupied_spaces": obs.occupied_spaces,
            "timestamp": obs.timestamp, "source": obs.source}


@app.post("/observations/batch", tags=["Campus"], dependencies=[Depends(require_write_key)])
def post_observations_batch(batch: List[ObservationRecord]):
    """Record many readings at once. Bad rows are reported, good rows are still stored; duplicates are skipped."""
    from digital_twin.observations import record_observations
    return record_observations(get_conn(), [o.model_dump() for o in batch])


@app.get("/campus/graph", tags=["Campus"])
def get_campus_graph():
    """Return full campus graph (nodes + edges) for routing visualization."""
    conn = get_conn()
    graph = _build_graph(conn)
    return _graph_service.to_json(graph)


@app.get(
    "/scenarios",
    tags=["Simulation"],
    summary="List available scenarios",
    response_description="Scenario IDs to use in /simulate and /compare",
)
def list_scenarios():
    """List all simulation scenarios.

    Use these IDs in `/simulate` → `scenario` and `/compare/{scenario}`.

    **Available scenarios:**
    - `E1_normal_day` — typical weekday
    - `E2_event_placement` — placement season (high demand)
    - `E4_gate_closure` — one gate closed
    - and more...

    Try it out: click **Try it out → Execute**. No parameters needed.
    """
    files = sorted(SCENARIO_DIR.glob("*.yaml"))
    return {"scenarios": [f.stem for f in files]}


@app.get(
    "/strategies",
    tags=["Simulation"],
    summary="List parking strategies",
    response_description="Strategy codes and descriptions",
)
def list_strategies():
    """List all parking allocation strategies.

    | Code | Name | Description |
    |------|------|-------------|
    | `B1` | First Available | Naive — first open lot |
    | `B2` | Nearest Available | Closest open lot by distance |
    | `B3` | Prediction Only | Avoids nearly-full lots |
    | `P` | ParkingNav-X | Full system (prediction + routing + load balance) |

    Try it out: click **Try it out → Execute**. No parameters needed.
    """
    return {
        "strategies": [
            {"code": "B1", "name": "First Available",     "description": "Naive — assigns first open lot found"},
            {"code": "B2", "name": "Nearest Available",   "description": "Picks the closest open lot by distance"},
            {"code": "B3", "name": "Prediction Only",     "description": "Avoids nearly-full lots using fill-rate forecast"},
            {"code": "P",  "name": "ParkingNav-X (full)", "description": "Prediction + gate load balancing + zone routing"},
        ]
    }


@app.post(
    "/simulate",
    tags=["Simulation"],
    summary="Run a parking simulation",
    response_description="Simulation metrics: overflow, search time, queue stats",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "ParkingNav-X on a normal day": {
                            "summary": "Recommended starter request",
                            "value": {"scenario": "E1_normal_day", "strategy": "P"},
                        },
                        "Nearest lot baseline": {
                            "value": {"scenario": "E2_event_placement", "strategy": "B2"},
                        },
                    }
                }
            }
        }
    },
)
def simulate(req: SimulateRequest):
    """Run a single parking simulation and return metrics.

    **Try these combinations:**
    - `scenario=E1_normal_day` + `strategy=P` (ParkingNav-X full system)
    - `scenario=E2_event_placement` + `strategy=B2` (baseline nearest)
    - `scenario=E4_gate_closure` + `strategy=B3` (prediction only)

    Returns overflow events, avg search time, avg wait time, gate queue stats.
    """
    scenario_file = SCENARIO_DIR / f"{req.scenario}.yaml"
    if not scenario_file.exists():
        available = [f.stem for f in SCENARIO_DIR.glob("*.yaml")]
        raise HTTPException(status_code=404, detail=f"Scenario '{req.scenario}' not found. Available: {available}")

    strategy = STRATEGIES.get(req.strategy)
    if not strategy:
        raise HTTPException(status_code=400, detail=f"Strategy must be one of: {list(STRATEGIES.keys())}")

    conn = get_conn()
    scenario = ScenarioLoader.load(scenario_file)
    engine = SimulationEngine()
    run_id = f"api-{uuid.uuid4().hex[:8]}"
    result = engine.run(scenario, strategy, conn, run_id=run_id)

    # Populate lot_occupancy_history so the risk engine sees real data
    if result.timesteps:
        last_ts = result.timesteps[-1]
        lot_ids = [k.replace("parking_occupancy_", "") for k in last_ts if k.startswith("parking_occupancy_")]
        for lot_id in lot_ids:
            occupied = last_ts.get(f"parking_occupancy_{lot_id}", 0)
            cap = last_ts.get(f"parking_capacity_{lot_id}", 1) or 1
            fill_rate = round(occupied / cap, 4)
            try:
                conn.execute(
                    "INSERT INTO lot_occupancy_history (lot_id, occupied_spaces, fill_rate, source) "
                    "VALUES (?, ?, ?, 'simulation')",
                    (lot_id, int(occupied), fill_rate),
                )
            except Exception:
                pass
        conn.commit()

    m = result.metrics
    return {
        "run_id": run_id,
        "scenario": req.scenario,
        "strategy": req.strategy,
        "campus": "vitap",
        "duration_min": scenario.duration_minutes,
        "vehicles": scenario.vehicle_count,
        "metrics": {
            "total_vehicles":       m.get("total_vehicles"),
            "parked_vehicles":      m.get("parked_vehicles"),
            "completed_trips":      m.get("completed_vehicles"),
            "overflow_events":      m.get("overflow_events_count"),
            "avg_search_time_min":  round(m["avg_search_time_min"], 2) if m.get("avg_search_time_min") is not None else None,
            "avg_wait_time_min":    round(m["avg_wait_time_min"], 2)   if m.get("avg_wait_time_min")   is not None else None,
            "avg_gate_queue":       round(m["avg_gate_queue"], 2)      if m.get("avg_gate_queue")      is not None else None,
            "peak_gate_queue":      m.get("max_gate_queue"),
        },
    }


@app.get(
    "/compare/{scenario}",
    tags=["Simulation"],
    summary="Compare strategies now",
    description="Runs B1, B2, B3 and ParkingNav-X immediately. For a long-running comparison, use POST /compare/{scenario}/start, then poll GET /compare/{scenario}/status.",
)
def compare_strategies(scenario: str = PathParameter(..., description="Scenario ID; use GET /scenarios to list available scenarios")):
    """Run all 4 strategies on a scenario and return side-by-side comparison."""
    scenario_file = SCENARIO_DIR / f"{scenario}.yaml"
    if not scenario_file.exists():
        available = [f.stem for f in SCENARIO_DIR.glob("*.yaml")]
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario}' not found. Available: {available}")

    conn = get_conn()
    results = {}
    for code in ["B1", "B2", "B3", "P"]:
        strategy = STRATEGIES[code]
        sc = ScenarioLoader.load(scenario_file)
        engine = SimulationEngine()
        run_id = f"api-cmp-{uuid.uuid4().hex[:6]}"
        try:
            result = engine.run(sc, strategy, conn, run_id=run_id)
        except Exception as e:  # surface the real cause to the UI instead of a bare 500
            raise HTTPException(
                status_code=500,
                detail=f"Strategy {code} failed on '{scenario}': {type(e).__name__}: {e}",
            )
        m = result.metrics
        results[code] = {
            "overflow_events":     m.get("overflow_events_count"),
            "avg_search_time_min": round(m["avg_search_time_min"], 2) if m.get("avg_search_time_min") is not None else None,
            "avg_wait_time_min":   round(m["avg_wait_time_min"], 2)   if m.get("avg_wait_time_min")   is not None else None,
            "avg_gate_queue":      round(m["avg_gate_queue"], 2)      if m.get("avg_gate_queue")      is not None else None,
            "peak_gate_queue":     m.get("max_gate_queue"),
            "successful_park_rate": round(m["successful_park_rate"], 4) if m.get("successful_park_rate") is not None else None,
        }
    return {"scenario": scenario, "comparison": results}


# ── Async compare endpoints (used by the UI to avoid proxy timeout) ────────────
# Keyed by scenario name; only one concurrent comparison per scenario.
_compare_jobs: dict = {}  # scenario -> {"status": "running"|"done"|"error", "result": ..., "error": ...}


@app.post(
    "/compare/{scenario}/start",
    tags=["Simulation"],
    summary="Start a background comparison",
    description="Start here before polling GET /compare/{scenario}/status. After starting, poll the status endpoint until it returns done.",
)
def compare_start(
    scenario: str = PathParameter(..., description="Scenario ID; use GET /scenarios to see available scenarios")
):
    """Launch a background comparison job for the given scenario."""
    scenario_file = SCENARIO_DIR / f"{scenario}.yaml"
    if not scenario_file.exists():
        available = [f.stem for f in SCENARIO_DIR.glob("*.yaml")]
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario}' not found. Available: {available}")

    def _run():
        conn = get_conn()
        results = {}
        for code in ["B1", "B2", "B3", "P"]:
            strategy = STRATEGIES[code]
            sc = ScenarioLoader.load(scenario_file)
            engine = SimulationEngine()
            run_id = f"api-cmp-{uuid.uuid4().hex[:6]}"
            try:
                result = engine.run(sc, strategy, conn, run_id=run_id)
            except Exception as e:
                _compare_jobs[scenario] = {"status": "error", "error": f"Strategy {code} failed: {type(e).__name__}: {e}"}
                return
            m = result.metrics
            results[code] = {
                "overflow_events":      m.get("overflow_events_count"),
                "avg_search_time_min":  round(m["avg_search_time_min"], 2) if m.get("avg_search_time_min") is not None else None,
                "avg_wait_time_min":    round(m["avg_wait_time_min"], 2)   if m.get("avg_wait_time_min")   is not None else None,
                "avg_gate_queue":       round(m["avg_gate_queue"], 2)      if m.get("avg_gate_queue")      is not None else None,
                "peak_gate_queue":      m.get("max_gate_queue"),
                "successful_park_rate": round(m["successful_park_rate"], 4) if m.get("successful_park_rate") is not None else None,
            }
        _compare_jobs[scenario] = {"status": "done", "result": {"scenario": scenario, "comparison": results}}

    _compare_jobs[scenario] = {"status": "running"}
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started", "scenario": scenario}


@app.get(
    "/compare/{scenario}/status",
    tags=["Simulation"],
    summary="Poll comparison status",
    description="Use POST /compare/{scenario}/start first. Returns not_started when no job exists, running while it executes, or done with comparison metrics.",
)
def compare_status(
    scenario: str = PathParameter(..., description="Scenario ID used when starting the comparison")
):
    """Poll status; a missing job is reported as not_started rather than an error."""
    if not (SCENARIO_DIR / f"{scenario}.yaml").exists():
        available = [f.stem for f in SCENARIO_DIR.glob("*.yaml")]
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario}' not found. Available: {available}")
    job = _compare_jobs.get(scenario)
    if job is None:
        return {
            "status": "not_started",
            "scenario": scenario,
            "message": f"Start this comparison with POST /compare/{scenario}/start, then poll this endpoint again.",
        }
    if job["status"] == "done":
        return {"status": "done", **job["result"]}
    if job["status"] == "error":
        return {"status": "error", "scenario": scenario, "error": job.get("error", "Unknown error")}
    return {"status": "running", "scenario": scenario}


@app.get("/events", tags=["Campus"])
def get_events():
    conn = get_conn()
    rows = conn.execute(
        "SELECT event_id, event_type, name, start_time, end_time, status FROM events WHERE campus_id='vitap'"
    ).fetchall()
    return {
        "campus": "vitap",
        "events": [
            {"id": r[0], "type": r[1], "name": r[2], "start": r[3], "end": r[4], "status": r[5]}
            for r in rows
        ],
    }


# ── Routing endpoints ──────────────────────────────────────────────────────────

@app.get(
    "/route",
    tags=["Routing"],
    summary="Get point-to-point route",
    response_description="Dijkstra path with geometry, time and distance",
)
def get_route(
    origin: str = Query("vitap-gate-main", description="Start node ID (gate or parking lot)"),
    destination: str = Query("vitap-lot-hostel", description="End node ID (parking lot or destination)"),
    mode: str = Query("drive", enum=["drive", "walk"], description="Travel mode"),
):
    """
    Compute the Dijkstra shortest path between two campus nodes.

    **Try these combinations:**
    - Drive: `origin=vitap-gate-main` → `destination=vitap-lot-hostel` → `mode=drive`
    - Drive: `origin=vitap-gate-visitor` → `destination=vitap-lot-academic-main` → `mode=drive`
    - Walk:  `origin=vitap-lot-hostel` → `destination=vitap-osm-mh5-001` → `mode=walk`

    Returns path, geometry (polyline), total time and distance.
    """
    conn = get_conn()
    graph = _build_graph(conn)

    if origin not in graph:
        raise HTTPException(status_code=404, detail=f"Origin '{origin}' not found in campus graph")
    if destination not in graph:
        raise HTTPException(status_code=404, detail=f"Destination '{destination}' not found in campus graph")

    # Filter subgraph by mode and open status
    def edge_filter(u, v, data):
        if data.get("status") != "open":
            return False
        if mode == "drive":
            return bool(data.get("is_driveable"))
        return bool(data.get("is_walkable"))

    filtered = nx.subgraph_view(
        graph,
        filter_edge=lambda u, v, k=None: edge_filter(u, v, graph.edges[u, v])
        if not k else edge_filter(u, v, graph.edges[u, v, k])
    )

    try:
        path = nx.dijkstra_path(
            filtered, origin, destination,
            weight="weight_time_seconds"
        )
        length = nx.dijkstra_path_length(
            filtered, origin, destination,
            weight="weight_time_seconds"
        )
        dist = nx.dijkstra_path_length(
            filtered, origin, destination,
            weight="weight_distance_meters"
        )
    except nx.NetworkXNoPath:
        raise HTTPException(
            status_code=422,
            detail=f"No {mode} route from '{origin}' to '{destination}'. Check road closures or mode restrictions."
        )
    except nx.NodeNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))

    geometry = _route_geometry(graph, path)

    # Build segment list
    segments = []
    for i in range(len(path) - 1):
        u, v = path[i], path[i + 1]
        if graph.has_edge(u, v):
            edge = graph.edges[u, v]
            segments.append({
                "from": u,
                "to": v,
                "road_id": edge.get("road_id"),
                "time_seconds": edge.get("weight_time_seconds"),
                "distance_meters": edge.get("weight_distance_meters"),
                "is_driveable": edge.get("is_driveable"),
                "is_walkable": edge.get("is_walkable"),
            })

    return {
        "origin": origin,
        "destination": destination,
        "mode": mode,
        "path": path,
        "total_time_seconds": round(length),
        "total_distance_meters": round(dist),
        "total_time_minutes": round(length / 60, 1),
        "segments": segments,
        "geometry": geometry,
    }


class ShortestPathRequest(BaseModel):
    origin_id: str
    destination_id: str
    travel_mode: str = "driving"   # "driving" or "walking"
    optimization: str = "fastest"  # "fastest" or "shortest_distance"

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "origin_id": "vitap-gate-main",
                    "destination_id": "vitap-lot-hostel",
                    "travel_mode": "driving",
                    "optimization": "fastest",
                },
                {
                    "origin_id": "vitap-gate-visitor",
                    "destination_id": "vitap-lot-academic-main",
                    "travel_mode": "driving",
                    "optimization": "shortest_distance",
                },
                {
                    "origin_id": "vitap-lot-hostel",
                    "destination_id": "vitap-osm-mh5-001",
                    "travel_mode": "walking",
                    "optimization": "fastest",
                },
            ]
        }
    }


@app.post(
    "/routing/shortest-path",
    tags=["Routing"],
    summary="Shortest or fastest path between campus nodes",
    response_description="Dijkstra route with geometry, duration and distance",
)
def routing_shortest_path(req: ShortestPathRequest):
    """
    Compute the optimal route between two campus nodes using Dijkstra.

    **optimization options:**
    - `fastest` — minimize travel time
    - `shortest_distance` — minimize distance in metres

    **travel_mode options:**
    - `driving` — driveable roads only
    - `walking` — walkable paths only

    **Try these examples (pre-filled in the request body above):**
    1. Drive gate→lot (fastest): `vitap-gate-main` → `vitap-lot-hostel`
    2. Drive gate→lot (shortest distance): `vitap-gate-visitor` → `vitap-lot-academic-main`
    3. Walk lot→destination: `vitap-lot-hostel` → `vitap-osm-mh5-001`
    """
    conn = get_conn()
    graph = _build_graph(conn)

    if req.origin_id not in graph:
        raise HTTPException(status_code=404, detail=f"Origin '{req.origin_id}' not found in campus graph")
    if req.destination_id not in graph:
        raise HTTPException(status_code=404, detail=f"Destination '{req.destination_id}' not found in campus graph")

    mode = "drive" if req.travel_mode == "driving" else "walk"
    weight = "weight_distance_meters" if req.optimization == "shortest_distance" else "weight_time_seconds"

    def ef(u, v, data):
        if data.get("status") != "open":
            return False
        return bool(data.get("is_driveable")) if mode == "drive" else bool(data.get("is_walkable"))

    filtered = nx.subgraph_view(graph, filter_edge=lambda u, v, k=None: ef(u, v, graph.edges[u, v]))
    try:
        path = nx.dijkstra_path(filtered, req.origin_id, req.destination_id, weight=weight)
        t = nx.dijkstra_path_length(filtered, req.origin_id, req.destination_id, weight="weight_time_seconds")
        d = nx.dijkstra_path_length(filtered, req.origin_id, req.destination_id, weight="weight_distance_meters")
        geometry = _route_geometry(graph, path)
    except nx.NetworkXNoPath:
        raise HTTPException(status_code=422, detail=f"No {req.travel_mode} route from '{req.origin_id}' to '{req.destination_id}'.")
    except nx.NodeNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {
        "success": True,
        "origin_id": req.origin_id,
        "destination_id": req.destination_id,
        "route_type": req.travel_mode,
        "optimization": req.optimization,
        "distance_m": round(d),
        "duration_min": round(t / 60, 1),
        "path": path,
        "geometry": geometry,
        "algorithm": "Dijkstra",
        "graph_based": True,
    }


@app.get(
    "/find_parking",
    tags=["Routing"],
    summary="Find best parking for a destination",
    response_description="Ranked parking options with drive + walk routes",
    openapi_extra={
        "parameters": [
            {
                "name": "destination_id",
                "in": "query",
                "required": True,
                "schema": {
                    "type": "string",
                    "example": "vitap-osm-mh5-001",
                    "enum": [
                        "vitap-osm-mh5-001",
                        "vitap-osm-mh4-001",
                        "vitap-osm-lh2-001",
                        "vitap-lib-001",
                        "vitap-gym-001",
                    ],
                },
                "description": "Where the user is going (hostel, library, gym, …)",
            },
            {
                "name": "gate_id",
                "in": "query",
                "required": False,
                "schema": {
                    "type": "string",
                    "default": "vitap-gate-main",
                    "enum": ["vitap-gate-main", "vitap-gate-visitor"],
                },
                "description": "Entry gate the vehicle uses",
            },
        ]
    },
)
def find_parking(destination_id: str, gate_id: str = "vitap-gate-main"):
    """
    Find the best parking lot for a given destination, with vehicle + walking routes.

    **Try these:**
    - `destination_id=vitap-osm-mh5-001` (MH-1 hostel) + `gate_id=vitap-gate-main`
    - `destination_id=vitap-lib-001` (library) + `gate_id=vitap-gate-visitor`
    - `destination_id=vitap-gym-001` (gym) + `gate_id=vitap-gate-main`

    Returns ranked parking lots with drive route (gate→lot) and walk route (lot→destination).

    Returns ranked parking options:
    - Vehicle route: gate → parking lot (driveable)
    - Walking route: parking lot → destination (walkable)
    - Scores by predicted fill rate + total travel time
    """
    conn = get_conn()
    graph = _build_graph(conn)

    # Get all open parking lots
    lots = conn.execute(
        "SELECT parking_lot_id, name, usable_capacity, status FROM parking_lots WHERE campus_id='vitap' AND status='open'"
    ).fetchall()

    # Get destination info
    dest_row = conn.execute(
        "SELECT destination_id, name, category, nearest_parking_lot_ids FROM destinations WHERE campus_id='vitap' AND destination_id=?",
        (destination_id,)
    ).fetchone()

    if not dest_row:
        raise HTTPException(status_code=404, detail=f"Destination '{destination_id}' not found")

    # Get predictions for fill rates
    try:
        from digital_twin.prediction.engine import forecast_context
        predictor, _basis = forecast_context(conn)
        preds = predictor.predict_all(15)
    except Exception:
        preds = {}

    # Preferred lots from destination config
    preferred_lots = []
    if dest_row[3]:
        try:
            preferred_lots = json.loads(dest_row[3])
        except Exception:
            preferred_lots = []

    def _make_filtered(mode):
        def _ef(u, v, data):
            if data.get("status") != "open":
                return False
            return bool(data.get("is_driveable")) if mode == "drive" else bool(data.get("is_walkable"))
        return nx.subgraph_view(graph, filter_edge=lambda u, v, k=None: _ef(u, v, graph.edges[u, v]))

    def _try_route(origin, dest, mode, weight="weight_time_seconds"):
        filtered = _make_filtered(mode)
        try:
            path = nx.dijkstra_path(filtered, origin, dest, weight=weight)
            t = nx.dijkstra_path_length(filtered, origin, dest, weight="weight_time_seconds")
            d = nx.dijkstra_path_length(filtered, origin, dest, weight="weight_distance_meters")
            geo = _route_geometry(graph, path)
            return {
                "path": path, "time_seconds": round(t), "distance_meters": round(d),
                "geometry": geo,
                "optimization": "fastest_time" if weight == "weight_time_seconds" else "shortest_distance",
                "algorithm": "Dijkstra",
            }
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    options = []
    for lot in lots:
        lot_id = lot[0]
        lot_name = lot[1]
        capacity = lot[2]

        # Get fill rate prediction
        pred = preds.get(lot_id, {})
        fill_rate = pred.get("fill_rate", 0.4) if pred else 0.4
        available = int(capacity * (1 - fill_rate))

        # Compute drive route: gate → lot (fastest by time)
        drive_route = _try_route(gate_id, lot_id, "drive", weight="weight_time_seconds")
        if not drive_route:
            continue  # skip unreachable lots

        # Distance-optimal drive route (may differ from time-optimal)
        drive_route_dist = _try_route(gate_id, lot_id, "drive", weight="weight_distance_meters")
        # Only keep the distance route if it's genuinely shorter in metres
        if drive_route_dist and drive_route_dist["path"] == drive_route["path"]:
            drive_route_dist = None  # identical path — no need to show duplicate

        # Compute walk route: lot → destination (fastest by time)
        walk_route = _try_route(lot_id, destination_id, "walk", weight="weight_time_seconds")
        if not walk_route:
            continue  # skip if no walking path to destination

        # Distance-optimal walk route
        walk_route_dist = _try_route(lot_id, destination_id, "walk", weight="weight_distance_meters")
        if walk_route_dist and walk_route_dist["path"] == walk_route["path"]:
            walk_route_dist = None

        # Scoring: lower is better
        # 40% drive time + 40% walk time + 20% fill penalty
        drive_t = drive_route["time_seconds"]
        walk_t = walk_route["time_seconds"]
        fill_penalty = fill_rate * 300  # up to 5 min penalty at full
        score = 0.4 * drive_t + 0.4 * walk_t + 0.2 * fill_penalty

        is_preferred = lot_id in preferred_lots
        if is_preferred:
            score *= 0.85  # 15% bonus for recommended lots

        # Cost breakdown for this lot (M4/M9 spec: expose individual cost components)
        fill_penalty_norm = min(1.0, fill_rate * 1.2)
        drive_norm = min(1.0, drive_t / 600.0)   # 600s = 10 min reference
        walk_norm = min(1.0, walk_t / 600.0)
        cost_breakdown = {
            "search_travel_cost": round(0.4 * drive_norm + 0.4 * walk_norm, 4),
            "fill_penalty_cost": round(0.2 * fill_penalty_norm, 4),
            "total_score": round(score, 1),
            "components": {
                "drive_time_seconds": round(drive_t),
                "walk_time_seconds": round(walk_t),
                "fill_rate": round(fill_rate, 3),
                "fill_penalty_seconds": round(fill_rate * 300),
            },
        }
        pred_detail = preds.get(lot_id, {})
        options.append({
            "lot_id": lot_id,
            "lot_name": lot_name,
            "capacity": capacity,
            "estimated_available": max(0, available),
            "predicted_fill_rate": round(fill_rate, 3),
            "predicted_fill_pct": round(fill_rate * 100, 1),
            "prediction_interval_90": {
                "lower_pct": round(float(pred_detail.get("lower_90", fill_rate * 0.9)) * 100, 1),
                "upper_pct": round(float(pred_detail.get("upper_90", min(1.0, fill_rate * 1.1))) * 100, 1),
            } if pred_detail else None,
            "is_preferred_for_destination": is_preferred,
            "drive_route": drive_route,
            "drive_route_shortest_distance": drive_route_dist,
            "walk_route": walk_route,
            "walk_route_shortest_distance": walk_route_dist,
            "total_time_seconds": round(drive_t + walk_t),
            "total_time_minutes": round((drive_t + walk_t) / 60, 1),
            "drive_time_minutes": round(drive_t / 60, 1),
            "walk_time_minutes": round(walk_t / 60, 1),
            "score": round(score, 1),
            "cost_breakdown": cost_breakdown,
            "recommendation_reason": _explain(lot_name, fill_rate, drive_t, walk_t, is_preferred),
        })

    options.sort(key=lambda x: x["score"])

    # Assign ranks and build structured explanation for top choice (M9)
    for i, opt in enumerate(options):
        opt["rank"] = i + 1
    best = options[0] if options else None
    explanation = None
    if best:
        pred_d = preds.get(best["lot_id"], {})
        explanation = _explain_structured(
            lot_id=best["lot_id"],
            lot_name=best["lot_name"],
            fill_rate=best["predicted_fill_rate"],
            drive_t=best["drive_route"]["time_seconds"],
            walk_t=best["walk_route"]["time_seconds"],
            preferred=best["is_preferred_for_destination"],
            lower_90=float(pred_d.get("lower_90", best["predicted_fill_rate"] * 0.9)) if pred_d else None,
            upper_90=float(pred_d.get("upper_90", min(1.0, best["predicted_fill_rate"] * 1.1))) if pred_d else None,
            objective_score=best["score"],
            rank=1,
            alternatives=options[1:3],
        )

    return {
        "destination_id": destination_id,
        "destination_name": dest_row[1],
        "destination_category": dest_row[2],
        "gate_used": gate_id,
        "options_count": len(options),
        "best": best,
        "explanation": explanation,
        "options": options[:5],  # top 5
        "note": "[SYNTHETIC] Fill rates from XGBoost model trained on synthetic data. Not real occupancy.",
    }


def _explain(lot_name, fill_rate, drive_t, walk_t, preferred):
    parts = []
    if preferred:
        parts.append("Nearest to your destination")
    if fill_rate < 0.5:
        parts.append(f"{(1-fill_rate)*100:.0f}% capacity available")
    elif fill_rate < 0.8:
        parts.append(f"Moderately occupied ({fill_rate*100:.0f}%)")
    else:
        parts.append(f"Near capacity ({fill_rate*100:.0f}%) — consider alternatives")
    parts.append(f"{round(drive_t/60, 1)} min drive + {round(walk_t/60, 1)} min walk")
    return ". ".join(parts)


def _explain_structured(lot_id, lot_name, fill_rate, drive_t, walk_t, preferred,
                         lower_90=None, upper_90=None, objective_score=None, rank=None,
                         risk_level=None, alternatives=None):
    """Return a structured explanation object matching M9 spec requirements."""
    if fill_rate < 0.5:
        occupancy_status = "low"
    elif fill_rate < 0.80:
        occupancy_status = "moderate"
    elif fill_rate < 0.90:
        occupancy_status = "high"
    else:
        occupancy_status = "critical"

    explanation = {
        "lot_id": lot_id,
        "lot_name": lot_name,
        "chosen": preferred or (rank == 1),
        "current_fill_pct": round(fill_rate * 100, 1),
        "occupancy_status": occupancy_status,
        "predicted_fill_pct": round(fill_rate * 100, 1),
        "prediction_interval_90": (
            {"lower_pct": round((lower_90 or fill_rate) * 100, 1),
             "upper_pct": round((upper_90 or fill_rate) * 100, 1)}
            if lower_90 is not None else None
        ),
        "drive_time_min": round(drive_t / 60, 1),
        "walk_time_min": round(walk_t / 60, 1),
        "total_time_min": round((drive_t + walk_t) / 60, 1),
        "risk_level": risk_level or ("HIGH" if fill_rate >= 0.90 else "MEDIUM" if fill_rate >= 0.75 else "LOW"),
        "objective_score": round(objective_score, 4) if objective_score is not None else None,
        "rank": rank,
        "preferred_for_destination": preferred,
        "why_chosen": _explain(lot_name, fill_rate, drive_t, walk_t, preferred),
        "alternatives_count": len(alternatives) if alternatives else 0,
        "fallback_used": False,
    }
    return explanation


# ── Prediction endpoints ───────────────────────────────────────────────────────

def _data_source_label(prefix: str) -> str:
    from digital_twin.prediction.engine import data_description
    d = data_description()
    return f"[SYNTHETIC] {prefix} synthetic demand data" if d["mode"] == "synthetic" else f"[{d['mode'].upper()}] {prefix} {d['label']}"


@app.get("/predict/model-metrics", tags=["Prediction"])
def get_model_metrics():
    """Return XGBoost evaluation metrics (MAE, R², 90% CI coverage) from prediction_metrics.json."""
    import json as _json
    metrics_file = ROOT / "experiments" / "results" / "prediction_metrics.json"
    if metrics_file.exists():
        try:
            data = _json.loads(metrics_file.read_text())
            lots = data.get("lots", [])
            xgb_lots = [l["xgboost"] for l in lots if "xgboost" in l]
            if xgb_lots:
                avg_mae = round(sum(l["mae"] for l in xgb_lots) / len(xgb_lots), 4)
                avg_r2  = round(sum(l["r2"]  for l in xgb_lots) / len(xgb_lots), 4)
                avg_cov = round(sum(l.get("coverage_90", 0) for l in xgb_lots) / len(xgb_lots), 4)
                return {
                    "horizon_minutes": data.get("horizon_minutes", 15),
                    "summary": {"mae": avg_mae, "r2": avg_r2, "coverage_90": avg_cov,
                                "n_lots": len(xgb_lots)},
                    "lots": lots,
                    "model": "XGBoost + conformal prediction",
                    "data_source": "experiments/results/prediction_metrics.json",
                }
        except Exception:
            pass
    return {"summary": {}, "lots": [], "model": "XGBoost", "note": "metrics not yet computed"}


def _count(conn, sql: str) -> int:
    """Run a COUNT query and return an int (0 on error)."""
    try:
        row = conn.execute(sql).fetchone()
        if row is None:
            return 0
        return int(row["n"] if isinstance(row, dict) else row[0])
    except Exception:
        return 0


@app.get("/data/summary", tags=["Info"])
def get_data_summary():
    """Return structured dataset summary for the dashboard Dataset Overview tab."""
    conn = get_conn()
    try:
        # ── Occupancy history (lot_occupancy_history table) ────────────────────
        total_obs    = _count(conn, "SELECT COUNT(*) AS n FROM lot_occupancy_history")
        real_manual  = _count(conn, "SELECT COUNT(*) AS n FROM lot_occupancy_history WHERE source IN ('sensor','manual')")
        sim_synth    = _count(conn, "SELECT COUNT(*) AS n FROM lot_occupancy_history WHERE source IN ('simulation','synthetic')")
        null_vals    = _count(conn, "SELECT COUNT(*) AS n FROM lot_occupancy_history WHERE occupied_spaces IS NULL")
        try:
            date_row = conn.execute(
                "SELECT MIN(recorded_at) AS mn, MAX(recorded_at) AS mx FROM lot_occupancy_history"
            ).fetchone()
            date_range = {
                "from": (date_row["mn"] if isinstance(date_row, dict) else date_row[0]) if date_row else None,
                "to":   (date_row["mx"] if isinstance(date_row, dict) else date_row[1]) if date_row else None,
            }
        except Exception:
            date_range = {"from": None, "to": None}

        # ── Campus config ──────────────────────────────────────────────────────
        n_lots  = _count(conn, "SELECT COUNT(*) AS n FROM parking_lots WHERE campus_id='vitap'")
        n_gates = _count(conn, "SELECT COUNT(*) AS n FROM gates WHERE campus_id='vitap'")
        n_roads = _count(conn, "SELECT COUNT(*) AS n FROM roads WHERE campus_id='vitap'")
        n_dests = _count(conn, "SELECT COUNT(*) AS n FROM destinations WHERE campus_id='vitap'")

        # ── Synthetic training data size ───────────────────────────────────────
        syn_rows = 0
        syn_last = None
        try:
            from digital_twin.prediction.engine import data_description
            dd = data_description()
            syn_rows = dd.get("n_rows", 0)
            syn_last = dd.get("last_generated")
            syn_mode = dd.get("mode", "synthetic")
            syn_label = dd.get("label", "synthetic demand profile")
        except Exception:
            syn_mode = "synthetic"
            syn_label = "synthetic demand profile"

        # ── Simulation runs ────────────────────────────────────────────────────
        n_sim_runs = _count(conn, "SELECT COUNT(*) AS n FROM simulation_runs")

        # ── Experiment results (from JSON files, not DB) ───────────────────────
        exp_total = 0
        try:
            if SUMMARY_FILE.exists():
                s = json.loads(SUMMARY_FILE.read_text())
                # results_summary.json has keys like "E1_normal_day|B1" -> {n_runs, ...}
                exp_total = sum(v.get("n_runs", 0) for v in s.values() if isinstance(v, dict))
        except Exception:
            pass

        # ── New table counts (migration 0009) ──────────────────────────────────
        raw_obs_count  = _count(conn, "SELECT COUNT(*) AS n FROM parking_observations")
        occ_snap_count = _count(conn, "SELECT COUNT(*) AS n FROM occupancy_snapshots")
        route_count    = _count(conn, "SELECT COUNT(*) AS n FROM route_cache")
        events_count   = _count(conn, "SELECT COUNT(*) AS n FROM campus_events")

        # ── Data integrity checks ──────────────────────────────────────────────
        integrity = {
            "parking_lots_loaded": n_lots > 0,
            "gates_loaded": n_gates > 0,
            "occupancy_history_no_nulls": null_vals == 0,
            "experiment_results_present": exp_total > 0,
            "prediction_model_trained": syn_rows > 0,
        }

        return {
            "status": "ok",
            "occupancy_history": {
                "total": total_obs,
                "real_manual": real_manual,
                "simulated_synthetic": sim_synth,
                "null_values": null_vals,
                "source": "lot_occupancy_history (database)",
                "date_range": date_range,
            },
            "campus_config": {
                "parking_lots": n_lots,
                "gates": n_gates,
                "roads": n_roads,
                "destinations": n_dests,
                "source": "configs/campus/vitap.yaml -> database",
                "generation": "OSM-calibrated hand-crafted campus graph",
            },
            "synthetic_training_data": {
                "rows": syn_rows,
                "last_generated": syn_last,
                "source": f"[{syn_mode.upper()}] {syn_label}",
                "generation": "digital_twin/prediction/training_data.py (BPR + demand profile)",
                "note": "Clearly labelled synthetic; real sensor data replaces it after 1 week of readings.",
            },
            "simulation_runs": {
                "total": n_sim_runs,
                "source": "simulation_runs table (database)",
            },
            "experiment_results": {
                "total_runs": exp_total,
                "source": "experiments/results/results_summary.json",
            },
            "record_counts": {
                "parking_observations": raw_obs_count,
                "occupancy_snapshots": occ_snap_count,
                "route_cache": route_count,
                "campus_events": events_count,
            },
            "data_integrity": integrity,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Data summary failed: {e}")


@app.post("/predict/retrain", tags=["Prediction"], dependencies=[Depends(require_write_key)])
def retrain_predictor():
    """Refit the forecast models on current data: real recorded occupancy for every lot with at least a week
    of readings, synthetic for the rest. Run after importing or posting real observations."""
    from digital_twin.prediction.engine import retrain
    return retrain(get_conn())


@app.get(
    "/predict/occupancy",
    tags=["Prediction"],
    summary="Predict occupancy for one lot",
    response_description="Fill rate forecast with 90% confidence interval",
    openapi_extra={
        "parameters": [
            {
                "name": "lot_id",
                "in": "query",
                "required": False,
                "schema": {
                    "type": "string",
                    "default": "vitap-lot-academic-main",
                    "enum": [
                        "vitap-lot-academic-main",
                        "vitap-lot-hostel",
                        "vitap-lot-admin-visitor",
                        "vitap-lot-sports",
                        "vitap-lot-overflow",
                    ],
                },
                "description": "Parking lot ID",
            },
            {
                "name": "horizon_minutes",
                "in": "query",
                "required": False,
                "schema": {"type": "integer", "default": 15, "enum": [5, 15, 30, 60]},
                "description": "How many minutes ahead to forecast",
            },
        ]
    },
)
def predict_occupancy(lot_id: str = "vitap-lot-academic-main", horizon_minutes: int = 15,
                      as_of: Optional[str] = None):
    """Predict occupancy for a parking lot at `horizon_minutes` from now.

    **Try these:**
    - `lot_id=vitap-lot-hostel` + `horizon_minutes=15`
    - `lot_id=vitap-lot-academic-main` + `horizon_minutes=30`
    - `lot_id=vitap-lot-sports` + `horizon_minutes=60`

    Returns fill rate (0–1), 90% confidence interval and model used.
    """
    try:
        from digital_twin.prediction.engine import forecast_context
        predictor, basis = forecast_context(get_conn(), as_of)
        result = predictor.predict(lot_id=lot_id, horizon_minutes=horizon_minutes)
        return {
            "lot_id": lot_id,
            "horizon_minutes": horizon_minutes,
            "predicted_fill_rate": round(float(result["fill_rate"]), 4),
            "lower_90": round(float(result["lower_90"]), 4),
            "upper_90": round(float(result["upper_90"]), 4),
            "model": result["model"],
            "proactive_alert": bool(result["fill_rate"] >= 0.90),
            "forecast_basis": basis,
            "data_source": _data_source_label("Trained on"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prediction failed: {e}")


@app.get(
    "/predict/all",
    tags=["Prediction"],
    summary="Predict occupancy for all lots",
    response_description="Fill rate forecasts for all 5 campus parking lots",
    openapi_extra={
        "parameters": [
            {
                "name": "horizon_minutes",
                "in": "query",
                "required": False,
                "schema": {"type": "integer", "default": 15, "enum": [5, 15, 30, 60]},
                "description": "How many minutes ahead to forecast",
            },
        ]
    },
)
def predict_all_lots(horizon_minutes: int = 15, as_of: Optional[str] = None):
    """Predict occupancy for **all 5** VIT-AP parking lots in one call.

    Try it out: click **Try it out → Execute** (default `horizon_minutes=15` works).

    Returns fill rate (0–1) + 90% CI for each lot.
    """
    lot_ids = [
        "vitap-lot-academic-main",
        "vitap-lot-hostel",
        "vitap-lot-admin-visitor",
        "vitap-lot-sports",
        "vitap-lot-overflow",
    ]
    try:
        from digital_twin.prediction.engine import forecast_context
        from digital_twin.prediction.engine import data_description
        predictor, basis = forecast_context(get_conn(), as_of)
        real = set(data_description()["real_lots"])
        predictions = []
        for lot_id in lot_ids:
            r = predictor.predict(lot_id=lot_id, horizon_minutes=horizon_minutes)
            predictions.append({
                "lot_id": lot_id,
                "predicted_fill_rate": round(float(r["fill_rate"]), 4),
                "lower_90": round(float(r["lower_90"]), 4),
                "upper_90": round(float(r["upper_90"]), 4),
                "proactive_alert": bool(r["fill_rate"] >= 0.90),
                "model": r.get("model", "unknown"),
                "live_fill_used": bool(r.get("basis", {}).get("live_fill_used")),
                "trained_on": "real" if lot_id in real else "synthetic",
            })
        return {
            "horizon_minutes": horizon_minutes,
            "forecast_basis": basis,
            "predictions": predictions,
            "data_source": _data_source_label("XGBoost model"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prediction failed: {e}")


# ── Risk endpoints ─────────────────────────────────────────────────────────────

@app.get(
    "/risk/current",
    tags=["Risk"],
    summary="Current risk scores",
    response_description="Risk scores for overflow, gate congestion, lot fill",
)
def get_current_risk():
    """Return current risk scores across all risk dimensions.

    Try it out: click **Try it out → Execute**. No parameters needed.
    """
    try:
        from digital_twin.risk.engine import RiskEngine
        conn = get_conn()
        re = RiskEngine(conn)
        scores = re.compute_all()
        return {"status": "ok", "risks": scores}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Risk computation failed: {e}")


@app.get(
    "/risk/forecast",
    tags=["Risk"],
    summary="Forecasted risk scores",
    response_description="Risk forecast at the given horizon",
    openapi_extra={
        "parameters": [
            {
                "name": "horizon",
                "in": "query",
                "required": False,
                "schema": {"type": "integer", "default": 15, "enum": [5, 15, 30, 60]},
                "description": "Minutes ahead to forecast risk",
            },
        ]
    },
)
def get_risk_forecast(horizon: int = 15, as_of: Optional[str] = None):
    """Return forecasted risk scores at `horizon` minutes ahead.

    Try it out: click **Try it out → Execute** (default `horizon=15` works).
    """
    try:
        from digital_twin.risk.engine import RiskEngine
        from digital_twin.prediction.engine import forecast_context
        conn = get_conn()
        predictor, basis = forecast_context(conn, as_of)
        re = RiskEngine(conn, predictor=predictor)
        scores = re.compute_forecast(horizon_minutes=horizon)
        return {"horizon_minutes": horizon, "forecast_basis": basis, "risks": scores}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Risk forecast failed: {e}")


# ── Optimization endpoints ─────────────────────────────────────────────────────

@app.get(
    "/optimize/profiles",
    tags=["Optimization"],
    summary="List optimization weight profiles",
    response_description="P1/P2/P3 profiles with weights",
)
def optimize_profiles():
    """Return available weight profiles (P1/P2/P3) and their objective weights.

    Try it out: click **Try it out → Execute**. No parameters needed.
    """
    from legacy.flat_optimizer.active_engine import describe_profiles
    return {"profiles": describe_profiles()}


@app.post("/optimize", tags=["Optimization"])
def optimize(req: OptimizeRequest):
    try:
        import time as _time
        from legacy.flat_optimizer.active_engine import MultiObjectiveOptimizer
        conn = get_conn()
        opt = MultiObjectiveOptimizer(weight_profile=req.weight_profile)
        t0 = _time.perf_counter()
        recommendations = opt.generate_recommendations(conn)
        latency_ms = round((_time.perf_counter() - t0) * 1000, 2)
        return {
            "scenario": req.scenario,
            "weight_profile": req.weight_profile,
            "latency_ms": latency_ms,
            "fallback_used": any(r.get("type") == "FALLBACK" for r in recommendations),
            "recommendations": recommendations,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Optimization failed: {e}")


@app.get(
    "/recommendations",
    tags=["Optimization"],
    summary="Get proactive parking recommendations",
    response_description="Ranked lot recommendations with cost breakdown",
)
def get_recommendations():
    """Get the latest proactive parking recommendations (uses default weight profile P1).

    Try it out: click **Try it out → Execute**. No parameters needed.
    """
    try:
        import time as _time
        from legacy.flat_optimizer.active_engine import MultiObjectiveOptimizer
        conn = get_conn()
        opt = MultiObjectiveOptimizer()
        t0 = _time.perf_counter()
        recs = opt.generate_recommendations(conn)
        latency_ms = round((_time.perf_counter() - t0) * 1000, 2)
        return {"recommendations": recs, "count": len(recs), "latency_ms": latency_ms}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Recommendations failed: {e}")


# ── Experiments endpoints ──────────────────────────────────────────────────────

class ExperimentRunRequest(BaseModel):
    scenario: str
    strategies: List[str] = ["B1", "B2", "B3", "P"]
    seeds: List[int] = [0, 1, 2]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"scenario": "E1_normal_day", "strategies": ["B1", "B2", "B3", "P"], "seeds": [0, 1, 2]},
                {"scenario": "E2_event_placement", "strategies": ["B3", "P"], "seeds": [0]},
                {"scenario": "E4_gate_closure", "strategies": ["P"], "seeds": [0, 1, 2, 3, 4]},
            ]
        }
    }


def _load_results() -> list:
    if not RESULTS_FILE.exists():
        return []
    try:
        return json.loads(RESULTS_FILE.read_text())
    except Exception:
        return []


def _compute_summary(results: list) -> dict:
    from collections import defaultdict
    groups = defaultdict(list)
    for r in results:
        if r.get("status") == "ok":
            groups[(r["scenario"], r["strategy"])].append(r)

    summary = {}
    for (sc, st), runs in groups.items():
        key = f"{sc}|{st}"
        metrics = {}
        for metric in ["avg_search_time_min", "avg_wait_time_min", "avg_gate_queue",
                        "overflow_events", "successful_park_rate"]:
            vals = [r[metric] for r in runs if r.get(metric) is not None]
            if not vals:
                metrics[metric] = None
                continue
            n = len(vals)
            mean = statistics.mean(vals)
            std = statistics.stdev(vals) if n > 1 else 0.0
            tc = 2.0 if n < 30 else 1.96
            metrics[metric] = {
                "mean": round(mean, 4), "std": round(std, 4),
                "ci95": round(tc * std / math.sqrt(n), 4), "n": n,
            }
        summary[key] = {"scenario": sc, "strategy": st, "n": len(runs), "metrics": metrics}
    return summary


@app.get("/experiments/results", tags=["Experiments"])
def get_experiment_results(
    scenario: Optional[str] = None,
    strategy: Optional[str] = None,
    limit: int = 200,
):
    """Return stored experiment run records, optionally filtered."""
    results = _load_results()
    if not results:
        return {"results": [], "total": 0, "note": "No results found. Run experiments first."}

    if scenario:
        results = [r for r in results if r.get("scenario") == scenario]
    if strategy:
        results = [r for r in results if r.get("strategy") == strategy]

    return {
        "results": results[:limit],
        "total": len(results),
        "returned": min(len(results), limit),
        "data_source": "experiments/results/all_results.json",
    }


@app.get("/experiments/summary", tags=["Experiments"])
def get_experiment_summary():
    """Return aggregated stats (mean ± 95%CI) per scenario × strategy."""
    # Prefer pre-computed file; recompute if absent or if all_results is newer
    if SUMMARY_FILE.exists():
        try:
            return json.loads(SUMMARY_FILE.read_text())
        except Exception:
            pass
    results = _load_results()
    if not results:
        return {}
    return _compute_summary(results)


@app.post("/experiments/run", tags=["Experiments"])
def run_experiment(req: ExperimentRunRequest):
    """
    Run a small experiment batch synchronously (≤ 5 seeds recommended).
    Results are appended to experiments/results/all_results.json.
    """
    import time as _time

    STRATEGY_MAP = {
        "B1": ("B1", lambda: __import__("digital_twin.simulation.strategy",
                fromlist=["FirstAvailableStrategy"]).FirstAvailableStrategy()),
        "B2": ("B2", lambda: __import__("digital_twin.simulation.strategy",
                fromlist=["NearestAvailableStrategy"]).NearestAvailableStrategy()),
        "B3": ("B3", lambda: __import__("digital_twin.simulation.demo_strategies",
                fromlist=["PredictionOnlyStrategy"]).PredictionOnlyStrategy()),
        "P":  ("P",  lambda: __import__("digital_twin.simulation.demo_strategies",
                fromlist=["ParkingNavXFullStrategy"]).ParkingNavXFullStrategy()),
    }

    scenario_file = SCENARIO_DIR / f"{req.scenario}.yaml"
    if not scenario_file.exists():
        raise HTTPException(status_code=404, detail=f"Scenario '{req.scenario}' not found")

    bad_strats = [s for s in req.strategies if s not in STRATEGY_MAP]
    if bad_strats:
        raise HTTPException(status_code=400, detail=f"Unknown strategies: {bad_strats}")

    new_results = []
    for strategy_code in req.strategies:
        label, factory = STRATEGY_MAP[strategy_code]
        for seed in req.seeds:
            run_id = f"{req.scenario}-{strategy_code}-s{seed}"
            conn = get_conn()
            strategy = factory()
            strategy.label = label
            sc = ScenarioLoader.load(scenario_file)
            sc.random_seed = seed
            t0 = _time.time()
            try:
                engine = SimulationEngine()
                result = engine.run(sc, strategy, conn, run_id=run_id)
                m = result.metrics
                rec = {
                    "run_id": run_id, "scenario": req.scenario,
                    "strategy": strategy_code, "seed": seed,
                    "elapsed_s": round(_time.time() - t0, 2),
                    "avg_search_time_min": m.get("avg_search_time_min"),
                    "avg_wait_time_min": m.get("avg_wait_time_min"),
                    "avg_gate_queue": m.get("avg_gate_queue"),
                    "overflow_events": m.get("overflow_events_count"),
                    "total_vehicles": m.get("total_vehicles"),
                    "successful_park_rate": m.get("successful_park_rate"),
                    "status": "ok",
                }
            except Exception as e:
                rec = {"run_id": run_id, "scenario": req.scenario,
                       "strategy": strategy_code, "seed": seed,
                       "error": str(e), "status": "error"}
            new_results.append(rec)

    # Persist
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_results()
    by_id = {r["run_id"]: r for r in existing}
    for r in new_results:
        by_id[r["run_id"]] = r
    RESULTS_FILE.write_text(json.dumps(list(by_id.values()), indent=2))

    ok = [r for r in new_results if r.get("status") == "ok"]
    err = [r for r in new_results if r.get("status") == "error"]
    return {
        "submitted": len(new_results),
        "ok": len(ok),
        "errors": len(err),
        "results": new_results,
    }
