---
id: ADR-0002
title: Run builds and flashes as shell jobs, one flock-held runner per mesh
status: proposed
date: 2026-11-13
requirements: [MESH-BUILD-0004, MESH-BUILD-0005]
supersedes: []
superseded_by: []
---

# Run builds and flashes as shell jobs, one flock-held runner per mesh

## Context

Building a board takes minutes and flashing one needs a person at the board.
Before 0.10 a build ran inside the request that asked for it, so a page waited on
a compiler, two meshes on one machine waited on each other, and a flash could not
wait for the person to press BOOTSEL. Mesh is one Go service generated from Rho,
with no job queue of its own.

## Decision

Applying writes each step as a shell script beside the mesh, in `jobs/`, numbered
in order, with a log, and answers at once. One runner per mesh (`runner.sh`,
started under `flock -n` on `runner.flock`) runs the scripts in order: a job with
a `.wait` file waits until its `.go` exists (the person says the board is ready),
a job with a `.cancel` file ends with 130, and a job whose `.after` prerequisite
ended non-zero is skipped with 125. Every job runs under `umask 077`. The
background worker reads the exit files to record what a board runs. The lock
stays with the runner and is not inherited by the programs a job starts.

## Consequences

- No page waits on a build; one mesh's build never waits on another's.
- A job's state is files a person can read when something goes wrong, and a
  failed job can be tried again.
- The runner needs `flock` (declared in `system_deps`) and a POSIX shell.
- A runner that dies leaves a queue with nothing running it; Mesh says so on the
  job strip with what holds the lock.
