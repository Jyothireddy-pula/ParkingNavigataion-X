import React, { useState, useEffect } from 'react'
import {
  BarChart, Bar, Cell, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer, RadarChart, Radar, PolarGrid,
  PolarAngleAxis, PolarRadiusAxis
} from 'recharts'
import { api } from '../api'

const fmt = (v) => {
  if (v == null || !Number.isFinite(v)) return '—'
  if (v === 0) return '0'
  if (Math.abs(v) >= 100) return v.toFixed(1)
  if (Math.abs(v) >= 10)  return v.toFixed(2)
  if (Math.abs(v) >= 1)   return v.toFixed(3)
  return v.toFixed(4)
}

const STRATEGIES = ['B1', 'B2', 'B3', 'P']
const STRATEGY_COLORS = { B1: '#94a3b8', B2: '#60a5fa', B3: '#a78bfa', P: '#10b981' }
const METRICS = [
  { key: 'avg_search_time_min',  label: 'Avg Search Time (min)', lower: true },
  { key: 'avg_wait_time_min',    label: 'Avg Wait Time (min)',   lower: true },
  { key: 'avg_gate_queue',       label: 'Avg Gate Queue',        lower: true },
  { key: 'overflow_events',      label: 'Overflow Events',       lower: true },
  { key: 'successful_park_rate', label: 'Park Success Rate',     lower: false },
]

export default function ExperimentsDashboard() {
  const [results, setResults] = useState({})
const [loading, setLoading] = useState(false)
const [scenarios, setScenarios] = useState([])
const [selectedScenario, setSelectedScenario] = useState('')
const [selectedMetric, setSelectedMetric] = useState('avg_search_time_min')
const [progress, setProgress] = useState([])
const [scenarioError, setScenarioError] = useState('')

useEffect(() => {
  const loadScenarios = async () => {
    try {
      setScenarioError('')

      const res = await api.getScenarios()
      const availableScenarios = res.data.scenarios ?? []

      setScenarios(availableScenarios)

      if (availableScenarios.length > 0) {
        setSelectedScenario(current =>
          availableScenarios.includes(current)
            ? current
            : availableScenarios[0]
        )
      } else {
        setScenarioError('The API returned no scenarios.')
      }
    } catch (error) {
      setScenarioError(
        error.response?.data?.detail ||
        error.message ||
        'Failed to load scenarios.'
      )
    }
  }

  const loadExistingResults = async () => {
    try {
      const sumRes = await api.getExperimentSummary()
      const summary = sumRes.data
      if (!summary || Object.keys(summary).length === 0) return
      const reconstructed = {}
      Object.values(summary).forEach(item => {
        if (!item.scenario || !item.strategy) return
        if (!reconstructed[item.scenario]) reconstructed[item.scenario] = {}
        const means = {}
        Object.entries(item.metrics || {}).forEach(([metric, stats]) => {
          means[metric] = stats != null ? (stats.mean ?? null) : null
        })
        reconstructed[item.scenario][item.strategy] = means
      })
      if (Object.keys(reconstructed).length > 0) {
        setResults(reconstructed)
        setSelectedScenario(current =>
          reconstructed[current] ? current : Object.keys(reconstructed)[0]
        )
      }
    } catch { /* silent — existing results are optional */ }
  }

  loadScenarios()
  loadExistingResults()
}, [])

  const runExperiments = async () => {
  setLoading(true)
  setProgress(['Loading stored experiment results...'])
  setResults({})

  try {
    const sumRes = await api.getExperimentSummary()
    const summary = sumRes.data
    if (!summary || Object.keys(summary).length === 0) {
      setProgress(['No stored results found. Run experiments via the API first.'])
      return
    }
    const reconstructed = {}
    Object.values(summary).forEach(item => {
      if (!item.scenario || !item.strategy) return
      if (!reconstructed[item.scenario]) reconstructed[item.scenario] = {}
      const means = {}
      Object.entries(item.metrics || {}).forEach(([metric, stats]) => {
        means[metric] = stats != null ? (stats.mean ?? null) : null
      })
      reconstructed[item.scenario][item.strategy] = means
    })
    const loaded = Object.keys(reconstructed)
    setResults(reconstructed)
    setSelectedScenario(current =>
      reconstructed[current] ? current : loaded[0]
    )
    setProgress(loaded.map(s => `✓ ${s}`))
  } catch (error) {
    setProgress([`Error: ${error.response?.data?.detail || error.message || 'Failed to load results'}`])
  } finally {
    setLoading(false)
  }
}

  // Build chart data for selected scenario + selected metric
  const scenarioData = results[selectedScenario] || {}
  const activeMetric = METRICS.find(m => m.key === selectedMetric) || METRICS[0]

  // One bar per strategy for the selected metric
  const chartData = STRATEGIES.map(s => ({
    strategy: s,
    value: scenarioData[s]?.[activeMetric.key] ?? null,
  }))

  // Radar data (normalized) for selected scenario
  const metricMaxes = METRICS.map(m => {
  const values = STRATEGIES
    .map(s => scenarioData[s]?.[m.key])
    .filter(v => typeof v === 'number' && Number.isFinite(v))

  return values.length > 0
    ? Math.max(...values, 1)
    : null
})
  const radarData = STRATEGIES.map(strategy => {
  const point = { strategy }

  METRICS.forEach((m, i) => {
    const val = scenarioData[strategy]?.[m.key]
    const max = metricMaxes[i]

    if (
      typeof val !== 'number' ||
      !Number.isFinite(val) ||
      max === null
    ) {
      point[m.label] = null
    } else {
      // Lower values are better for these metrics.
      point[m.label] = m.lower ? 1 - val / max : val / max
    }
  })

  return point
})

  // Summary table: all scenarios × all strategies
  const hasResults = Object.keys(results).length > 0

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-800">Experiments Dashboard</h2>
          <p className="text-sm text-gray-500">
            Compare available scenarios across the supported strategies.
          </p>
        </div>
        <button
          onClick={runExperiments}
          disabled={loading}
          className="px-4 py-2 bg-vitap-blue text-white rounded-md text-sm font-medium hover:bg-blue-800 disabled:opacity-50"
        >
          {loading ? '⏳ Refreshing…' : '↻ Refresh Results'}
        </button>
      </div>

      {/* Progress log */}
      {progress.length > 0 && (
        <div className="bg-gray-900 text-green-400 rounded-lg p-3 text-xs font-mono space-y-0.5 max-h-32 overflow-y-auto">
          {progress.map((line, i) => <div key={i}>{line}</div>)}
          {loading && <div className="animate-pulse">Loading...</div>}
        </div>
      )}

      {/* Controls */}
      {hasResults && (
        <div className="bg-white rounded-lg shadow p-4">
          <div className="flex gap-4 items-center flex-wrap">
            <div>
              <label className="text-xs font-medium text-gray-600 block mb-1">Scenario</label>
              <select
                value={selectedScenario}
                onChange={e => setSelectedScenario(e.target.value)}
                className="border border-gray-300 rounded px-2 py-1 text-sm"
              >
                {scenarioError && (
                  <p className="text-sm text-red-600" role="alert">
                    {scenarioError}
                  </p>
                )}
                {scenarios
                  .filter(s => results[s])
                  .map(s => (
                    <option key={s} value={s}>
                      {s.replace(/_/g, ' ')}
                    </option>
                  ))}
              </select>
            </div>
            <div>
              <label className="text-xs font-medium text-gray-600 block mb-1">Primary Metric</label>
              <select
                value={selectedMetric}
                onChange={e => setSelectedMetric(e.target.value)}
                className="border border-gray-300 rounded px-2 py-1 text-sm"
              >
                {METRICS.map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
              </select>
            </div>
          </div>
        </div>
      )}

      {hasResults && (
        <>
          {/* Strategy Comparison Bar Chart — selected metric */}
          <div className="bg-white rounded-lg shadow p-4">
            <h3 className="text-sm font-semibold text-gray-700 mb-1">
              Strategy Comparison — {selectedScenario.replace(/_/g, ' ')}
            </h3>
            <p className="text-xs text-gray-400 mb-3">{activeMetric.label} · {activeMetric.lower ? 'lower is better' : 'higher is better'}</p>
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={chartData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="strategy" tick={{ fontSize: 13, fontWeight: 600 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip formatter={(v) => [fmt(v), activeMetric.label]} />
                <Bar dataKey="value" name={activeMetric.label} radius={[4, 4, 0, 0]}>
                  {chartData.map(d => (
                    <Cell key={d.strategy} fill={STRATEGY_COLORS[d.strategy]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* All-metrics summary cards */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            {METRICS.map(m => (
              <div
                key={m.key}
                onClick={() => setSelectedMetric(m.key)}
                className={`bg-white rounded-lg shadow p-3 cursor-pointer border-2 transition-colors ${selectedMetric === m.key ? 'border-vitap-blue' : 'border-transparent hover:border-gray-200'}`}
              >
                <div className="text-xs text-gray-500 mb-2 leading-tight">{m.label}</div>
                {STRATEGIES.map(s => {
                  const val = scenarioData[s]?.[m.key]
                  return (
                    <div key={s} className="flex items-center justify-between text-xs py-0.5">
                      <span className={`font-bold ${s === 'P' ? 'text-green-700' : 'text-gray-500'}`}>{s}</span>
                      <span className={`font-mono ${s === 'P' ? 'text-green-700 font-semibold' : 'text-gray-700'}`}>
                        {fmt(val)}
                      </span>
                    </div>
                  )
                })}
              </div>
            ))}
          </div>

          {/* Summary Table (all scenarios) */}
          <div className="bg-white rounded-lg shadow p-4 overflow-x-auto">
            <h3 className="text-sm font-semibold text-gray-700 mb-3">Results Summary Table</h3>
            <table className="min-w-full text-xs">
              <thead>
                <tr className="border-b bg-gray-50">
                  <th className="text-left py-2 px-3 text-gray-500">Scenario</th>
                  <th className="text-left py-2 px-3 text-gray-500">Strategy</th>
                  <th className="text-right py-2 px-3 text-gray-500">Search (min)</th>
                  <th className="text-right py-2 px-3 text-gray-500">Wait (min)</th>
                  <th className="text-right py-2 px-3 text-gray-500">Avg Queue</th>
                  <th className="text-right py-2 px-3 text-gray-500">Overflow</th>
                  <th className="text-right py-2 px-3 text-green-700 font-semibold">Park Success %</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(results).flatMap(([scenario, stratMap]) =>
                  STRATEGIES.map((s, si) => {
                    const m = stratMap[s] || {}
                    const isParkingNav = s === 'P'
                    return (
                      <tr key={`${scenario}-${s}`} className={`border-b ${isParkingNav ? 'bg-green-50 font-semibold' : ''} ${si === 0 ? 'border-t-2 border-gray-300' : ''}`}>
                        <td className="py-1.5 px-3 text-gray-600">{si === 0 ? scenario.replace(/_/g, ' ') : ''}</td>
                        <td className="py-1.5 px-3">
                          <span className={`px-1.5 py-0.5 rounded text-xs font-bold ${isParkingNav ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-700'}`}>{s}</span>
                        </td>
                        <td className="text-right py-1.5 px-3 font-mono">{fmt(m.avg_search_time_min)}</td>
                        <td className="text-right py-1.5 px-3 font-mono">{fmt(m.avg_wait_time_min)}</td>
                        <td className="text-right py-1.5 px-3 font-mono">{fmt(m.avg_gate_queue)}</td>
                        <td className="text-right py-1.5 px-3 font-mono">{fmt(m.overflow_events)}</td>
                        <td className={`text-right py-1.5 px-3 font-medium ${isParkingNav ? 'text-green-700' : ''}`}>
                          {m.successful_park_rate != null
                            ? `${(m.successful_park_rate * 100).toFixed(1)}%`
                            : '—'}
                        </td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>
        </>
      )}

      {!hasResults && !loading && (
        <div className="text-center py-20 bg-white rounded-lg shadow">
          <div className="text-4xl mb-3">🔬</div>

          <div className="text-gray-600">
            {scenarios.length > 0
              ? 'Run the experiments to view actual strategy comparisons.'
              : 'Waiting for available scenarios from the API.'}
          </div>

          <div className="text-xs text-gray-400 mt-2">
            Missing experiment results are not treated as zero.
          </div>
        </div>
      )}
    </div>
  )
}
