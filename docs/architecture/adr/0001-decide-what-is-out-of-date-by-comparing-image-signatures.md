---
id: ADR-0001
title: Decide what is out of date by comparing image signatures
status: proposed
date: 2026-11-13
requirements: [MESH-0003, MESH-BUILD-0001, MESH-BUILD-0002, MESH-BUILD-0003]
supersedes: []
superseded_by: []
---

# Decide what is out of date by comparing image signatures

## Context

Before 0.9 Mesh was a wizard of stages — topology, network, build, verify — and
reopening a stage reset every later one: renaming a board put the whole mesh back
to "not built". Editing in place (MESH-0003) needs a finer answer to one question:
does this edit change what a board's image is, or not? Only the person decides
when a board is reflashed, so the answer must be shown, not acted on.

## Decision

What a board's image is built from is its **signature**, a text of labelled
segments computed from the plan alone (`deploy_node_signature`): its board, role,
parent, link and the link's settings, its modules, the links it serves, its
production toggle when on, and the gateway's address when it dials the gateway
over a network. The gateway has its own (`deploy_gateway_signature`): where it
runs, its host, its REST and uplink ports, the links it serves and the meshes it
reports through or that report through it. When a job flashes a board or starts
the gateway successfully, the signature it was built from is written to
`deployed.txt`. A board is out of date when its recorded signature differs from
the plan's, and every differing segment is named in the reason
(`deploy_reason`): never programmed, or "board, modules, link, ... changed".

Names, descriptions and a device's address are not in any signature.

## Consequences

- An edit never builds or resets anything; Apply changes lists exactly the
  boards whose signature changed.
- A new field that reaches a board's image must be added to its signature, or a
  change to it will not put the board out of date — the unit tests in
  `tests/domain_test.rho` hold the list.
- A board programmed before signatures were kept has none and shows as never
  programmed until it is flashed again (or the plan is told it was).
