# Legacy optimizer

`flat_optimizer/` is now the source of truth for ParkingNav-X optimization.
The database-backed recommendation engine is in `flat_optimizer/active_engine.py`,
and the graph-based implementation is in `flat_optimizer/parking_optimizer.py`.
The simulation and API adapt their campus state to these modules. The standalone
demo reads the VIT-AP graph from `configs/campus/vitap.yaml` and the latest
per-lot occupancy from `data/observations/vitap_observations.csv`.

Run the standalone JSON example from the repository root with:

```powershell
python -m legacy.flat_optimizer.main
```

The compatibility module `digital_twin.optimization.engine` re-exports the
active optimizer for callers that still use the earlier import path.
