import React, { useEffect, useState } from 'react'
import {
  MapContainer, TileLayer, Marker, Popup, Polyline, Tooltip, useMap,
} from 'react-leaflet'
import L from 'leaflet'
import { api } from '../api'
import { FALLBACK_CENTER, FALLBACK_ZOOM, isCoord, positionNote } from '../mapConfig'
import 'leaflet/dist/leaflet.css'


delete L.Icon.Default.prototype._getIconUrl
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
})

function FitRoute({ points }) {
  const map = useMap()
  const key = JSON.stringify(points)
  useEffect(() => {
    if (points.length >= 2) map.fitBounds(points, { padding: [40, 40], maxZoom: 18 })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, map])
  return null
}

function fillColor(rate) {
  if (rate >= 0.90) return '#ef4444'
  if (rate >= 0.70) return '#f97316'
  if (rate >= 0.50) return '#eab308'
  return '#22c55e'
}

function destIcon() {
  return L.divIcon({
    html: `<div style="width:24px;height:24px;font-size:22px;line-height:24px;text-align:center;">📍</div>`,
    className: '', iconSize: [24, 24], iconAnchor: [12, 24], popupAnchor: [0, -24],
  })
}

function gateIcon() {
  return L.divIcon({
    html: `<div style="width:24px;height:24px;font-size:20px;line-height:24px;text-align:center;">🚧</div>`,
    className: '', iconSize: [24, 24], iconAnchor: [12, 24], popupAnchor: [0, -24],
  })
}

function parkingIcon(rank) {
  const colors = ['#16a34a', '#2563eb', '#9333ea', '#ea580c', '#6b7280']
  const c = colors[Math.min(rank, colors.length - 1)]
  return L.divIcon({
    html: `<div style="background:${c};color:white;border-radius:50%;width:26px;height:26px;display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:13px;box-shadow:0 2px 6px rgba(0,0,0,0.4);">${rank + 1}</div>`,
    className: '', iconAnchor: [13, 13],
  })
}

// Build a Google Maps directions deeplink (no API key needed)
function gmapsDirectionsUrl(fromLat, fromLng, toLat, toLng, mode = 'driving') {
  if (!fromLat || !toLat) return null
  const origin = `${fromLat},${fromLng}`
  const dest = `${toLat},${toLng}`
  return `https://www.google.com/maps/dir/?api=1&origin=${encodeURIComponent(origin)}&destination=${encodeURIComponent(dest)}&travelmode=${mode}`
}


function fmt_m(m) {
  if (m == null) return '—'
  return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${m} m`
}

export default function FindParkingTab({ initialDest }) {
  const [campus, setCampus] = useState(null)
  const [selectedDest, setSelectedDest] = useState('')
  const [selectedGate, setSelectedGate] = useState('vitap-gate-main')
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [selectedOption, setSelectedOption] = useState(0)
  const [showDistRoute, setShowDistRoute] = useState(false)

  useEffect(() => {
    let cancelled = false
    const loadCampus = async () => {
      setError(null)
      try {
        const res = await api.getCampus()
        if (cancelled) return
        setCampus(res.data)
        const available = res.data.destinations || []
        const wanted = initialDest && available.find(d => d.id === initialDest.id)
        setSelectedDest(wanted ? wanted.id : (available[0]?.id ?? ''))
      } catch (e) {
        if (cancelled) return
        setError(e.response?.data?.detail || e.message || 'Failed to load campus data.')
      }
    }
    loadCampus()
    return () => { cancelled = true }
  }, [initialDest])

  const findParking = async () => {
    if (!selectedDest) return
    if (campus && !campus.destinations?.some(d => d.id === selectedDest)) {
      setError('This destination no longer exists. Reload the page to refresh the destination list.')
      return
    }
    setLoading(true)
    setResult(null)
    setError(null)
    try {
      const res = await api.findParking(selectedDest, selectedGate)
      setResult(res.data)
      setSelectedOption(0)
      setShowDistRoute(false)
    } catch (e) {
      setError(
        e.response?.status === 404
          ? 'That destination is no longer available. Reload the page to refresh the destination list.'
          : (e.response?.data?.detail || e.message || 'Failed to find parking')
      )
    } finally {
      setLoading(false)
    }
  }

  const option = result?.options?.[selectedOption]
  const destNode = campus?.destinations?.find(d => d.id === selectedDest)
  const gateNode = campus?.gates?.find(g => g.id === selectedGate)
  const lotNode = option ? campus?.parking_lots?.find(l => l.id === option.lot_id) : null

  const mapCenter = isCoord(destNode) ? [destNode.lat, destNode.lng] : FALLBACK_CENTER

  // Active route geometry (may switch to distance-optimal)
  const activeDriveRoute = showDistRoute && option?.drive_route_shortest_distance
    ? option.drive_route_shortest_distance
    : option?.drive_route
  const activeWalkRoute = showDistRoute && option?.walk_route_shortest_distance
    ? option.walk_route_shortest_distance
    : option?.walk_route

  const driveCoords = activeDriveRoute?.geometry?.map(p => [p.lat, p.lng]) || []
  const walkCoords = activeWalkRoute?.geometry?.map(p => [p.lat, p.lng]) || []
  const allRoutePoints = [...driveCoords, ...walkCoords]


  // Navigation deeplink URLs
  const navDriveUrl = isCoord(gateNode) && isCoord(lotNode)
    ? gmapsDirectionsUrl(gateNode.lat, gateNode.lng, lotNode.lat, lotNode.lng, 'driving')
    : null
  const navWalkUrl = isCoord(lotNode) && isCoord(destNode)
    ? gmapsDirectionsUrl(lotNode.lat, lotNode.lng, destNode.lat, destNode.lng, 'walking')
    : null

  const hasDistAlternative = !!(option?.drive_route_shortest_distance || option?.walk_route_shortest_distance)

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-bold text-gray-800">Find Parking</h2>
        <p className="text-sm text-gray-500">
          Choose your destination and entry gate — ParkingNav-X recommends the best available lot with drive + walk route.
        </p>
      </div>

      {/* Input controls */}
      <div className="bg-white rounded-lg shadow p-5">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Destination</label>
            <select
              value={selectedDest}
              onChange={e => setSelectedDest(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
            >
              {(campus?.destinations || []).map(d => (
                <option key={d.id} value={d.id}>{d.name} ({d.category?.replace(/_/g, ' ')})</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Entry Gate</label>
            <select
              value={selectedGate}
              onChange={e => setSelectedGate(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
            >
              {(campus?.gates || []).map(g => (
                <option key={g.id} value={g.id} disabled={g.status !== 'open'}>
                  {g.name} {g.status !== 'open' ? '(closed)' : ''}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-end">
            <button
              onClick={findParking}
              disabled={loading || !selectedDest}
              className="w-full px-4 py-2 bg-blue-700 text-white rounded-md text-sm font-semibold hover:bg-blue-800 disabled:opacity-50 transition-colors"
            >
              {loading ? '⏳ Finding…' : '🅿️ Find Best Parking'}
            </button>
          </div>
        </div>
      </div>

      {error && (
        <div className="p-4 bg-red-50 border border-red-200 rounded text-red-700 text-sm">⚠️ {error}</div>
      )}

      {result && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Options list */}
          <div className="space-y-3">
            <div className="text-sm font-semibold text-gray-600">
              {result.options_count} options found for <strong>{result.destination_name}</strong>
            </div>
            {result.options.map((opt, idx) => {
              const lotCoords = campus?.parking_lots?.find(l => l.id === opt.lot_id)
              const driveNavUrl = isCoord(gateNode) && isCoord(lotCoords)
                ? gmapsDirectionsUrl(gateNode.lat, gateNode.lng, lotCoords.lat, lotCoords.lng, 'driving')
                : null
              return (
                <div
                  key={opt.lot_id}
                  onClick={() => { setSelectedOption(idx); setShowDistRoute(false) }}
                  className={`w-full text-left p-3 rounded-lg border-2 transition-all cursor-pointer ${
                    idx === selectedOption ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white hover:border-gray-300'
                  }`}
                >
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`w-6 h-6 rounded-full text-white text-xs flex items-center justify-center font-bold ${
                      idx === 0 ? 'bg-green-600' : idx === 1 ? 'bg-blue-600' : 'bg-gray-500'
                    }`}>{idx + 1}</span>
                    <span className="text-sm font-semibold text-gray-800 leading-tight">{opt.lot_name}</span>
                    {opt.is_preferred_for_destination && (
                      <span className="ml-auto text-xs bg-green-100 text-green-700 px-1.5 py-0.5 rounded">⭐ Recommended</span>
                    )}
                  </div>
                  <div className="text-xs text-gray-600 space-y-0.5 ml-8">
                    <div>🚗 {opt.drive_time_minutes} min · {fmt_m(opt.drive_route?.distance_meters)} &nbsp;🚶 {opt.walk_time_minutes} min · {fmt_m(opt.walk_route?.distance_meters)}</div>
                    <div>⏱️ Total {opt.total_time_minutes} min &nbsp;|&nbsp; ~{opt.estimated_available} spaces free</div>
                    <div>
                      Fill:&nbsp;
                      <span className="font-semibold" style={{ color: fillColor(opt.predicted_fill_rate) }}>
                        {opt.predicted_fill_pct}%
                      </span>
                    </div>
                  </div>
                  {/* Navigate button per card */}
                  {driveNavUrl && (
                    <a
                      href={driveNavUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={e => e.stopPropagation()}
                      className="mt-2 ml-8 inline-flex items-center gap-1 text-xs bg-blue-600 text-white px-2 py-1 rounded hover:bg-blue-700"
                    >
                      🗺️ Navigate with Google Maps
                    </a>
                  )}
                </div>
              )
            })}
            <div className="text-xs text-gray-400 italic">
              Routes: Dijkstra shortest-path on campus road graph. Fill rates from XGBoost forecast.
            </div>
          </div>

          {/* Map + detail panel */}
          <div className="lg:col-span-2 space-y-3">
            {destNode && !isCoord(destNode) && (
              <div className="p-2 bg-amber-50 border border-amber-200 rounded text-xs text-amber-800">
                {destNode.name} has no reference coordinates yet, so it is not drawn on the map.
                Lot ranking and walk times still use the routing graph.
              </div>
            )}

            {/* Map */}
            <div className="rounded-lg overflow-hidden border border-gray-200 shadow" style={{ height: 340 }}>
              <MapContainer center={mapCenter} zoom={FALLBACK_ZOOM} style={{ height: '100%', width: '100%' }}>
                  <TileLayer attribution='&copy; OpenStreetMap' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
                  <FitRoute points={allRoutePoints} />

                  {/* Gate marker */}
                  {isCoord(gateNode) && (
                    <Marker position={[gateNode.lat, gateNode.lng]} icon={gateIcon()}>
                      <Tooltip permanent direction="top" offset={[0, -14]}>
                        <span className="text-xs font-medium">🚧 {gateNode.name}</span>
                      </Tooltip>
                      <Popup><strong>🚧 {gateNode.name}</strong><br />Entry gate</Popup>
                    </Marker>
                  )}

                  {/* All parking lot markers */}
                  {result.options.map((opt, idx) => {
                    const lot = campus?.parking_lots?.find(l => l.id === opt.lot_id)
                    if (!lot) return null
                    return (
                      <Marker key={opt.lot_id} position={[lot.lat, lot.lng]} icon={parkingIcon(idx)}>
                        <Tooltip permanent direction="top" offset={[0, -14]}>
                          <span className="text-xs font-medium">{lot.name.replace(' Lot', '').replace(' Parking', '')}</span>
                        </Tooltip>
                        <Popup>
                          <div className="text-sm">
                            <strong>{lot.name}</strong><br />
                            Fill: {opt.predicted_fill_pct}%<br />
                            ~{opt.estimated_available} spaces free<br />
                            Total: {opt.total_time_minutes} min
                          </div>
                        </Popup>
                      </Marker>
                    )
                  })}

                  {/* Destination marker */}
                  {isCoord(destNode) && (
                    <Marker position={[destNode.lat, destNode.lng]} icon={destIcon()}>
                      <Popup>
                        <strong>📍 {destNode.name}</strong><br />
                        {destNode.category?.replace(/_/g, ' ')}<br />
                        <span className="text-xs text-gray-500">{positionNote(destNode)}</span>
                      </Popup>
                    </Marker>
                  )}

                  {/* Drive route — solid blue */}
                  {driveCoords.length >= 2 && (
                    <Polyline positions={driveCoords} pathOptions={{ color: '#2563eb', weight: 5, opacity: 0.85 }}>
                      <Tooltip sticky>
                        <span className="text-xs">🚗 Drive ({activeDriveRoute?.optimization === 'shortest_distance' ? 'Shortest dist' : 'Fastest'}) · {activeDriveRoute?.distance_meters}m · {option?.drive_time_minutes} min</span>
                      </Tooltip>
                    </Polyline>
                  )}

                  {/* Walk route — dashed green */}
                  {walkCoords.length >= 2 && (
                    <Polyline positions={walkCoords} pathOptions={{ color: '#16a34a', weight: 4, opacity: 0.85, dashArray: '8 5' }}>
                      <Tooltip sticky>
                        <span className="text-xs">🚶 Walk ({activeWalkRoute?.optimization === 'shortest_distance' ? 'Shortest dist' : 'Fastest'}) · {activeWalkRoute?.distance_meters}m · {option?.walk_time_minutes} min</span>
                      </Tooltip>
                    </Polyline>
                  )}

                  {/* Map legend */}
                  <div className="leaflet-bottom leaflet-right">
                    <div className="leaflet-control bg-white border border-gray-200 rounded shadow text-xs p-2 m-2 space-y-1">
                      <div className="flex items-center gap-1.5"><span className="w-5 h-1 bg-blue-600 inline-block"></span>Drive route</div>
                      <div className="flex items-center gap-1.5"><span className="w-5 h-0 border-t-2 border-dashed border-green-600 inline-block"></span>Walk route</div>
                      <div className="flex items-center gap-1.5">🚧 Gate &nbsp; 📍 Destination</div>
                    </div>
                  </div>
                </MapContainer>
            </div>

            {/* Navigate buttons */}
            {option && (navDriveUrl || navWalkUrl) && (
              <div className="flex gap-2 flex-wrap">
                {navDriveUrl && (
                  <a
                    href={navDriveUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1.5 px-3 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors"
                  >
                    🗺️ Drive to {option.lot_name}
                  </a>
                )}
                {navWalkUrl && (
                  <a
                    href={navWalkUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1.5 px-3 py-2 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 transition-colors"
                  >
                    🚶 Walk to {result.destination_name}
                  </a>
                )}
              </div>
            )}

            {/* Route detail card */}
            {option && (
              <div className="bg-white rounded-lg border border-gray-200 shadow p-4">
                <div className="flex items-center gap-2 mb-3">
                  <span className="w-7 h-7 rounded-full bg-green-600 text-white text-sm flex items-center justify-center font-bold">
                    {selectedOption + 1}
                  </span>
                  <div>
                    <div className="font-bold text-gray-800">{option.lot_name}</div>
                    <div className="text-xs text-gray-500">Recommended parking for {result.destination_name}</div>
                  </div>
                </div>

                {/* Stats row */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                  {[
                    ['🚗 Drive', `${option.drive_time_minutes} min`, 'text-blue-700'],
                    ['🚶 Walk', `${option.walk_time_minutes} min`, 'text-green-700'],
                    ['⏱️ Total', `${option.total_time_minutes} min`, 'text-gray-700'],
                    ['🅿️ Forecast', `${option.predicted_fill_pct}%`,
                      option.predicted_fill_rate >= 0.9 ? 'text-red-600'
                      : option.predicted_fill_rate >= 0.7 ? 'text-orange-500' : 'text-green-600'],
                  ].map(([label, value, cls]) => (
                    <div key={label} className="text-center p-2 bg-gray-50 rounded border">
                      <div className={`text-lg font-bold ${cls}`}>{value}</div>
                      <div className="text-xs text-gray-500">{label}</div>
                    </div>
                  ))}
                </div>

                {/* Route steps */}
                <div className="space-y-2 mb-4">
                  <div className="flex items-start gap-2 text-sm">
                    <span className="mt-0.5 w-5 h-5 rounded-full bg-blue-600 text-white text-xs flex items-center justify-center flex-shrink-0">1</span>
                    <div>
                      <div className="font-medium text-gray-700">Drive to {option.lot_name}</div>
                      <div className="text-xs text-gray-500">
                        Enter via {campus?.gates?.find(g => g.id === result.gate_used)?.name || result.gate_used}
                        · {fmt_m(option.drive_route?.distance_meters)} · {option.drive_time_minutes} min
                      </div>
                    </div>
                  </div>
                  <div className="flex items-start gap-2 text-sm">
                    <span className="mt-0.5 w-5 h-5 rounded-full bg-green-600 text-white text-xs flex items-center justify-center flex-shrink-0">2</span>
                    <div>
                      <div className="font-medium text-gray-700">Walk to {result.destination_name}</div>
                      <div className="text-xs text-gray-500">
                        {fmt_m(option.walk_route?.distance_meters)} walking · {option.walk_time_minutes} min
                      </div>
                    </div>
                  </div>
                  <div className="mt-2 p-2 bg-yellow-50 border border-yellow-200 rounded text-xs text-yellow-800">
                    💡 {option.recommendation_reason}
                  </div>
                </div>

                {/* Route comparison table */}
                <div className="border border-gray-100 rounded-lg overflow-hidden">
                  <div className="flex items-center justify-between bg-gray-50 px-3 py-2 border-b border-gray-100">
                    <span className="text-xs font-semibold text-gray-600">Route Alternatives (Campus Graph · Dijkstra)</span>
                    {hasDistAlternative && (
                      <button
                        onClick={() => setShowDistRoute(v => !v)}
                        className={`text-xs px-2 py-0.5 rounded font-medium ${showDistRoute ? 'bg-purple-100 text-purple-700' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}
                      >
                        {showDistRoute ? '📏 Shortest dist on map' : 'Show shortest dist on map'}
                      </button>
                    )}
                  </div>
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-gray-500 bg-gray-50">
                        <th className="text-left px-3 py-1.5">Leg</th>
                        <th className="text-left px-3 py-1.5">Optimization</th>
                        <th className="text-right px-3 py-1.5">Distance</th>
                        <th className="text-right px-3 py-1.5">Time</th>
                      </tr>
                    </thead>
                    <tbody>
                      {/* Drive fastest */}
                      <tr className={`border-t border-gray-100 ${!showDistRoute ? 'bg-blue-50' : ''}`}>
                        <td className="px-3 py-1.5 text-blue-700 font-medium">🚗 Drive</td>
                        <td className="px-3 py-1.5 text-gray-600">⚡ Fastest</td>
                        <td className="px-3 py-1.5 text-right font-mono">{fmt_m(option.drive_route?.distance_meters)}</td>
                        <td className="px-3 py-1.5 text-right font-mono">{option.drive_time_minutes} min</td>
                      </tr>
                      {/* Drive shortest distance (if different) */}
                      {option.drive_route_shortest_distance && (
                        <tr className={`border-t border-gray-100 ${showDistRoute ? 'bg-purple-50' : ''}`}>
                          <td className="px-3 py-1.5 text-blue-700 font-medium">🚗 Drive</td>
                          <td className="px-3 py-1.5 text-gray-600">📏 Shortest dist</td>
                          <td className="px-3 py-1.5 text-right font-mono text-purple-700 font-semibold">{fmt_m(option.drive_route_shortest_distance?.distance_meters)}</td>
                          <td className="px-3 py-1.5 text-right font-mono">{(option.drive_route_shortest_distance?.time_seconds / 60).toFixed(1)} min</td>
                        </tr>
                      )}
                      {/* Walk fastest */}
                      <tr className={`border-t border-gray-100 ${!showDistRoute ? 'bg-green-50' : ''}`}>
                        <td className="px-3 py-1.5 text-green-700 font-medium">🚶 Walk</td>
                        <td className="px-3 py-1.5 text-gray-600">⚡ Fastest</td>
                        <td className="px-3 py-1.5 text-right font-mono">{fmt_m(option.walk_route?.distance_meters)}</td>
                        <td className="px-3 py-1.5 text-right font-mono">{option.walk_time_minutes} min</td>
                      </tr>
                      {/* Walk shortest distance (if different) */}
                      {option.walk_route_shortest_distance && (
                        <tr className={`border-t border-gray-100 ${showDistRoute ? 'bg-purple-50' : ''}`}>
                          <td className="px-3 py-1.5 text-green-700 font-medium">🚶 Walk</td>
                          <td className="px-3 py-1.5 text-gray-600">📏 Shortest dist</td>
                          <td className="px-3 py-1.5 text-right font-mono text-purple-700 font-semibold">{fmt_m(option.walk_route_shortest_distance?.distance_meters)}</td>
                          <td className="px-3 py-1.5 text-right font-mono">{(option.walk_route_shortest_distance?.time_seconds / 60).toFixed(1)} min</td>
                        </tr>
                      )}
                      {!option.drive_route_shortest_distance && !option.walk_route_shortest_distance && (
                        <tr className="border-t border-gray-100">
                          <td colSpan={4} className="px-3 py-2 text-center text-gray-400 italic">Fastest and shortest-distance routes are identical for this lot.</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Empty state */}
      {!result && !loading && !error && (
        <div className="text-center py-12 text-gray-400">
          <div className="text-5xl mb-4">🅿️</div>
          <div className="text-lg font-medium">Select your destination and click Find Best Parking</div>
          <div className="text-sm mt-1">
            ParkingNav-X computes optimal drive + walk routes using Dijkstra's algorithm on the campus road graph.
          </div>
        </div>
      )}
    </div>
  )
}
