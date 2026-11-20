import React, { useEffect, useState } from 'react'
import { api } from '../api'

// ── Constants ──────────────────────────────────────────────────────────────────

const STRATEGIES = ['B1', 'B2', 'B3', 'P']

const STRATEGY_LABELS = {
  B1: 'B1 — First Available',
  B2: 'B2 — Nearest Available',
  B3: 'B3 — Prediction Only',
  P:  'P  — ParkingNav-X (full)',
}

const STRATEGY_COLORS = {
  B1: '#94a3b8',
  B2: '#60a5fa',
  B3: '#a78bfa',
  P:  '#10b981',
}

// All five primary metrics. lower=true means lower values are better.
const METRICS = [
  { key: 'avg_search_time_min',  label: 'Avg Search Time',    unit: 'min',  lower: true  },
  { key: 'avg_wait_time_min',    label: 'Avg Wait Time',       unit: 'min',  lower: true  },
  { key: 'avg_gate_queue',       label: 'Avg Gate Queue',      unit: '',     lower: true  },
  { key: 'overflow_events',      label: 'Overflow Events',     unit: '',     lower: true  },
  { key: 'successful_park_rate', label: 'Park Success Rate',   unit: '%',    lower: false },
]

// ── Helpers ────────────────────────────────────────────────────────────────────

const fmt = (v, unit = '') => {
  if (v == null || !Number.isFinite(v)) return '—'
  let s
  if (v === 0)              s = '0'
  else if (Math.abs(v) >= 100) s = v.toFixed(1)
  else if (Math.abs(v) >= 10)  s = v.toFixed(2)
  else if (Math.abs(v) >= 1)   s = v.toFixed(3)
  else                         s = v.toFixed(4)
  if (unit === '%') return `${(v * 100).toFixed(1)}%`
  return unit ? `${s} ${unit}` : s
}

// Percentage improvement of P over baseline b on metric m.
// Returns null when either value is missing or baseline is 0.
const pctImprovement = (pVal, bVal, lower) => {
  if (pVal == null || bVal == null || !Number.isFinite(pVal) || !Number.isFinite(bVal)) return null
  if (bVal === 0 && pVal === 0) return 0
  if (bVal === 0) return null
  return lower
    ? ((bVal - pVal) / bVal) * 100   // lower-is-better: positive = P is better
    : ((pVal - bVal) / bVal) * 100   // higher-is-better: positive = P is better
}

// Extract means from the experiment summary for one scenario (all 4 strategies).
// Returns { B1: { metric: value, ... }, B2: ..., B3: ..., P: ... } | null
const extractStoredMeans = (summary, scenario) => {
  const out = {}
  for (const s of STRATEGIES) {
    const entry = summary[`${scenario}|${s}`]
    if (!entry) return null   // scenario not in stored results
    const means = {}
    for (const m of METRICS) {
      const stats = entry.metrics?.[m.key]
      means[m.key] = stats != null ? (stats.mean ?? null) : null
    }
    out[s] = means
  }
  return out
}

// Extract n_runs from summary for a scenario (use B1 as reference).
const extractNRuns = (summary, scenario) =>
  summary[`${scenario}|B1`]?.n ?? summary[`${scenario}|B1`]?.metrics?.avg_search_time_min?.n ?? null

// ── Component ──────────────────────────────────────────────────────────────────

export default function ScenarioControls() {
  const [scenarios, setScenarios]           = useState([])
  const [strategies, setStrategies]         = useState([])
  const [selectedScenario, setSelectedScenario] = useState('')
  const [selectedStrategy, setSelectedStrategy] = useState('P')

  // Stored experiment summary (same source as ExperimentsDashboard)
  const [expSummary, setExpSummary]         = useState({})
  const [summaryLoading, setSummaryLoading] = useState(true)

  // Single-strategy live simulation result
  const [simResult, setSimResult]           = useState(null)
  const [simLoading, setSimLoading]         = useState(false)
  const [simError, setSimError]             = useState('')
  const [simSource, setSimSource]           = useState('')  // 'live'

  // All-strategies comparison (live fallback only)
  const [liveCompare, setLiveCompare]       = useState(null)
  const [liveLoading, setLiveLoading]       = useState(false)
  const [liveError, setLiveError]           = useState('')

  const [initError, setInitError]           = useState('')

  // ── On mount: load scenarios, strategies, and stored experiment summary ──────
  useEffect(() => {
    const loadAll = async () => {
      try {
        const [sRes, stRes, sumRes] = await Promise.all([
          api.getScenarios(),
          api.getStrategies(),
          api.getExperimentSummary(),
        ])
        const scens = sRes.data.scenarios || []
        setScenarios(scens)
        if (scens.length > 0) setSelectedScenario(scens[0])
        setStrategies(stRes.data.strategies || [])
        setExpSummary(sumRes.data || {})
      } catch (e) {
        setInitError(`Could not load data: ${e.message}`)
      } finally {
        setSummaryLoading(false)
      }
    }
    loadAll()
  }, [])

  // ── Derived: stored results for the selected scenario ────────────────────────
  const storedData   = selectedScenario ? extractStoredMeans(expSummary, selectedScenario) : null
  const storedNRuns  = selectedScenario ? extractNRuns(expSummary, selectedScenario) : null
  const hasStored    = storedData != null

  // ── Single-strategy run ───────────────────────────────────────────────────────
  const runSimulation = async () => {
    setSimLoading(true)
    setSimResult(null)
    setSimError('')
    setSimSource('live')
    try {
      const res = await api.simulate(selectedScenario, selectedStrategy)
      setSimResult(res.data)
    } catch (e) {
      setSimError(`Simulation failed: ${e.response?.data?.detail || e.message}`)
    } finally {
      setSimLoading(false)
    }
  }

  // ── All-strategies live comparison (only used when no stored data) ────────────
  const runLiveComparison = async () => {
    setLiveLoading(true)
    setLiveCompare(null)
    setLiveError('')
    try {
      const res = await api.compareStrategies(selectedScenario)
      setLiveCompare({ ...res.data, source: 'live' })
    } catch (e) {
      setLiveError(`Comparison failed: ${e.response?.data?.detail || e.message}`)
    } finally {
      setLiveLoading(false)
    }
  }

  // ── Decide which comparison data to show ─────────────────────────────────────
  // Priority: stored experiment > live comparison result
  const activeCmp   = hasStored ? storedData : liveCompare?.comparison
  const cmpSource   = hasStored ? 'stored' : (liveCompare ? 'live' : null)
  const cmpScenario = selectedScenario

  // ── Comparison UI helper ──────────────────────────────────────────────────────
  const ComparisonPanel = ({ cmp, source, scenario, nRuns }) => {
    const pWins = METRICS.filter(m => {
      const vals = STRATEGIES.map(s => cmp[s]?.[m.key]).filter(v => v != null && Number.isFinite(v))
      if (!vals.length) return false
      const best = m.lower ? Math.min(...vals) : Math.max(...vals)
      return cmp['P']?.[m.key] === best
    }).length

    return (
      <div className="bg-white rounded-lg shadow p-5 space-y-4">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-gray-700">
              Strategy Comparison — {scenario.replace(/_/g, ' ')}
            </h3>
            <div className="flex items-center gap-2 mt-0.5">
              {source === 'stored' ? (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
                  📦 Stored Experiment · {nRuns ? `${nRuns} seeds · mean` : 'mean'} ± CI₉₅
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-800">
                  ⚡ Live Simulation · 1 seed · single run
                </span>
              )}
            </div>
          </div>
          <span className="px-3 py-1 bg-green-100 text-green-800 text-xs font-bold rounded-full">
            ParkingNav-X wins {pWins}/{METRICS.length} metrics
          </span>
        </div>

        {/* Per-metric cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {METRICS.map(m => {
            const vals = STRATEGIES.map(s => cmp[s]?.[m.key]).filter(v => v != null && Number.isFinite(v))
            if (!vals.length) return null
            const best  = m.lower ? Math.min(...vals) : Math.max(...vals)
            const worst = m.lower ? Math.max(...vals) : Math.min(...vals)
            const range = worst - best || 1e-9
            const pVal  = cmp['P']?.[m.key]
            const pIsBest = pVal != null && pVal === best

            return (
              <div key={m.key} className={`rounded-lg border p-3 ${pIsBest ? 'border-green-300 bg-green-50' : 'border-gray-200 bg-white'}`}>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-gray-600">
                    {m.label}{m.unit ? ` (${m.unit === '%' ? '%' : m.unit})` : ''}
                  </span>
                  <span className={`text-xs ${m.lower ? 'text-gray-400' : 'text-blue-500'}`}>
                    {m.lower ? '↓ lower better' : '↑ higher better'}
                  </span>
                </div>

                <div className="space-y-1.5">
                  {STRATEGIES.map(s => {
                    const v = cmp[s]?.[m.key]
                    if (v == null || !Number.isFinite(v)) return (
                      <div key={s} className="flex items-center gap-2 opacity-40">
                        <span className="w-6 text-xs font-bold flex-shrink-0 text-gray-400">{s}</span>
                        <div className="flex-1 bg-gray-100 rounded h-5" />
                        <span className="w-16 text-right text-xs font-mono flex-shrink-0 text-gray-400">—</span>
                      </div>
                    )
                    // Bar length: for lower-better, more bar = worse. For higher-better, more bar = better.
                    const pct = m.lower
                      ? ((v - best) / range) * 60 + 15   // worst gets 75%, best gets 15%
                      : ((v - Math.min(...vals)) / range) * 60 + 15
                    const isBest = v === best
                    const isP = s === 'P'
                    const imp = s !== 'P' ? pctImprovement(pVal, v, m.lower) : null

                    return (
                      <div key={s} className="flex items-center gap-2">
                        <span className={`w-6 text-xs font-bold flex-shrink-0 ${isP ? 'text-green-700' : 'text-gray-500'}`}>{s}</span>
                        <div className="flex-1 bg-gray-100 rounded h-5 overflow-hidden">
                          <div
                            className="h-full rounded"
                            style={{ width: `${Math.min(100, Math.max(5, pct))}%`, backgroundColor: STRATEGY_COLORS[s] }}
                          />
                        </div>
                        <span className={`w-20 text-right text-xs font-mono flex-shrink-0 ${isBest ? 'text-green-700 font-bold' : 'text-gray-600'}`}>
                          {fmt(v, m.unit)}
                        </span>
                        {s !== 'P' && imp != null && (
                          <span className={`w-14 text-right text-xs flex-shrink-0 ${imp > 0 ? 'text-green-600' : imp < 0 ? 'text-red-500' : 'text-gray-400'}`}>
                            {imp > 0 ? `↓ ${imp.toFixed(1)}%` : imp < 0 ? `↑ ${Math.abs(imp).toFixed(1)}%` : '—'}
                          </span>
                        )}
                        {s === 'P' && <span className="w-14" />}
                        {isBest && <span className="text-green-600 text-xs">★</span>}
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>

        {/* Footer note */}
        <p className="text-xs text-gray-400">
          {source === 'stored'
            ? `Data: experiments/results/results_summary.json · Experiment ID: ${scenario} · ${nRuns ?? '?'} seeds per strategy`
            : `Data: live simulation · Experiment ID: ${scenario} · 1 seed (not averaged)`
          }
          {' '}· Percentage shown is P improvement over each baseline (positive = P wins)
        </p>
      </div>
    )
  }

  // ── Render ────────────────────────────────────────────────────────────────────
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-bold text-gray-800">Scenario Controls</h2>
        <p className="text-sm text-gray-500">
          Run simulations and compare strategies. Stored experiment results (30 seeds) load instantly.
        </p>
      </div>

      {initError && (
        <div role="alert" className="p-3 rounded border border-red-200 bg-red-50 text-red-700 text-sm">{initError}</div>
      )}

      {/* Controls */}
      <div className="bg-white rounded-lg shadow p-5">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Scenario</label>
            <select
              value={selectedScenario}
              onChange={e => { setSelectedScenario(e.target.value); setSimResult(null); setLiveCompare(null) }}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
            >
              {scenarios.map(s => (
                <option key={s} value={s}>
                  {s.replace(/_/g, ' ')}
                  {expSummary[`${s}|B1`] ? ' ✓' : ''}
                </option>
              ))}
            </select>
            {selectedScenario && (
              <p className="text-xs mt-1">
                {hasStored
                  ? <span className="text-green-700">📦 Stored results available ({storedNRuns} seeds)</span>
                  : summaryLoading
                    ? <span className="text-gray-400">Loading…</span>
                    : <span className="text-amber-600">No stored results — live simulation required</span>
                }
              </p>
            )}
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Strategy (for single run)</label>
            <select
              value={selectedStrategy}
              onChange={e => setSelectedStrategy(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
            >
              {strategies.map(s => (
                <option key={s.code} value={s.code}>{s.code} — {s.name}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap gap-3">
            <button
              onClick={runSimulation}
              disabled={simLoading || !selectedScenario}
              className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
            >
              {simLoading ? '⏳ Running…' : '▶ Run Simulation'}
            </button>

            {/* Compare button: show stored instantly OR trigger live sim */}
            {!hasStored && (
              <button
                onClick={runLiveComparison}
                disabled={liveLoading || !selectedScenario}
                className="px-4 py-2 bg-amber-600 text-white rounded-md text-sm font-medium hover:bg-amber-700 disabled:opacity-50"
              >
                {liveLoading ? '⏳ Running live sim…' : '⚡ Compare All (live sim)'}
              </button>
            )}
          </div>

          {(simLoading || liveLoading) && (
            <p className="text-xs text-amber-600 font-medium">
              ⏳ Running on free hosting — this takes 60–90 seconds, please wait…
            </p>
          )}
          {simError && (
            <div role="alert" className="p-3 rounded border border-red-200 bg-red-50 text-red-700 text-sm">{simError}</div>
          )}
          {liveError && (
            <div role="alert" className="p-3 rounded border border-red-200 bg-red-50 text-red-700 text-sm">{liveError}</div>
          )}
        </div>
      </div>

      {/* Stored comparison — auto-shown when scenario has data */}
      {hasStored && (
        <ComparisonPanel
          cmp={storedData}
          source="stored"
          scenario={cmpScenario}
          nRuns={storedNRuns}
        />
      )}

      {/* Live comparison — only shown when stored data is absent */}
      {!hasStored && liveCompare && (
        <ComparisonPanel
          cmp={liveCompare.comparison}
          source="live"
          scenario={cmpScenario}
          nRuns={null}
        />
      )}

      {/* Single-strategy simulation result */}
      {simResult && (
        <div className="bg-white rounded-lg shadow p-5">
          <div className="flex items-center gap-2 mb-3">
            <h3 className="text-sm font-semibold text-gray-700">
              Single-Run Result — {simResult.scenario?.replace(/_/g, ' ')} / {simResult.strategy}
            </h3>
            <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-800">
              ⚡ Live · 1 seed
            </span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              ['Total Vehicles', simResult.vehicles],
              ['Parked',         simResult.metrics?.parked_vehicles],
              ['Overflow Events',simResult.metrics?.overflow_events],
              ['Search Time',    simResult.metrics?.avg_search_time_min != null ? fmt(simResult.metrics.avg_search_time_min, 'min') : null],
              ['Wait Time',      simResult.metrics?.avg_wait_time_min   != null ? fmt(simResult.metrics.avg_wait_time_min,   'min') : null],
              ['Avg Gate Queue', simResult.metrics?.avg_gate_queue      != null ? fmt(simResult.metrics.avg_gate_queue)            : null],
              ['Peak Gate Queue',simResult.metrics?.peak_gate_queue     != null ? fmt(simResult.metrics.peak_gate_queue)            : null],
              ['Run ID',         simResult.run_id],
            ].map(([label, value]) => (
              <div key={label} className="p-3 bg-gray-50 rounded border border-gray-200">
                <div className="text-xs text-gray-500">{label}</div>
                <div className="text-sm font-bold text-gray-800 font-mono">{value ?? '—'}</div>
              </div>
            ))}
          </div>
          <p className="text-xs text-gray-400 mt-2">
            Single live run · not averaged across seeds · results may differ from stored experiments
          </p>
        </div>
      )}
    </div>
  )
}
