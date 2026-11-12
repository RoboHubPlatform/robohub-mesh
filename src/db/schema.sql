-- The telemetry store Mesh offers other applications.
--
-- A gateway sends readings; Mesh keeps them as a series and as the newest value
-- per signal. A series is one field of one source, and a source is a device by
-- the identity it keeps for its life: the id a board opens its session with, or
-- the id the plan gave a device. Where a source sits — its area and node — is a
-- fact recorded in telemetry_source that may change without the series changing. The outbox's sequence is unique per gateway, so a repeated page
-- after a failed ack is ignored rather than stored twice.

PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
PRAGMA journal_size_limit=16777216;
PRAGMA wal_autocheckpoint=512;
PRAGMA auto_vacuum=INCREMENTAL;

-- One row per gateway feed, holding the sequence that has been committed and
-- the address the feed was read from. A changed address starts a new feed.
CREATE TABLE IF NOT EXISTS telemetry_gateway (
    id          TEXT PRIMARY KEY,
    project     TEXT NOT NULL,
    url         TEXT NOT NULL,
    last_seq    INTEGER NOT NULL DEFAULT 0,
    updated_ms  INTEGER NOT NULL DEFAULT 0
);

-- Every source a project has: the plan's placement of it and when it last
-- reported. A source the plan has not placed has area and node -1; a board that
-- reports from a place the plan gives another is a fault, never a merge.
CREATE TABLE IF NOT EXISTS telemetry_source (
    project     TEXT NOT NULL,
    source      TEXT NOT NULL,
    gateway_id  TEXT NOT NULL DEFAULT '',
    area_id     INTEGER NOT NULL DEFAULT -1,
    node_id     INTEGER NOT NULL DEFAULT -1,
    label       TEXT NOT NULL DEFAULT '',
    module_id   TEXT NOT NULL DEFAULT '',
    first_ms    INTEGER NOT NULL DEFAULT 0,
    last_ms     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project, source)
);

CREATE INDEX IF NOT EXISTS telemetry_source_place
    ON telemetry_source(project, area_id, node_id);

-- One reading: one signal of one source at one moment. area_id and node_id are
-- where the reading said it came from, kept as reported. received_ms is the
-- gateway's wall clock, which is what a series is queried on; a node's own
-- timestamp is its uptime and is not a calendar.
CREATE TABLE IF NOT EXISTS telemetry_reading (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT NOT NULL,
    gateway_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    area_id     INTEGER NOT NULL,
    node_id     INTEGER NOT NULL,
    field       TEXT NOT NULL,
    value       REAL NOT NULL,
    status      INTEGER NOT NULL DEFAULT 0,
    received_ms INTEGER NOT NULL,
    source      TEXT NOT NULL DEFAULT '',
    UNIQUE(gateway_id, seq)
);

CREATE INDEX IF NOT EXISTS telemetry_reading_series
    ON telemetry_reading(project, source, field, received_ms);

CREATE INDEX IF NOT EXISTS telemetry_reading_time
    ON telemetry_reading(project, received_ms);

-- The newest value of every signal, so a latest read does not scan the series.
CREATE TABLE IF NOT EXISTS telemetry_latest (
    project     TEXT NOT NULL,
    source      TEXT NOT NULL,
    field       TEXT NOT NULL,
    value       REAL NOT NULL,
    status      INTEGER NOT NULL DEFAULT 0,
    received_ms INTEGER NOT NULL,
    PRIMARY KEY (project, source, field)
);

-- One-minute rollups. They are written from raw readings once a bucket is
-- complete, and they are what a series reads after the raw window has passed.
CREATE TABLE IF NOT EXISTS telemetry_rollup_1m (
    project     TEXT NOT NULL,
    source      TEXT NOT NULL,
    field       TEXT NOT NULL,
    bucket_ms   INTEGER NOT NULL,
    value_min   REAL NOT NULL,
    value_max   REAL NOT NULL,
    value_avg   REAL NOT NULL,
    samples     INTEGER NOT NULL,
    PRIMARY KEY (project, source, field, bucket_ms)
);

CREATE INDEX IF NOT EXISTS telemetry_rollup_1m_series
    ON telemetry_rollup_1m(project, source, field, bucket_ms);

CREATE INDEX IF NOT EXISTS telemetry_rollup_1m_time
    ON telemetry_rollup_1m(project, bucket_ms);

-- Hourly rollups, written from the minute rollups. They outlive raw readings
-- and minute rollups, so a chart over a season still has a series.
CREATE TABLE IF NOT EXISTS telemetry_rollup_1h (
    project     TEXT NOT NULL,
    source      TEXT NOT NULL,
    field       TEXT NOT NULL,
    bucket_ms   INTEGER NOT NULL,
    value_min   REAL NOT NULL,
    value_max   REAL NOT NULL,
    value_avg   REAL NOT NULL,
    samples     INTEGER NOT NULL,
    PRIMARY KEY (project, source, field, bucket_ms)
);

CREATE INDEX IF NOT EXISTS telemetry_rollup_1h_series
    ON telemetry_rollup_1h(project, source, field, bucket_ms);

CREATE INDEX IF NOT EXISTS telemetry_rollup_1h_time
    ON telemetry_rollup_1h(project, bucket_ms);

-- Actionable faults: a reading whose status says the signal is not healthy.
-- These drive the live UI and safety loops; raw debug traces and audit text stay
-- in files. A fault is kept long enough for an operator to act on it and is
-- pruned separately from the readings.
CREATE TABLE IF NOT EXISTS telemetry_fault (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT NOT NULL,
    gateway_id  TEXT NOT NULL DEFAULT '',
    area_id     INTEGER NOT NULL DEFAULT 0,
    node_id     INTEGER NOT NULL DEFAULT 0,
    field       TEXT NOT NULL,
    status      INTEGER NOT NULL,
    severity    TEXT NOT NULL,
    message     TEXT NOT NULL,
    created_ms  INTEGER NOT NULL,
    source      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS telemetry_fault_time
    ON telemetry_fault(project, created_ms);

CREATE INDEX IF NOT EXISTS telemetry_fault_open
    ON telemetry_fault(project, severity, created_ms);
