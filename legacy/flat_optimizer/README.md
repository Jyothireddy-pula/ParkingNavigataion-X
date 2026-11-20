# ParkingNav-X optimizer

This folder owns the active optimizer implementation.

- `active_engine.py` provides database-backed recommendations and experiment
  scoring used by the project API.
- `parking_optimizer.py` provides the original graph and occupancy based
  multi-objective `find_best_parking` scorer.
- `runtime.py` adapts the main project's campus graph and simulation state to
  the flat optimizer inputs.
- `main.py` runs the standalone VIT-AP example using the repository campus
  configuration and latest per-lot occupancy observations.

Run the example from the repository root:

```powershell
python -m legacy.flat_optimizer.main
```
