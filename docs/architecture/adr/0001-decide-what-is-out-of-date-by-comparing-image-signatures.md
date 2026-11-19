---
id: ADR-0001
title: Decide what is out of date by comparing image signatures
status: proposed
date: 2026-11-13
requirements: [MESH-0003, MESH-BUILD-0001, MESH-BUILD-0002, MESH-BUILD-0003, MESH-BUILD-0012]
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

## Amendment (2026-11-19, proposed)

Found while analysing export and import, and proposed for review with the fix in
Mesh 0.21.1:

- **The decision computed the signature "from the plan alone", and that was the
  fault.** A board's image also carries what the mesh gives it: the area it reports
  from and its gateway admits it at (`CONFIG_RH_AREA_ID`), and a key derived from
  the gateway key, with that key's scope (`robohub_secret.h`). Neither was in the
  signature, so a board flashed under an old area or keyed to an old gateway key
  was shown as running what is planned while its gateway could no longer admit or
  authenticate it. The same was true, in smaller ways, of what a build falls back
  to when the plan says nothing: the fleet's area and uplink port, the mesh's
  Wi-Fi name and password, the default gateway host.
- **The signature must cover every input the build reads, as the build resolves
  it.** `deploy_node_signature` and `deploy_gateway_signature` now take, beside the
  plan, what the mesh gives (`deploy_mesh`: the fleet's area and uplink port, the
  gateway key's scope and, for a gateway that reports through another mesh, that
  mesh's address and key scope). A board's signature adds the area, the key's
  scope and its number, takes the mesh's Wi-Fi when it has none of its own, and
  names the address it dials as the build resolves it; the gateway's adds the area
  and the key's scope, its uplink port as resolved, and the address and key scope
  of the mesh above (MESH-BUILD-0001, MESH-BUILD-0002).
- **The key is named by its scope, never by itself.** The scope,
  HMAC-SHA256(K_gw, "rh-scope1")[0:8], is already written into every image and
  reveals nothing of the key, so `deployed.txt` and a project file's sealed record
  hold no secret they did not hold before.
- **Deliberately left out:** a board's key epoch, since a rotation reaches the
  board by REKEY and is followed on its own (MESH-ACCESS-0005), and the versions of
  the catalog's boards and drivers, which are the catalog's, not the mesh's, and
  would put every board of every mesh out of date on any catalog update; whether a
  board should pick up a new driver is another question than whether it can still
  talk to its gateway.
- **A record written before this cannot be trusted.** Signatures now begin with
  their shape (`sig=2`). A record without it does not say what area or key the
  board was built with, so it is shown out of date with that reason ("recorded
  without its area and key") instead of being compared segment by segment, and it
  is not migrated: the area and key at the time it was written cannot be
  reconstructed, and assuming today's would repeat the fault (MESH-BUILD-0012).
  After upgrading from 0.21.0, every board and the gateway show out of date once,
  until they are flashed or restarted.
- **The project file's rule still holds and is now also enforced by the
  signature.** Records travel only with the full identity (MESH-XFER-0017); a
  record that travels with it compares equal, and one that met another area or key
  would not.
