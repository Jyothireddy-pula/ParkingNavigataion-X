def calculate_overflow_risk(occupied, capacity):

    if capacity <= 0:
        raise ValueError(
            "Parking capacity must be greater than 0."
        )

    projected_fill = occupied / capacity

    return min(1.0, max(0.0, projected_fill))