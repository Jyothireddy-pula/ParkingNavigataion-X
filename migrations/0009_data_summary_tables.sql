-- Migration 0009: Create tables referenced by GET /data/summary but never defined.
--
-- parking_observations  - raw sensor / manual occupancy pushes (pre-validation)
-- occupancy_snapshots   - periodic system-driven snapshots of all lot fill rates
-- route_cache           - cached OSRM / network route results (saves re-computation)
-- campus_events         - structured event calendar driving demand spikes
--
-- lot_occupancy_history (created in 0001, extended in 0008) is the validated store;
-- parking_observations feeds into it after validation via POST /observations.

CREATE TABLE IF NOT EXISTS parking_observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id          TEXT    NOT NULL,
    occupied_spaces INTEGER NOT NULL,
    source          TEXT    NOT NULL DEFAULT 'sensor',
    recorded_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    raw_payload     TEXT                           -- JSON blob of original push
);

CREATE INDEX IF NOT EXISTS idx_pobs_lot_time
    ON parking_observations (lot_id, recorded_at);

-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS occupancy_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id          TEXT    NOT NULL,
    fill_rate       REAL    NOT NULL,
    occupied_spaces INTEGER NOT NULL,
    snapshot_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    trigger         TEXT    NOT NULL DEFAULT 'scheduled'  -- 'scheduled' | 'event' | 'manual'
);

CREATE INDEX IF NOT EXISTS idx_osnap_lot_time
    ON occupancy_snapshots (lot_id, snapshot_at);

-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS route_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    origin_lat      REAL    NOT NULL,
    origin_lon      REAL    NOT NULL,
    dest_lat        REAL    NOT NULL,
    dest_lon        REAL    NOT NULL,
    distance_m      REAL    NOT NULL,
    duration_s      REAL    NOT NULL,
    provider        TEXT    NOT NULL DEFAULT 'networkx',  -- 'networkx' | 'osrm'
    cached_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (origin_lat, origin_lon, dest_lat, dest_lon, provider)
);

-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS campus_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    campus_id       TEXT    NOT NULL DEFAULT 'vitap',
    name            TEXT    NOT NULL,
    event_type      TEXT    NOT NULL DEFAULT 'academic',  -- 'academic'|'sports'|'cultural'|'exam'
    starts_at       TEXT    NOT NULL,
    ends_at         TEXT    NOT NULL,
    expected_attendance INTEGER,
    affected_lots   TEXT,                                 -- JSON array of lot_ids
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cev_campus_time
    ON campus_events (campus_id, starts_at, ends_at);
