"""Adapters for using the legacy ParkingNav-X optimizer in the main runtime."""

from __future__ import annotations

from typing import Dict, Optional

import networkx as nx

from .parking_optimizer import find_best_parking


def graph_to_adjacency(graph, *, weight="weight_time_seconds"):
    """Convert an active campus graph to the flat optimizer's adjacency map."""
    adjacency = {node: {} for node in graph.nodes}
    for source, destination, edge in graph.edges(data=True):
        if edge.get("status", "open") != "open" or not edge.get("is_driveable", True):
            continue
        cost = edge.get(weight)
        if cost is None:
            if weight == "weight_time_seconds":
                cost = edge.get("weight", edge.get("expected_travel_time_seconds", 0.0))
            else:
                cost = edge.get("weight_distance_meters", edge.get("length_m", 0.0))
        adjacency[source][destination] = float(cost or 0.0)
    return adjacency


def optimize_state(
    graph,
    parking_lots: Dict,
    gates: Dict,
    start_gate: str,
    *,
    forecast_fills: Optional[Dict[str, float]] = None,
    use_forecast: bool = True,
    weight_profile="P1",
):
    """Run the legacy scoring algorithm against an active simulation state."""
    if start_gate not in gates or gates[start_gate].get("status", "open") != "open":
        return None

    graph_data = graph_to_adjacency(graph)
    parking_data = {
        lot_id: {
            "capacity": int(lot.get("usable_capacity", lot.get("capacity", 0))),
            "occupied": int(lot.get("occupied_spaces", lot.get("occupied", 0))),
        }
        for lot_id, lot in parking_lots.items()
        if lot.get("status", "open") == "open"
    }
    gate_states = {
        gate_id: {
            "capacity": gate.get("capacity", 5),
            "queue_length": gate.get("queue_length", 0),
        }
        for gate_id, gate in gates.items()
        if gate.get("status", "open") == "open"
    }
    best = find_best_parking(
        graph_data,
        {},
        parking_data,
        start_gate,
        closed_gates=[
            gate_id for gate_id, gate in gates.items()
            if gate.get("status", "open") != "open"
        ],
        forecast_fills=forecast_fills if use_forecast else {},
        gate_states=gate_states,
        weight_profile=weight_profile,
    )
    if best is None:
        return None
    best["route"] = best["route"]
    return best


def optimize_networkx(
    graph,
    parking_lots: Dict,
    start_gate: str,
    *,
    gate_states: Optional[Dict] = None,
    forecast_fills: Optional[Dict[str, float]] = None,
    use_forecast: bool = True,
    use_uncertainty: bool = True,
    weight_profile="P1",
    routing_weight="weight_time_seconds",
):
    """Variant for API ranking, allowing each open gate as a candidate start."""
    adjacency = graph_to_adjacency(graph, weight=routing_weight)
    if start_gate:
        starts = [start_gate]
    else:
        starts = sorted(
            node for node, data in graph.nodes(data=True)
            if data.get("type") == "gate" and data.get("status", "open") == "open"
        )
    results = []
    for gate_id in starts:
        best = optimize_state(
            graph,
            parking_lots,
            gate_states or {},
            gate_id,
            forecast_fills=forecast_fills,
            use_forecast=use_forecast,
            weight_profile=weight_profile,
        )
        if best:
            results.extend([best, *best.get("alternatives", [])])
    if not results:
        return []
    by_lot = {}
    for result in results:
        lot_id = result["parking"]
        if lot_id not in by_lot or result["total_cost"] < by_lot[lot_id]["total_cost"]:
            by_lot[lot_id] = result
    ranked = sorted(by_lot.values(), key=lambda item: (item["total_cost"], item["projected_fill_rate"]))
    for index, option in enumerate(ranked, 1):
        option["rank"] = index
    return ranked
