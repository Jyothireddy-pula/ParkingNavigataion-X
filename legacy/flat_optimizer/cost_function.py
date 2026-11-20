from .weights import WEIGHT_PROFILES


class CostComponents:

    @staticmethod
    def waiting_time_cost(gate_states):

        if not gate_states:
            return 0.0

        queues = [
            state.get("queue_length", 0)
            for state in gate_states.values()
        ]

        return min(1.0, max(queues) / 20.0)


    @staticmethod
    def queue_cost(gate_states):

        if not gate_states:
            return 0.0

        scores = []

        for state in gate_states.values():

            capacity = state.get("capacity", 5)
            queue = state.get("queue_length", 0)

            score = min(
                1.0,
                queue / max(1, capacity * 3)
            )

            scores.append(score)

        return sum(scores) / len(scores)


    @staticmethod
    def search_travel_cost(
        route_cost,
        average_fill,
        max_route_cost,
        route_congestion=0.0,
    ):
        if max_route_cost <= 0:
            raise ValueError("Maximum route cost must be positive.")

        normalized_distance = min(1.0, max(0.0, route_cost / max_route_cost))
        normalized_search = min(1.0, max(0.0, average_fill))
        normalized_congestion = min(1.0, max(0.0, route_congestion))
        return (
            0.40 * normalized_distance
            + 0.30 * normalized_search
            + 0.30 * normalized_congestion
        )


    @staticmethod
    def utilization_imbalance(lot_fills):

        if len(lot_fills) < 2:
            return 0.0

        fills = list(lot_fills.values())

        mean = sum(fills) / len(fills)

        variance = sum(
            (fill - mean) ** 2
            for fill in fills
        ) / len(fills)

        return min(1.0, variance * 4.0)


    @staticmethod
    def overflow_penalty(lot_fills):

        if not lot_fills:
            return 0.0

        overflow_count = sum(
            1 for fill in lot_fills.values()
            if fill >= 0.95
        )

        return overflow_count / len(lot_fills)


def calculate_total_cost(
    waiting_cost,
    queue_cost,
    travel_cost,
    utilization_cost,
    overflow_cost,
    weight_profile="P1"
):

    weights = WEIGHT_PROFILES.get(
        weight_profile,
        WEIGHT_PROFILES["P1"]
    )

    components = {
        "waiting_time": waiting_cost,
        "queue_cost": queue_cost,
        "search_travel": travel_cost,
        "utilization_imbalance": utilization_cost,
        "overflow_penalty": overflow_cost
    }

    total_cost = (
        weights["w1"] * waiting_cost
        + weights["w2"] * queue_cost
        + weights["w3"] * travel_cost
        + weights["w4"] * utilization_cost
        + weights["w5"] * overflow_cost
    )

    return {
        "total_cost": total_cost,
        "components": components,
        "weights": weights
    }
