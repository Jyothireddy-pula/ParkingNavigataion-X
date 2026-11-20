WEIGHT_PROFILES = {
    "P1": {
        "w1": 0.40,
        "w2": 0.30,
        "w3": 0.20,
        "w4": 0.05,
        "w5": 0.05
    },

    "P2": {
        "w1": 0.20,
        "w2": 0.20,
        "w3": 0.20,
        "w4": 0.20,
        "w5": 0.20
    },

    "P3": {
        "w1": 0.10,
        "w2": 0.10,
        "w3": 0.10,
        "w4": 0.20,
        "w5": 0.50
    }
}


PROFILE_LABELS = {
    "P1": "Minimize Wait Time",
    "P2": "Balanced",
    "P3": "Minimize Overflow"
}


TERM_NAMES = {
    "w1": "waiting_time",
    "w2": "queue_cost",
    "w3": "search_travel",
    "w4": "utilization_imbalance",
    "w5": "overflow_penalty"
}