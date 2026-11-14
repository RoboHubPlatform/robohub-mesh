# mesh-telemetry

Mesh's telemetry store: every reading its gateways and devices deliver, kept as
a series per signal, rolled up by minute and hour, with the newest value of each
signal and the faults worth acting on. It is a Go port that Mesh is built with
(`ports: [telemetry]` in Mesh's manifest), because Rho has no file locking or
database batch of its own. It lives in Mesh's repository and is versioned with
Mesh, so a change to the store is a Mesh release.

## Who uses it

Only Mesh links it. The toolchain finds it as a plugin of Mesh's own tree that
serves the `robohub` platform and provides ports (`integration.provides.ports`),
and compiles it into Mesh's program beside the `sql` port it is written
against. Mesh calls it through `extern fn telemetry_*` declarations in
`src/mesh/telemetry.rho`.

Everyone else reads the store through Mesh's API: the Mesh gateway provider
asks `/mesh/api/projects/<project>/telemetry/series` for a chart's history,
`…/telemetry/stream` (or the broker topic `mesh/<project>/telemetry` that Mesh
relays) for the live feed, and each source's `/value` for its newest reading
(see {ref}`robohub-provider-mesh/index`). So a dashboard card in aquarium or
energy shows what this store holds.

## What it does

| Job | How |
|---|---|
| **Ingest a gateway** | Asks the gateway's outbox for the readings after the feed's cursor (`GET /api/v1/telemetry/outbox?after=<seq>&limit=500`, with the gateway's token), writes the page in one transaction, advances the cursor, then acknowledges (`POST /api/v1/telemetry/ack`). A failure leaves the page to be read again, and a repeated page is ignored by the reading's unique key |
| **Record a reading** | A reading Mesh takes itself (an HTTP or MQTT device it polls) goes into the same series |
| **Live feed** | Each recorded reading is also appended as a JSON line to the feed file Mesh streams to its pages and relays to the broker |
| **Latest** | The newest value of a signal, without scanning the series |
| **Series** | A time range, raw when the step is empty and averaged into buckets otherwise. A range in the last two hours reads raw rows; an older one reads the minute or hour rollups, falling back to raw rows while the first rollup catches up. `from` and `to` may be relative (`2h`, `30m`) |
| **Roll up** | After each ingest, completed raw minutes are folded into `telemetry_rollup_1m`, and completed hours of those into `telemetry_rollup_1h` |
| **Retention** | Removes each window's oldest rows: raw readings first, then minutes, then hours. Faults are pruned separately |
| **Maintenance** | WAL checkpoints (passive, or truncate in a maintenance window), incremental or full vacuum, and a storage status (sizes, free space, row counts, last maintenance) for Mesh's Storage tab |
| **Faults** | Records a fault from a reading whose status says the signal is not healthy, or from Mesh itself (a safety rule, a device that did not answer); lists, clears and prunes them |

## Its tables

The schema is Mesh's own (`db/schema.sql`), applied through the `sql` port
before the first poll; the port writes the tables it declares. A store from
before series were keyed by source is migrated on open.

| Table | What it holds |
|---|---|
| `telemetry_gateway` | One row per gateway feed: the sequence committed so far and the address it was read from. A changed address starts a new feed |
| `telemetry_source` | Every source a project has: the plan's placement of it (area and node, `-1` when unplaced) and when it last reported |
| `telemetry_reading` | One reading: one signal of one source at one moment, with where it said it came from and the gateway's wall-clock time (`received_ms`), which a series is queried on |
| `telemetry_latest` | The newest value of every signal |
| `telemetry_rollup_1m` | One-minute rollups, read after the raw window has passed |
| `telemetry_rollup_1h` | Hourly rollups, which outlive the raw readings and minute rollups |
| `telemetry_fault` | Actionable faults, kept long enough for an operator to act on |

A series is one signal of one source, and a source is a device by the identity
it keeps for its life — the id a board opens its session with, or the id the
plan gave an HTTP device — never by where it sits. A reading that arrives with
no identity is placed by its area and node through `telemetry_source`, and kept
under `legacy:<area>.<node>` until a source is bound there; binding one adopts
those readings. A board reporting from a place the plan gives another is a
fault, never a merge.

## Where it is kept, and for how long

The database is `ROBOHUB_MESH_TELEMETRY_DB` when set, otherwise
`~/.rho/mesh/telemetry.db`. Mesh's settings decide the retention:

| Mesh setting | Default | What it keeps |
|---|---|---|
| `telemetry.raw_days` | 14 | Raw readings (also the project's `telemetry_retention_days`, or `ROBOHUB_MESH_TELEMETRY_RETENTION_DAYS`) |
| `telemetry.minute_days` | 90 | Minute rollups |
| `telemetry.hour_days` | 1825 | Hour rollups |
| `telemetry.fault_days` | 365 | Faults |
| `telemetry.vacuum_mode` | `incremental` | How free pages are reclaimed |

`sql_contract_test.go` declares what the port needs from the `sql` port
(opening a database, applying the schema, a statement batch, the raw handle) so
the port type-checks where it lives; the real definitions are the `sql` port's,
merged in at build time.
