---
id: ADR-0003
title: Keep the telemetry store as a Go port in Mesh's own tree
status: proposed
date: 2026-11-14
requirements: [MESH-0009, MESH-0010, MESH-0021, MESH-TEL-0001]
supersedes: []
superseded_by: []
---

# Keep the telemetry store as a Go port in Mesh's own tree

## Context

Mesh is the only telemetry store of the installation (robohub:ADR-0008). Rho has
no file locking or database batch of its own, so the code that ingests a
gateway's outbox in one transaction, rolls readings up, prunes them and answers
the series queries is Go, generated into Mesh's program as a port. Until 0.18.14
that port lived in the toolchain's RoboHub plugin, so a change to Mesh's store was
a release of the toolchain, and the toolchain carried one application's schema.

## Decision

The telemetry store is Mesh's own Go port, `plugins/mesh-telemetry` in Mesh's
repository, provided to the build as a plugin of the project's own tree
(`integration.provides.ports`) and asked for by `ports: [telemetry]` in Mesh's
manifest. Its schema is `db/schema.sql`, read by the same sql port that creates
the database. It is versioned and released with Mesh, not published on its own.

## Consequences

- A change to the store is a Mesh release, with Mesh's changelog.
- The store is written against the sql port's generated functions; the Go test
  file declares them so the port type-checks where it lives.
- `rho req --workspace` does not discover the plugin as a module of its own; its
  requirements are Mesh's (MESH-TEL-*).
- The Go code of the store has no behavioural test of its own today; Mesh's
  smoke tests do not exercise ingest.
