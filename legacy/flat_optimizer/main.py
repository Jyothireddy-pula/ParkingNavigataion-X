"""Run the flat optimizer on the repository's VIT-AP campus data."""

import csv
from pathlib import Path

import yaml

if __package__:
    from .parking_optimizer import find_best_parking
    from .recommendation import create_recommendation
else:  # Support direct-file invocation as well as ``python -m``.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from flat_optimizer.parking_optimizer import find_best_parking
    from flat_optimizer.recommendation import create_recommendation


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "configs" / "campus" / "vitap.yaml"
OBSERVATIONS_PATH = PROJECT_ROOT / "data" / "observations" / "vitap_observations.csv"


def load_vitap_inputs():
    """Build optimizer inputs from VIT-AP configuration and occupancy observations."""
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    graph = {}
    for edge in config.get("edges", []):
        if edge.get("status", "open") != "open" or not edge.get("is_driveable", True):
            continue
        start = edge["from_node_id"]
        end = edge["to_node_id"]
        graph.setdefault(start, {})[end] = float(
            edge.get("weight_time_seconds", edge.get("weight_distance_meters", 0))
        )
        graph.setdefault(end, {})

    parking_data = {
        lot["parking_lot_id"]: {
            "capacity": int(lot["usable_capacity"]),
            "occupied": 0,
        }
        for lot in config.get("parking_lots", [])
        if lot.get("status", "open") == "open" and lot.get("usable_capacity", 0) > 0
    }
    latest_by_lot = {}
    with OBSERVATIONS_PATH.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            lot_id = row.get("parking_lot_id")
            occupied = row.get("occupied_spaces")
            if lot_id not in parking_data or not occupied:
                continue
            timestamp = row.get("timestamp", "")
            if timestamp >= latest_by_lot.get(lot_id, ""):
                capacity = parking_data[lot_id]["capacity"]
                parking_data[lot_id]["occupied"] = min(
                    capacity, max(0, int(float(occupied)))
                )
                latest_by_lot[lot_id] = timestamp

    gate_states = {
        gate["gate_id"]: {"capacity": int(gate["capacity"]), "queue_length": 0}
        for gate in config.get("gates", [])
        if gate.get("status", "open") == "open"
    }
    latest_timestamp = max(latest_by_lot.values(), default=None)
    return graph, parking_data, gate_states, latest_timestamp


def main():
    graph, parking_data, gate_states, latest_timestamp = load_vitap_inputs()
    if not gate_states:
        raise RuntimeError("The VIT-AP campus configuration has no open gates.")

    start_gate = next(iter(gate_states))
    best_option = find_best_parking(
        graph,
        {},  # Road state is not present in the observations CSV.
        parking_data,
        start_gate,
        gate_states=gate_states,
        weight_profile="P1",
    )

    print("\n--- VIT-AP Parking Nav X ---")
    print(f"Campus config: {CONFIG_PATH}")
    print(f"Occupancy observations: {OBSERVATIONS_PATH}")
    print(f"Latest occupancy timestamp: {latest_timestamp or 'none'}")
    print(create_recommendation(best_option))


if __name__ == "__main__":
    main()
