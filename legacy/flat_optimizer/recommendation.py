def create_recommendation(best_option):

    if best_option is None:
        return "No available parking route was found."

    route_text = " → ".join(
        best_option["route"]
    )

    components = best_option["components"]

    message = (
        f"Recommended parking: {best_option['parking']}\n"
        f"Route: {route_text}\n"
        f"Route distance/cost: {best_option['route_cost']}\n"
        f"Overall objective J: {best_option['total_cost']:.4f}\n"
        f"Rank: {best_option['rank']}\n"
        f"Projected occupancy: "
        f"{best_option['projected_fill_rate']:.2%}\n\n"

        f"Objective breakdown:\n"
        f"Waiting time: {components['waiting_time']:.4f}\n"
        f"Queue cost: {components['queue_cost']:.4f}\n"
        f"Search/travel: {components['search_travel']:.4f}\n"
        f"Utilization imbalance: "
        f"{components['utilization_imbalance']:.4f}\n"
        f"Overflow penalty: "
        f"{components['overflow_penalty']:.4f}\n"
    )

    return message