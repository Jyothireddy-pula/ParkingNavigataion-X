"""
Demo allocation strategies: B3 (Prediction-Only) and P (ParkingNav-X Full).
These are the advanced strategies that go beyond the B1/B2 baselines in strategy.py.
"""

import networkx as nx

from digital_twin.simulation.strategy import AllocationStrategy, AssignmentResult
from legacy.flat_optimizer.runtime import graph_to_adjacency
from legacy.flat_optimizer.parking_optimizer import find_best_parking

# J-function weight profiles (same as optimization/engine.py WEIGHT_PROFILES)
# w3 = search/travel, w5 = overflow penalty, w1/w2 = queue/waiting costs
_J_WEIGHT_PROFILES = {
    "P1": {"w1": 0.40, "w2": 0.30, "w3": 0.15, "w4": 0.05, "w5": 0.10},  # minimize waiting
    "P2": {"w1": 0.20, "w2": 0.20, "w3": 0.20, "w4": 0.20, "w5": 0.20},  # balanced
    "P3": {"w1": 0.10, "w2": 0.10, "w3": 0.20, "w4": 0.10, "w5": 0.50},  # minimize overflow
}


class PredictionOnlyStrategy(AllocationStrategy):
    """B3 — Prediction-aware strategy.

    Uses predicted occupancy (fill rate) to avoid lots that are likely to
    fill up soon, steering vehicles to lots with headroom.
    """

    label = "B3-PredictionOnly"
    FILL_THRESHOLD = 0.80

    def __init__(self):
        self._forecast = None

    def update_policy(self, state_snapshot: dict, forecast: dict = None) -> None:
        """Keep the latest occupancy prediction supplied by the simulation cycle."""
        self._forecast = forecast or {}

    def assign(self, vehicle, campus_state):
        predicted_fills = (self._forecast or {}).get("lot_fill_rates", {})
        best_lot, best_score = None, float("inf")
        for lot_id, lot in campus_state.parking_lots.items():
            if lot["status"] != "open":
                continue
            cap = lot["usable_capacity"]
            occ = lot["occupied_spaces"]
            if cap <= 0 or occ >= cap:
                continue
            current_fill = occ / cap
            fill_rate = predicted_fills.get(lot_id, current_fill)
            if fill_rate > self.FILL_THRESHOLD:
                continue
            if not nx.has_path(campus_state.graph, vehicle.entry_gate, lot_id):
                continue
            distance = nx.shortest_path_length(
                campus_state.graph, vehicle.entry_gate, lot_id, weight="weight_distance_meters"
            )
            score = distance * (1 + fill_rate)
            if score < best_score:
                best_lot, best_score = lot_id, score
        # Fallback: threshold filtered everything → pick nearest available
        if best_lot is None:
            for lot_id, lot in campus_state.parking_lots.items():
                if lot["status"] != "open" or lot["occupied_spaces"] >= lot["usable_capacity"]:
                    continue
                if not nx.has_path(campus_state.graph, vehicle.entry_gate, lot_id):
                    continue
                distance = nx.shortest_path_length(
                    campus_state.graph, vehicle.entry_gate, lot_id, weight="weight_distance_meters"
                )
                if best_lot is None or distance < best_score:
                    best_lot, best_score = lot_id, distance
        return AssignmentResult(parking_lot_id=best_lot, gate_id=vehicle.entry_gate)


class ParkingNavXFullStrategy(AllocationStrategy):
    """P — ParkingNav-X full system strategy.

    Combines: prediction-aware lot selection + BPR-weighted route cost +
    forecast-driven proactive avoidance + uncertainty hedging.

    Scoring (per lot):
        score = route_cost * (1 + effective_fill)

    route_cost is travel TIME in seconds (use_optimization=True, default) or
    travel DISTANCE in metres (use_optimization=False, ablation baseline).
    Using travel time aligns lot scoring with the actual BPR-weighted routing
    the engine uses, so lots are ranked by what the driver will actually experience.

    Gate queue penalty was previously added here but is constant per vehicle
    (same entry gate for all lots scored in one call) so it cannot change
    lot rankings — it was removed.

    Ablation flags (set via update_policy's ablation_flags kwarg):
    - use_prediction:  if False, use current occupancy only (no forecast)
    - use_optimization: if False, score by distance (metres) not travel time
    - use_uncertainty:  if False, ignore upper conformal interval in scoring
    - proactive:        if False, only avoid lots that are currently ≥ threshold
    """

    label = "P-ParkingNavX"
    # P uses PREDICTED fill, so thresholds match B3's but are applied to forecast not current.
    # This gives proactive avoidance: P skips a lot at current 65% if forecast says it'll hit 80%.
    FILL_SOFT_THRESHOLD = 0.60   # steepen penalty above 60% predicted fill
    FILL_HARD_THRESHOLD = 0.80   # hard-skip when PREDICTED fill ≥ 80% (B3 uses current fill)

    def __init__(self, weight_profile: str = "P1"):
        self._forecast = None
        self._use_prediction = True
        self._use_optimization = True
        self._use_uncertainty = True
        self._proactive = True
        self._weights = _J_WEIGHT_PROFILES.get(weight_profile, _J_WEIGHT_PROFILES["P2"])
        self._weight_profile = weight_profile

    def update_policy(self, state_snapshot: dict, forecast: dict = None,
                      ablation_flags: dict = None, weight_profile: str = None) -> None:
        """Called every 5 min by simulation engine."""
        self._forecast = forecast
        if ablation_flags is not None:
            self._use_prediction = ablation_flags.get("use_prediction", True)
            self._use_optimization = ablation_flags.get("use_optimization", True)
            self._use_uncertainty = ablation_flags.get("use_uncertainty", True)
            self._proactive = ablation_flags.get("proactive", True)
        if weight_profile is not None:
            self._weights = _J_WEIGHT_PROFILES.get(weight_profile, self._weights)
            self._weight_profile = weight_profile

    def assign(self, vehicle, campus_state):
        # ── Routing weight: travel time (default) or distance (ablation) ──────
        # use_optimization=True  → weight_time_seconds: aligns scoring with BPR routing.
        # use_optimization=False → weight_distance_meters: ablation baseline (how B3 scores).
        # Previously, use_optimization added a gate-queue penalty that was constant
        # per vehicle (same entry gate for all lots scored in one call) so it had
        # zero effect on lot rankings and was removed.
        routing_weight = "weight_time_seconds" if self._use_optimization else "weight_distance_meters"
        route_scale = 60.0 if self._use_optimization else 100.0

        # ── Forecast fill rates ───────────────────────────────────────────────
        if self._use_prediction and self._forecast and "lot_fill_rates" in self._forecast:
            legacy_forecast = dict(self._forecast.get("lot_fill_rates", {}))
            if self._use_uncertainty:
                upper = self._forecast.get("lot_fill_rates_upper", {})
                legacy_forecast = {key: max(value, upper.get(key, value))
                                   for key, value in legacy_forecast.items()}
        else:
            legacy_forecast = {}
        if not self._proactive:
            legacy_forecast = {}
        graph_data = graph_to_adjacency(campus_state.graph, weight=routing_weight)
        graph_data = {
            node: {neighbor: cost / route_scale for neighbor, cost in edges.items()}
            for node, edges in graph_data.items()
        }
        parking_data = {
            lot_id: {"capacity": lot.get("usable_capacity", 0),
                     "occupied": lot.get("occupied_spaces", 0)}
            for lot_id, lot in campus_state.parking_lots.items()
            if lot.get("status") == "open"
        }
        gates = {
            gate_id: {"capacity": gate.get("capacity", 5),
                      "queue_length": gate.get("queue_length", 0)}
            for gate_id, gate in campus_state.gates.items()
            if gate.get("status") == "open"
        }
        road_congestion = {}
        for source, destination, edge in campus_state.graph.edges(data=True):
            road = campus_state.roads.get(edge.get("road_id"), {})
            capacity = road.get("capacity", 0)
            load = road.get("current_load", 0)
            if capacity:
                road_congestion[destination] = min(1.0, max(0.0, load / capacity))
        option = find_best_parking(
            graph_data, road_congestion, parking_data, vehicle.entry_gate,
            closed_gates=[gate_id for gate_id, gate in campus_state.gates.items()
                          if gate.get("status") != "open"],
            forecast_fills=legacy_forecast, gate_states=gates,
            weight_profile=getattr(self, "_weight_profile", "P1"),
            assignment_block=1,
        )
        return AssignmentResult(
            parking_lot_id=option["parking"] if option else None,
            gate_id=vehicle.entry_gate,
            route=option["route"] if option else [],
        )

        if self._use_prediction and self._forecast and "lot_fill_rates" in self._forecast:
            predicted_fills = self._forecast["lot_fill_rates"]
            upper_fills = self._forecast.get("lot_fill_rates_upper", {})
        else:
            predicted_fills = {}
            upper_fills = {}

        best_lot, best_score = None, float("inf")

        for lot_id, lot in campus_state.parking_lots.items():
            if lot["status"] != "open":
                continue
            cap = lot["usable_capacity"]
            occ = lot["occupied_spaces"]
            if cap <= 0 or occ >= cap:
                continue

            if not nx.has_path(campus_state.graph, vehicle.entry_gate, lot_id):
                continue

            current_fill = occ / cap

            # ── Effective fill for proactive / reactive modes ─────────────────
            if self._proactive and self._use_prediction:
                # Use max(predicted, current * 0.9) so P can benefit from
                # improving lots (departures reducing fill) while remaining
                # conservative. The 0.9 floor prevents chasing lots that are
                # genuinely full but transiently mispredicted as improving.
                predicted_fill = max(
                    predicted_fills.get(lot_id, current_fill),
                    current_fill * 0.9,
                )
                # Widen with upper conformal interval for SCORING only.
                # The hard-skip uses predicted_fill (not the widened value) so
                # that wide conformal intervals don't cause every lot to be
                # skipped and force a fallback to B2.
                if self._use_uncertainty:
                    effective_fill = max(predicted_fill,
                                         upper_fills.get(lot_id, predicted_fill))
                else:
                    effective_fill = predicted_fill
            else:
                predicted_fill = current_fill
                effective_fill = current_fill

            # Hard skip: based on predicted fill (not uncertainty-widened score)
            # so wide conformal bounds never exclude every lot from consideration.
            if predicted_fill >= self.FILL_HARD_THRESHOLD:
                continue

            # ── Route cost from vehicle's actual entry gate ───────────────────
            # Scored by travel time (seconds) so lot ranking reflects actual
            # BPR-weighted route quality, not just physical proximity.
            route_cost = nx.shortest_path_length(
                campus_state.graph, vehicle.entry_gate, lot_id,
                weight=routing_weight
            )

            # ── Composite score: route_cost × fill_multiplier ─────────────────
            # Identical structure to B3 but using PREDICTED fill (not current)
            # and BPR travel time (not raw distance) as the base cost.
            # Above FILL_SOFT_THRESHOLD: multiplier steepens to penalise congested routing.
            if effective_fill <= self.FILL_SOFT_THRESHOLD:
                fill_multiplier = 1.0 + effective_fill
            else:
                fill_multiplier = 1.0 + self.FILL_SOFT_THRESHOLD + (
                    (effective_fill - self.FILL_SOFT_THRESHOLD) * 3.0
                )

            score = route_cost * fill_multiplier

            if score < best_score:
                best_lot, best_score = lot_id, score

        # ── Fallback: FILL_HARD_THRESHOLD filtered everything ─────────────────
        if best_lot is None:
            for lot_id, lot in campus_state.parking_lots.items():
                if lot["status"] != "open" or lot["occupied_spaces"] >= lot["usable_capacity"]:
                    continue
                if not nx.has_path(campus_state.graph, vehicle.entry_gate, lot_id):
                    continue
                route_cost = nx.shortest_path_length(
                    campus_state.graph, vehicle.entry_gate, lot_id, weight=routing_weight
                )
                if best_lot is None or route_cost < best_score:
                    best_lot, best_score = lot_id, route_cost

        return AssignmentResult(parking_lot_id=best_lot, gate_id=vehicle.entry_gate)
