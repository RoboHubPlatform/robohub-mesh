---
id: ADR-0006
title: Import as a new mesh, a copy or over an existing one, previewed and whole
status: accepted
date: 2026-11-19
requirements: [MESH-0025, MESH-0027, MESH-XFER-0008, MESH-XFER-0013, MESH-XFER-0014, MESH-XFER-0015, MESH-XFER-0016, MESH-XFER-0017, MESH-XFER-0018, MESH-XFER-0019, MESH-XFER-0020, MESH-XFER-0021, MESH-XFER-0022, MESH-XFER-0023, MESH-XFER-0026]
supersedes: []
superseded_by: []
---

# Import as a new mesh, a copy or over an existing one, previewed and whole

## Context

The maintainer wants to re-import an export "with overwrite or with a
different name or simply put it in another robohub-mesh instance". What an
import must preserve follows from what is bound to what (ADR-0004, ADR-0005):

- A flashed board is admitted by its gateway when its id, area, node number and
  epoch match the node manifest and its key derives from the gateway key; a
  network board also dials the gateway's host and uplink port. Change any of
  them and the board must be built and flashed again.
- Telemetry is kept under `(project, source)`: a mesh that keeps its folder's
  name keeps its history; a new mesh starts with none.
- Applications read a mesh with a token scoped `mesh:<id>` (MESH-API-0002); a
  mesh that keeps its id on a new machine keeps its applications' scopes.
- On one machine, meshes must not share an area, a REST port or an uplink port
  (MESH-FLEET-0001), and two meshes with the same node ids and the same gateway
  key would admit each other's boards.
- `deployed.txt` records what runs (ADR-0001); its signatures are computed from
  the plan alone and do not include the area or the gateway key.
- A plan write raises the revision and keeps the plan before it under
  `revisions/` — but only when the last copy is two seconds old
  (`plan_keep_revision`); `rho_lock` is per process.
- Mesh's catalog — platform boards, drivers, modules, device integrations — is
  read from the local registry at run time (MESH-PLAN-0006) and may differ
  between machines.
- Editing never starts anything (MESH-0003); revoking a board takes effect at
  once through the node manifest, which every plan write rewrites.

## Decision

**Three modes.**

- **New mesh** (default). Named as the file says or as the person types; under
  the file's mesh id when it is free here, otherwise an id made from the name by
  `project_id_from_name`. Node numbers and ids are kept. The file's area and
  ports are kept when no other mesh here has them, otherwise the fleet assigns
  new ones. The sealed gateway key is kept when opened (ADR-0005). The file's
  deployed signatures are kept only when the key, the area, the uplink port and
  every number and id are all kept, so a mesh moved whole shows its boards
  current and anything less shows them never programmed (MESH-XFER-0014,
  MESH-XFER-0017).
- **Copy.** What copying a mesh as a template does today (MESH-FLEET-0004):
  new ids, new key, no secrets, no deployed record, no link to another mesh
  (MESH-XFER-0015). Offered when identity cannot be kept.
- **Overwrite.** Replaces an existing mesh's plan definition with the file's in
  one plan write, after the person types the mesh's name. It keeps what is this
  Mesh's: the id and folder, area and ports, gateway key, deployed records (the
  ordinary signature comparison then shows what changed), token records, jobs and
  history; plan runtime keys are dropped and regenerate. The plan it replaces is
  kept as a revision regardless of the two-second rule, so the overwrite can be
  undone like any edit (MESH-XFER-0016). An overwrite whose definition is
  already the mesh's writes nothing (MESH-XFER-0023).

**Conflicts settled before writing** (MESH-XFER-0018, MESH-XFER-0019): id
taken → new id or overwrite; a node id or the sealed key used by another mesh
here → identity refused, copy offered (re-importing an export beside its own
original lands here); area or port taken → reassigned and listed; link to a
mesh not here → dropped and listed, and a kept link goes through the existing
ring check; catalog entries missing → listed, nodes kept, their builds refused
until present; Wi-Fi passwords not carried → listed. Structural problems
(MESH-XFER-0008) are errors, catalog problems warnings.

**Preview, then confirm** (MESH-XFER-0020). Every import is first a dry run
that writes nothing and answers what will be created, changed, removed,
reassigned, kept and omitted, which boards will be out of date and which the
manifest will admit or refuse. The confirmation names the file by its SHA-256
digest and is refused for any other; the file is checked again on
confirmation. The file is not kept on the server between the two: the page
holds it in its form, the API caller sends it again.

**Whole or nothing** (MESH-XFER-0021). A new mesh is built in a hidden folder
`projects/.import-<random>/` (`project_ids` only lists folders that hold a
plan, and the rename is atomic on one filesystem): plan written with
`rho_yaml_write_flat`, `gateway.key` with `rho_write_secret`, `deployed.txt`,
manifest. Its fleet place is taken under the fleet's lock, then the folder is
renamed into place; a failed rename gives the place back. An overwrite is one
`plan_write`. Mesh removes `.import-*` folders, and fleet places whose folder is
missing, at start.

**Nothing runs** (MESH-XFER-0022). No job is queued, no gateway started,
stopped or restarted. A new mesh's gateway is "not started" until Apply. An
overwrite rewrites the manifest as any edit does, and the preview lists the
boards it admits, refuses or revokes.

**Data only** (MESH-XFER-0026). Values are never run, never put on a command
line, never read as paths (TLS paths are kept as strings and warned about when
the file does not exist here); a mesh id is accepted only in the charset
`project_id_from_name` produces.

## Consequences

- "Move this mesh to a new machine" is: sealed export, import as new, Apply
  nothing — boards keep reporting once the gateway is started there and they can
  reach it.
- "Undo my last hour" is: overwrite with this morning's export; history stays.
- "Second site" is: import as copy.
- The import duplicates part of the editor's checks (links, transports, bus
  plan); they must be the same functions, in `src/mesh/domain/`, not a second
  copy.
- SHA-256 of the file is needed for the digest: the runtime has HMAC-SHA256
  only, so the digest is computed by the port of ADR-0007 (or as an HMAC under a
  fixed key, which is equally good as a content name).
- Whole-fleet export (several meshes and their relay links in one file) is not
  decided here; schema 1 holds one mesh.

## Alternatives considered

- **Always mint new ids on import.** Simple, but a move then loses every flashed
  board and the history's identity. Rejected; kept as the copy mode.
- **Overwrite by deleting and re-creating the folder.** Loses jobs, deployed
  records and the revision trail, and needs the gateway stopped. Rejected.
- **Overwrite replaces the gateway key too.** Breaks MESH-ACCESS-0001 and every
  board flashed here. Rejected.
- **Keep the uploaded file on the server between preview and confirm.** A
  staging store of untrusted files with expiry and ownership, for no gain over
  sending it twice. Rejected.
- **Import without a preview, with undo only.** Undo of a new mesh is a delete;
  undo of an overwrite is a revision — but a manifest change takes effect before
  anyone sees it. Rejected.

## Amendment (2026-11-19, approved by the maintainer)

Made during implementation and approved in review:

- **An overwrite from a file without passwords keeps this mesh's Wi-Fi passwords,**
  the mesh-wide one and each node's, matched by node id; a sealed block that is
  opened replaces them. This is what makes re-importing a default export over its
  own mesh change nothing (MESH-XFER-0016, MESH-XFER-0023).
- **An overwrite takes the file's name and description** (`mesh.*`), and keeps this
  Mesh's id, folder, area, ports, gateway key, record of what runs, tokens, jobs and
  history; runtime keys of the plan are dropped (MESH-XFER-0016, MESH-XFER-0019).
