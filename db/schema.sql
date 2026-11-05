-- The telemetry store Mesh offers other applications.
--
-- A gateway sends readings; Mesh keeps them as a series and as the newest value
-- per signal. The outbox's sequence is unique per gateway, so a repeated page
-- after a failed ack is ignored rather than stored twice.

PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;

-- One row per gateway feed, holding the sequence that has been committed and
-- the address the feed was read from. A changed address starts a new feed.
CREATE TABLE IF NOT EXISTS telemetry_gateway (
    id          TEXT PRIMARY KEY,
    project     TEXT NOT NULL,
    url         TEXT NOT NULL,
    last_seq    INTEGER NOT NULL DEFAULT 0,
    updated_ms  INTEGER NOT NULL DEFAULT 0
);

-- One reading: one signal of one device at one moment. received_ms is the
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
    UNIQUE(gateway_id, seq)
);

CREATE INDEX IF NOT EXISTS telemetry_reading_series
    ON telemetry_reading(project, area_id, node_id, field, received_ms);

CREATE INDEX IF NOT EXISTS telemetry_reading_time
    ON telemetry_reading(project, received_ms);

-- The newest value of every signal, so a latest read does not scan the series.
CREATE TABLE IF NOT EXISTS telemetry_latest (
    project     TEXT NOT NULL,
    area_id     INTEGER NOT NULL,
    node_id     INTEGER NOT NULL,
    field       TEXT NOT NULL,
    value       REAL NOT NULL,
    status      INTEGER NOT NULL DEFAULT 0,
    received_ms INTEGER NOT NULL,
    PRIMARY KEY (project, area_id, node_id, field)
);

-- One-minute rollups. They are written from raw readings once a bucket is
-- complete, and they are what a series reads after the raw window has passed.
CREATE TABLE IF NOT EXISTS telemetry_rollup_1m (
    project     TEXT NOT NULL,
    area_id     INTEGER NOT NULL,
    node_id     INTEGER NOT NULL,
    field       TEXT NOT NULL,
    bucket_ms   INTEGER NOT NULL,
    value_min   REAL NOT NULL,
    value_max   REAL NOT NULL,
    value_avg   REAL NOT NULL,
    samples     INTEGER NOT NULL,
    PRIMARY KEY (project, area_id, node_id, field, bucket_ms)
);

CREATE INDEX IF NOT EXISTS telemetry_rollup_1m_series
    ON telemetry_rollup_1m(project, area_id, node_id, field, bucket_ms);

-- Hourly rollups, written from the minute rollups. They outlive raw readings
-- and minute rollups, so a chart over a season still has a series.
CREATE TABLE IF NOT EXISTS telemetry_rollup_1h (
    project     TEXT NOT NULL,
    area_id     INTEGER NOT NULL,
    node_id     INTEGER NOT NULL,
    field       TEXT NOT NULL,
    bucket_ms   INTEGER NOT NULL,
    value_min   REAL NOT NULL,
    value_max   REAL NOT NULL,
    value_avg   REAL NOT NULL,
    samples     INTEGER NOT NULL,
    PRIMARY KEY (project, area_id, node_id, field, bucket_ms)
);

CREATE INDEX IF NOT EXISTS telemetry_rollup_1h_series
    ON telemetry_rollup_1h(project, area_id, node_id, field, bucket_ms);
