"""
Run high-congestion (HC) experiments: HC1–HC5 × B1/B2/B3/P × N seeds.

Outputs:
    experiments/results/hc_results.json
    experiments/results/hc_summary.json
    experiments/results/hc_summary_table.md

Usage:
    python scripts/run_hc_experiments.py [--seeds 30] [--workers 4]
"""

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import sys
sys.path.insert(0, str(ROOT))

from digital_twin.db import get_connection, apply_migrations
from digital_twin.config_loader import load_campus_config
from digital_twin.simulation.engine import SimulationEngine
from digital_twin.simulation.scenario import ScenarioLoader
from digital_twin.simulation.strategy import FirstAvailableStrategy, NearestAvailableStrategy
from digital_twin.simulation.demo_strategies import PredictionOnlyStrategy, ParkingNavXFullStrategy

CAMPUS_YAML = ROOT / "configs" / "campus" / "vitap.yaml"
SCENARIO_DIR = ROOT / "configs" / "scenarios" / "vitap"
RESULTS_DIR  = ROOT / "experiments" / "results"
RUNS_DIR     = ROOT / "experiments" / "runs"

HC_SCENARIOS = [
    # (yaml_stem, [strategies])
    ("HC1_high_congestion_85", ["B1", "B2", "B3", "P"]),
    ("HC2_high_congestion_90", ["B1", "B2", "B3", "P"]),
    ("HC3_high_congestion_95", ["B1", "B2", "B3", "P"]),
    ("HC4_lot_closure_high",   ["B1", "B2", "B3", "P"]),
    ("HC5_event_high",         ["B1", "B2", "B3", "P"]),
]


def _make_strategy(code):
    LABELS = {
        "B1": ("B1", FirstAvailableStrategy),
        "B2": ("B2", NearestAvailableStrategy),
        "B3": ("B3", PredictionOnlyStrategy),
        "P":  ("P",  ParkingNavXFullStrategy),
    }
    label, cls = LABELS[code]
    s = cls()
    s.label = label
    return s


def get_conn():
    conn = get_connection(":memory:")
    apply_migrations(conn)
    load_campus_config(CAMPUS_YAML, conn)
    return conn


def run_one(scenario_stem, strategy_code, seed):
    scenario_yaml = SCENARIO_DIR / f"{scenario_stem}.yaml"
    raw = __import__("yaml", fromlist=["safe_load"]).safe_load(scenario_yaml.open())
    raw["random_seed"] = seed
    scenario = ScenarioLoader.from_dict(raw)
    conn = get_conn()
    engine = SimulationEngine(runs_dir=str(RUNS_DIR))
    strategy = _make_strategy(strategy_code)
    result = engine.run(scenario, strategy, conn)
    m = result.metrics
    return {
        "scenario": scenario_stem,
        "strategy": strategy_code,
        "seed": seed,
        "run_id": result.run_id,
        "metrics": {
            "avg_search_time_min": m.get("avg_search_time_min", 0.0),
            "avg_wait_time_min": m.get("avg_wait_time_min", 0.0),
            "overflow_events_count": m.get("overflow_events_count", 0),
            "successful_park_rate": m.get("successful_park_rate", 0.0),
            "avg_gate_queue": m.get("avg_gate_queue", 0.0),
            "total_travel_distance_m": m.get("total_travel_distance_m", 0.0),
            "total_reassignments": m.get("total_reassignments", 0),
        },
        "secondary": {
            "avg_utilization_pct": result.secondary_metrics.get("avg_utilization_pct", 0.0),
            "total_vehicles": result.secondary_metrics.get("total_vehicles_simulated", 0),
        },
    }


def _t_ci(values, confidence=0.95):
    n = len(values)
    if n < 2:
        return 0.0
    import math
    mean = statistics.mean(values)
    std = statistics.stdev(values)
    # t-critical for df=n-1, 95% CI (approximation for n≥5)
    t_crit = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57,
               9: 2.26, 14: 2.14, 19: 2.09, 29: 2.04, 59: 2.00, 999: 1.96}
    df = n - 1
    tc = next((v for k, v in sorted(t_crit.items()) if df <= k), 1.96)
    return tc * std / math.sqrt(n)


def summarise(all_results):
    from collections import defaultdict
    groups = defaultdict(list)
    for r in all_results:
        if "error" not in r:
            key = f"{r['scenario']}|{r['strategy']}"
            groups[key].append(r)

    summary = {}
    for key, runs in groups.items():
        sc, st = key.split("|", 1)
        for metric_name in ["avg_search_time_min", "avg_wait_time_min",
                             "overflow_events_count", "successful_park_rate",
                             "avg_utilization_pct"]:
            vals = []
            for r in runs:
                v = r["metrics"].get(metric_name) or r.get("secondary", {}).get(metric_name)
                if v is not None:
                    vals.append(float(v))
            if not vals:
                continue
            summary.setdefault(key, {
                "scenario": sc, "strategy": st, "n_runs": len(runs), "metrics": {}
            })
            summary[key]["metrics"][metric_name] = {
                "mean": round(statistics.mean(vals), 4),
                "std":  round(statistics.stdev(vals) if len(vals) > 1 else 0.0, 4),
                "ci_95": round(_t_ci(vals), 4),
                "n": len(vals),
            }
    return summary


def print_table(summary):
    rows = []
    for key, s in sorted(summary.items()):
        m = s["metrics"]
        rows.append({
            "key": key,
            "scenario": s["scenario"],
            "strategy": s["strategy"],
            "n": s["n_runs"],
            "search": m.get("avg_search_time_min", {}).get("mean", 0),
            "overflow": m.get("overflow_events_count", {}).get("mean", 0),
            "park_rate": m.get("successful_park_rate", {}).get("mean", 0),
            "util": m.get("avg_utilization_pct", {}).get("mean", 0),
        })

    print(f"\n{'Scenario':<30} {'Strategy':<6} {'N':>4}  {'AvgSearch(min)':>14}  {'Overflow/run':>12}  {'ParkRate':>8}  {'AvgUtil%':>8}")
    print("-" * 90)
    for r in rows:
        print(f"{r['scenario']:<30} {r['strategy']:<6} {r['n']:>4}  "
              f"{r['search']:>14.3f}  {r['overflow']:>12.1f}  {r['park_rate']:>8.4f}  {r['util']:>8.1f}%")

    # Mark strategy that achieves best (lowest) avg_search_time_min per scenario
    per_scenario = {}
    for r in rows:
        sc = r["scenario"]
        per_scenario.setdefault(sc, []).append(r)
    print("\nBest strategy by avg_search_time_min per scenario:")
    for sc, sc_rows in sorted(per_scenario.items()):
        best = min(sc_rows, key=lambda x: x["search"])
        print(f"  {sc:<30}: {best['strategy']} ({best['search']:.3f} min)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=10,
                        help="Seeds per cell (default 10; use 30 for publication)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--scenarios", nargs="*",
                        help="Run specific scenarios only (default: all HC)")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    results_path = RESULTS_DIR / "hc_results.json"

    scenarios = HC_SCENARIOS
    if args.scenarios:
        scenarios = [(s, st) for s, st in HC_SCENARIOS if s in args.scenarios]

    tasks = []
    for scenario_stem, strategies in scenarios:
        for strategy_code in strategies:
            for seed in range(args.seeds):
                tasks.append((scenario_stem, strategy_code, seed))

    total = len(tasks)
    print(f"HC experiment: {total} runs ({args.seeds} seeds × {len(strategies)} strategies × {len(scenarios)} scenarios)")
    print(f"Workers: {args.workers}\n")

    all_results = []
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_one, sc, st, seed): (sc, st, seed) for sc, st, seed in tasks}
        for fut in as_completed(futs):
            sc, st, seed = futs[fut]
            done += 1
            try:
                r = fut.result()
                all_results.append(r)
                elapsed = time.time() - t0
                eta = elapsed / done * (total - done)
                print(f"[{done}/{total}] {sc}|{st} seed={seed} "
                      f"search={r['metrics']['avg_search_time_min']:.3f}min "
                      f"overflow={r['metrics']['overflow_events_count']} "
                      f"util={r['secondary']['avg_utilization_pct']:.1f}% "
                      f"ETA {eta/60:.0f}min")
            except Exception as e:
                print(f"[{done}/{total}] ERROR {sc}|{st} seed={seed}: {e}")
                all_results.append({"scenario": sc, "strategy": st, "seed": seed, "error": str(e)})

    results_path.write_text(json.dumps(all_results, indent=2))
    print(f"\nSaved {len(all_results)} results to {results_path}")

    summary = summarise(all_results)
    (RESULTS_DIR / "hc_summary.json").write_text(json.dumps(summary, indent=2))
    print_table(summary)

    # Markdown summary
    md_lines = ["# HC Experiment Results\n",
                f"Seeds: {args.seeds}  Workers: {args.workers}\n",
                "| Scenario | Strategy | N | AvgSearch(min) | Overflow/run | ParkRate | AvgUtil% |",
                "|----------|----------|---|----------------|-------------|----------|----------|"]
    for key, s in sorted(summary.items()):
        m = s["metrics"]
        search = m.get("avg_search_time_min", {})
        overflow = m.get("overflow_events_count", {})
        park = m.get("successful_park_rate", {})
        util = m.get("avg_utilization_pct", {})
        md_lines.append(
            f"| {s['scenario']} | {s['strategy']} | {s['n_runs']} "
            f"| {search.get('mean',0):.3f} ± {search.get('ci_95',0):.3f} "
            f"| {overflow.get('mean',0):.1f} "
            f"| {park.get('mean',0):.4f} "
            f"| {util.get('mean',0):.1f}% |"
        )
    (RESULTS_DIR / "hc_summary_table.md").write_text("\n".join(md_lines))
    print(f"\nMarkdown table: {RESULTS_DIR / 'hc_summary_table.md'}")


if __name__ == "__main__":
    main()
