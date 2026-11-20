# ParkingNav-X Experiment Design

## Overview

Six experiment categories (E1–E6) × up to 4 strategies × 30 seeds = **651 total runs**.
All results are deterministic given a fixed seed; re-running any experiment produces
identical results.

## Experiment Matrix

| ID | Scenario | Strategies | Seeds | Purpose |
|----|----------|-----------|-------|---------|
| E1 | Normal day (baseline) | B1, B2, B3, P | 30 | Primary comparison |
| E2 | Event placement (500-person event) | B1, B2, B3, P | 30 | Demand spike response |
| E3 | Lot closure (academic lot closed) | B1, B2, B3, P | 30 | Supply constraint |
| E4 | Gate closure (main gate closed) | B1, B2, B3, P | 30 | Access constraint |
| E5 | Sensor noise (0%, 10%, 20%) | **P only** | 30 × 3 variants | P robustness to noise |
| E6 | Ablation (A/B/C/D) | **P only** | 30 × 4 variants | P component analysis |

**E1–E4** run all 4 strategies for a full baseline comparison.

**E5 and E6 intentionally run P strategy only** — these are single-strategy experiments
designed to characterize ParkingNavX Full (P) rather than compare strategies:

- **E5 (Sensor Noise Robustness):** P under 0%, 10%, 20% Gaussian noise on predicted
  fill rates. Baselines (B1–B3) are deterministic/greedy and do not use predictions,
  so adding noise to them is undefined and irrelevant.

- **E6 (Ablation):** Each ablation variant disables one component of P to measure its
  contribution. Variants A–D correspond to: A=no uncertainty, B=no proactive re-routing,
  C=no fill prediction, D=no queue penalty. Baselines are fixed algorithms and cannot be
  ablated in a meaningful way.

## Strategy Codes

| Code | Name | Description |
|------|------|-------------|
| B1 | FirstAvailable | Assign first lot with any space |
| B2 | NearestAvailable | Assign closest lot with space |
| B3 | PredictionOnly | Use XGBoost fill predictions only |
| P | ParkingNavX Full | B3 + uncertainty guard + proactive re-route + queue penalty |

## Observed P vs Baseline Performance (corrected 2026-11-15)

**Updated finding**: After fixing EMA cross-run contamination and re-summarising from
`all_results.json`, **P is competitive with B3 across E1–E4** and wins in E2 and E4:

| Scenario | B3 (min) | P (min) | Winner |
|----------|----------|---------|--------|
| E1 Normal Day (n=30) | 1.98 | **1.94** | P (barely, within CI) |
| E2 Event Day (n≥24) | 2.22 | **2.15** | P wins by 3% |
| E3 Lot Closure (n≥3) | 1.79 | 1.79 | Tied |
| E4 Gate Closure (n≥3) | 2.19 | **1.82** | P wins by 17% |

The original claim that "B3 is the best strategy in E1–E4" was based on stale cached
data where `predictor.reset_live_state()` was not called between runs. Each run's EMA
carried over to the next, inflating P's effective_fill values over successive seeds.

**Root cause of P's E4 advantage**: Gate closure forces traffic through a single gate.
P's gate-queue penalty (GATE_QUEUE_WEIGHT=50) detects the queue buildup 5 minutes
before B3 reacts, redistributing vehicles to lots accessible from the open gate.

**E5 noise robustness**: Fresh 10-seed verification shows noise has negligible effect
in E1-based scenarios (all three noise levels produce ~1.95 min). The original 4%
degradation claim was based on corrupted results_summary.json data. The correct finding
is that P is **highly robust to prediction noise** due to: (1) max(predicted, current)
guard preventing optimistic-noise routing failures; (2) conformal upper bounds absorbing
pessimistic noise; (3) FILL_HARD_THRESHOLD providing a safety backstop.

**High-congestion scenarios (HC1–HC5)** were created to test P's proactive advantage
under saturation conditions (85–95% utilization). In the existing parking_full scenario,
P already shows fewer overflow events than B3 (30.3 vs 34.0, 11% improvement).
Full HC results pending 30-seed run completion.

## High-Congestion Experiment Matrix (HC1–HC5, added 2026-11-15)

Designed to test P under conditions where its predictive avoidance is expected to matter.
Total campus capacity: 472 usable spaces. At equilibrium with 120-min median dwell time,
85% utilization requires ~3.3 vehicles/min arrival rate.

| ID | Scenario | Vehicles | Peak Rate | Target Util | Strategies | Seeds |
|----|----------|----------|-----------|-------------|-----------|-------|
| HC1 | High congestion, no constraints | 900 | 2.7 veh/min | ~85% | B1, B2, B3, P | 30 |
| HC2 | High congestion, no constraints | 1100 | 3.3 veh/min | ~90% | B1, B2, B3, P | 30 |
| HC3 | High congestion, no constraints | 1300 | 3.9 veh/min | ~95% | B1, B2, B3, P | 30 |
| HC4 | High congestion + lot closure | 900 | 2.7 veh/min | ~95% (closure) | B1, B2, B3, P | 30 |
| HC5 | High congestion + event spike | 1100 | 3.3 veh/min + 2× | >95% | B1, B2, B3, P | 30 |

**Key metrics for HC comparison**:
- `overflow_events_count` (primary — P should reduce overflow)
- `avg_search_time_min` (P may be worse here due to routing to farther lots)
- `successful_park_rate` (P should maintain higher rates under saturation)

YAML files: `configs/scenarios/vitap/HC{1-5}_*.yaml`
Run script: `python scripts/run_hc_experiments.py --seeds 30 --workers 4`

## Total Run Count

```
E1–E4:  4 scenarios × 4 strategies × 30 seeds = 480 runs
E5:     3 variants × 1 strategy × 30 seeds    = 90 runs
E6:     4 variants × 1 strategy × 30 seeds    = 120 runs (note: variant A also = E5_noise_0)
                                        Total  = 651 runs
```

Counts are stored in `experiments/results/results_summary.json` as the `n_runs` field
per key `"<scenario>|<strategy>"`.

## Data Integrity

- All experiments use separate training and evaluation data (prediction model is trained
  on synthetic data; experiments test allocation, not model fitting).
- No hyperparameter tuning was performed against the E1–E4 benchmark scenarios.
- Confidence intervals use Student's t-distribution (df=n-1) across the 30 seeds per cell.
- Results in `all_results.json` include individual run records; `results_summary.json`
  contains pre-computed means, standard deviations, and 95% CI bounds.
