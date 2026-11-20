from .gate_constraints import is_gate_open
from .parking_constraints import is_parking_available
from .cost_function import (
    calculate_total_cost,
    CostComponents
)
from .congestion_risk import calculate_congestion_risk
from .overflow_risk import calculate_overflow_risk
from .dijkstra import dijkstra
from .weights import WEIGHT_PROFILES


ASSIGNMENT_BLOCK = 10
CURRENT_WEIGHT = 0.60
FORECAST_WEIGHT = 0.40
MAX_ROUTE_COST = 20.0


def get_route_congestion_risk(route, predictions):

    congestion_levels = []

    for node in route:

        if node in predictions:

            congestion_levels.append(
                calculate_congestion_risk(
                    predictions[node]
                )
            )

    if not congestion_levels:
        return 0.0

    return sum(congestion_levels) / len(congestion_levels)


def blend_occupancy(
    current_fills,
    forecast_fills,
    parking_data
):

    blended = {}

    for lot_id, details in parking_data.items():

        capacity = details["capacity"]

        current = current_fills.get(
            lot_id,
            details["occupied"] / capacity
        )

        forecast = forecast_fills.get(
            lot_id,
            current
        )

        blended[lot_id] = (
            CURRENT_WEIGHT * current
            + FORECAST_WEIGHT * forecast
        )

    return blended


def find_best_parking(
    graph,
    predictions,
    parking_data,
    start,
    closed_gates=None,
    forecast_fills=None,
    gate_states=None,
    weight_profile="P1",
    assignment_block=ASSIGNMENT_BLOCK,
):

    if closed_gates is None:
        closed_gates = []

    if forecast_fills is None:
        forecast_fills = {}

    if gate_states is None:
        gate_states = {}

    if weight_profile not in WEIGHT_PROFILES:
        weight_profile = "P1"

    # 1. Validate entry gate

    if not is_gate_open(start, closed_gates):
        return None

    # 2. Retrieve current occupancy

    current_fills = {}

    for lot_id, details in parking_data.items():

        capacity = details["capacity"]

        if capacity <= 0:
            continue

        current_fills[lot_id] = (
            details["occupied"] / capacity
        )

    # 3. Blend current and predicted occupancy

    blended = blend_occupancy(
        current_fills,
        forecast_fills,
        parking_data
    )

    # 4. Calculate average occupancy

    average_fill = (
        sum(blended.values()) / len(blended)
        if blended else 0.0
    )

    # 5. Calculate system-level gate costs

    costs = CostComponents()

    waiting_cost = costs.waiting_time_cost(
        gate_states
    )

    queue_cost = costs.queue_cost(
        gate_states
    )

    # 6. Evaluate all feasible parking lots

    candidates = []

    for parking_name, details in parking_data.items():

        occupied = details["occupied"]
        capacity = details["capacity"]

        if not is_parking_available(details):
            continue

        # Ensure space exists for the assignment block

        if occupied + assignment_block > capacity:
            continue

        # Calculate actual shortest route

        route, route_cost = dijkstra(
            graph,
            start,
            parking_name
        )

        if not route:
            continue

        # Calculate route congestion

        route_congestion = get_route_congestion_risk(
            [node for node in route if node not in parking_data],
            predictions
        )

        # Simulate assigning 10 vehicles

        projected = blended.copy()

        projected[parking_name] = min(
            1.0,
            blended[parking_name]
            + assignment_block / capacity
        )

        # Calculate projected occupancy for candidate

        projected_fill = projected[parking_name]

        # Calculate distance and search difficulty

        travel_cost = costs.search_travel_cost(
            route_cost,
            average_fill,
            MAX_ROUTE_COST
        )

        # Calculate utilization imbalance

        utilization_cost = costs.utilization_imbalance(
            projected
        )

        # Calculate overflow penalty

        overflow_cost = costs.overflow_penalty(
            projected
        )

        # Calculate total weighted objective

        objective = calculate_total_cost(
            waiting_cost,
            queue_cost,
            travel_cost,
            utilization_cost,
            overflow_cost,
            weight_profile
        )

        # Calculate individual overflow risk

        overflow_risk = calculate_overflow_risk(
            occupied + assignment_block,
            capacity
        )

        option = {
            "parking": parking_name,
            "route": route,
            "route_cost": route_cost,
            "congestion_risk": route_congestion,
            "overflow_risk": overflow_risk,
            "projected_fill_rate": projected_fill,
            "waiting_cost": waiting_cost,
            "queue_cost": queue_cost,
            "travel_cost": travel_cost,
            "utilization_cost": utilization_cost,
            "overflow_cost": overflow_cost,
            "total_cost": objective["total_cost"],
            "components": objective["components"],
            "weights": objective["weights"],
            "recommended": projected_fill < 0.70
        }

        candidates.append(option)

    # 7. Rank candidates by lowest objective score

    candidates.sort(
        key=lambda x: (
            x["total_cost"],
            x["projected_fill_rate"]
        )
    )

    # 8. Assign ranks

    for index, candidate in enumerate(candidates):

        candidate["rank"] = index + 1

    # 9. Return best candidate and alternatives

    if not candidates:
        return None

    best_option = candidates[0]

    best_option["alternatives"] = candidates[1:]

    return best_option
