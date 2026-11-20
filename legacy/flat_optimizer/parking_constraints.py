def is_parking_available(parking_details):

    capacity = parking_details["capacity"]
    occupied = parking_details["occupied"]

    if capacity <= 0:
        raise ValueError(
            "Parking capacity must be greater than 0."
        )

    if occupied < 0:
        raise ValueError(
            "Occupied spaces cannot be negative."
        )

    if occupied > capacity:
        raise ValueError(
            "Occupied spaces cannot exceed capacity."
        )

    return occupied < capacity


def can_accept_assignment(
    parking_details,
    assignment_size=10
):

    capacity = parking_details["capacity"]
    occupied = parking_details["occupied"]

    if not is_parking_available(parking_details):
        return False

    return occupied + assignment_size <= capacity