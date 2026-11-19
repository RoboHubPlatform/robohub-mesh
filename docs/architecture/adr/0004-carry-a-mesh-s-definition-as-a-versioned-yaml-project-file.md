---
id: ADR-0004
title: Carry a mesh's definition as a versioned YAML project file
status: accepted
date: 2026-11-19
requirements: [MESH-0025, MESH-XFER-0001, MESH-XFER-0002, MESH-XFER-0003, MESH-XFER-0004, MESH-XFER-0005, MESH-XFER-0006, MESH-XFER-0007, MESH-XFER-0008]
supersedes: []
superseded_by: []
---

# Carry a mesh's definition as a versioned YAML project file

## Context

The maintainer asked for a mesh to be exported as a YAML or JSON file that
holds everything needed to create the mesh again "with those exact devices and
gateways and relays and everything", so that a mesh can be re-imported over
itself, under another name, or into another Mesh. Today a mesh cannot leave its
folder except by copying it as a template inside the same Mesh
(MESH-FLEET-0004).

### What a mesh is today

A mesh (a "project" in the code, `?project=` and `/mesh/p/{project}` in the
addresses) is a folder `~/.rho/mesh/projects/<id>/` (`ROBOHUB_MESH_PROJECTS`),
mode 0700, whose name is its id (`project_id_from_name`: letters, digits, `-`,
`_`, `.`, spaces made `-`, a `-2` suffix when taken). Mesh-wide state is in
`~/.rho/mesh/state/` (`RHO_MESH_STATE_DIR`) and the telemetry database is
`~/.rho/mesh/telemetry.db`. A mesh is created by `new_project` (folder,
`plan.yml` with `project.name`, a place in `fleet.yml`), copied by
`mesh_project_copy` (`cross_template_lines` drops ids, ports and deployment),
renamed by changing `project.name` (the folder never changes), and deleted by
`mesh_project_delete` (stops its native gateway, frees its fleet place, removes
the folder). Its telemetry is keyed by `(project, source)` and so survives
anything that keeps the folder's name.

Every piece of its state, classified:

| State | Where | Class |
|---|---|---|
| Name, description, gateway URL, telemetry retention | `plan.yml` `project.*`, `gateway_url`, `telemetry_retention_days` | definition |
| Gateway: role, board, driver, sensors, host, serial device, TLS file paths | `plan.yml` `gateway*`, `gateway_tls_*` | definition |
| Nodes (boards, modules, devices): number, id, kind, role, name, description, board, driver, parent / `parent_node`, link, link settings (endpoints, baud, host, port, SSID), sensors, module and bus placement, device integration, transport, address, device id | `plan.yml` `node.N.*`, `node.count`, `node.next_number` | definition (ids and numbers are also identity) |
| Key epoch, revoked flag, production toggle per board | `plan.yml` `node.N.epoch`, `.revoked`, `.production` | definition of the mesh's security state |
| Relay id, parent mesh, child meshes | `plan.yml` `relay_uid`, `mesh_parent`, `mesh_children` | definition (references other meshes by id) |
| Area, REST port, uplink port | `state/fleet.yml` and `plan.yml` `area_id`, `rest_port`, `uplink_port` | definition, but placed by this machine's fleet |
| Wi-Fi password (per node and mesh-wide) | `plan.yml` `password`, `node.N.password` | secret |
| Gateway key (32 bytes) | `gateway.key`, 0600 | secret / identity |
| Relay key (derived from the parent's gateway key) | `relay.key`, 0600 | secret, derivable |
| Production signing-key path | `signing-key.txt`, 0600 | secret reference, machine-local |
| Legacy gateway token, named token records, revoked subjects | `plan.yml` `gateway_token`, `tokens.jsonl` | secret / this installation's access policy |
| Registered application clients and their secrets | `state/clients.jsonl`, `client-secret.txt` | this installation's |
| Node manifest | `nodes.txt` | derived from the plan on every write |
| Board keys | — | never stored; derived from the gateway key, id and epoch |
| Plan revision, update time, steps, status, messages, `fault.last_id` | `plan.yml` | runtime |
| Per-node edit revision (stale-form check) | `plan.yml` `node.N.rev` | runtime |
| Device state, last value, last poll | `plan.yml` `node.N.state`, `.last_value`, `.last_checked` | runtime |
| What runs (image signatures) | `deployed.txt` | runtime, but bound to identity (ADR-0001) |
| Revisions, `.bak`, `.imported`, `.corrupt-*` copies | `revisions/`, folder | history |
| Jobs and their logs, board build folders (with `robohub_secret.h` and `project.conf`), images | `jobs/`, `boards/` | runtime / built artefacts (hold secrets) |
| Gateway pid, log, health, backoff, sessions, fault cursors, agora and telemetry event logs | folder files | runtime |
| Telemetry, roll-ups, faults, inbox | `telemetry.db`, `state/fault-*.jsonl` | history |
| Alert channels, storage settings, catalog caches | `state/`, robohub.settings | Mesh-wide, not the mesh's |

The plan is already a YAML document of nested maps, read and written through the
runtime's `rho_yaml_flat` and `rho_yaml_write_flat` as flat `a.b.c=value` lines;
`rho_yaml_write_flat` sorts keys (numeric keys by number), quotes every value and
replaces the file in one step. But the plan mixes the definition with runtime
keys, so the file cannot be the plan copied as it is.

### What the Rho runtime offers for the format

- YAML: `rho_yaml_flat` (read, Go only) and `rho_yaml_write_flat` (write, Go
  only), nested maps of scalars only — no lists, no flow style; a line the reader
  cannot place is silently skipped. `yaml.value/kind/keys` read single paths.
- JSON: `json_string` writes a literal; `json_str_field` and `json_num_field`
  find the first occurrence of a key in text. There is no JSON parser.
- The toolchain's `src/yaml` is the compiler's own loader and is not reachable
  from a program.

RoboHub publishes the rules for its own files as JSON Schemas in
`robohub/schemas/*.v1.json` (HUB-0007) and keeps the same rules written out in
Rho (`src/validate.rho`), checked by tests; nothing in the runtime validates a
document against a JSON Schema.

## Decision

1. **The file is the mesh's definition, by an allow-list.** A project file holds
   the plan's definition keys that the schema lists (MESH-XFER-0002), the
   mesh's id, name and description, and its placement (area, REST and uplink
   ports) as a wish the importing fleet may refuse. A plan key not in the list is
   not exported; a test holds the list, so a new definition key added to the plan
   without the schema fails the build — as image signatures are held (ADR-0001).
   Runtime state, history, built artefacts and anything that belongs to this
   installation (tokens, clients, Mesh-wide settings) are never in it
   (MESH-XFER-0003). Secrets are ADR-0005's.
2. **Its shape is the plan's own tree.** The file's `plan:` section is the plan's
   flat keys under `plan.` (`plan.node.3.board`), so export and import are a
   filter and a prefix, not a mapping that drifts from the plan. Beside it:
   `format: robohub-mesh-project`, `schema: 1`, `exported.by`, `exported.at`,
   `mesh.id`, `mesh.name`, `mesh.description`, `mesh.area`, `mesh.rest_port`,
   `mesh.uplink_port`, `omitted.*` (ADR-0005), and in a sealed export `sealed.*`
   and `deployed.*` (ADR-0005, ADR-0006).
3. **YAML is canonical; JSON is the same tree.** The export is written by
   `rho_yaml_write_flat` — so it is sorted, quoted and stable (MESH-XFER-0005),
   exactly as plan.yml is — with a header comment saying what the file is. JSON
   is offered on request as nested objects of string values with the same keys.
   Import accepts both, by content (MESH-XFER-0004). Because the runtime's YAML
   reader skips lines it cannot place, the import first checks each line of a
   YAML file against the nested-map subset and refuses with the line number; JSON
   is read by the port of ADR-0007.
4. **Versioned and published.** `schema` is a whole number raised whenever a key
   is added, removed or changes meaning. The import carries an older schema
   forward by migration functions kept for as long as Mesh exists, as
   `plan_import` and `migrate_module_nodes` carry older plans forward, and
   refuses a higher one (MESH-XFER-0007). Each version is published as
   `schemas/mesh-project.v<N>.json` (JSON Schema 2020-12) in Mesh's repository,
   the rules are written out in Rho in `src/mesh/domain/` (a pure module, unit
   tested), and a test holds the two to the same keys and patterns
   (MESH-XFER-0006).
5. **Checked whole.** The import collects every problem with its key path (and
   YAML line) before deciding anything, and writes nothing while there is one
   (MESH-XFER-0008).

## Consequences

- One file restores, moves or forks a mesh; it diffs well in git.
- The plan's key names become a published format: renaming a plan key is now a
  schema version and a migration, not a refactor.
- A YAML file written by hand with lists or flow style is refused rather than
  half-read; the error says which line.
- The schema exists twice (JSON Schema and Rho rules), held together by a test,
  as robohub's own schemas are.
- Multi-line values are not kept: the runtime's YAML writes and reads a value as
  one line (as plan.yml does today).

## Alternatives considered

- **Copy the folder (tar).** Carries history, logs, images and every secret in
  clear; not portable across Mesh versions. Rejected.
- **Export `plan.yml` as it is.** Carries runtime keys and the legacy token;
  importing it would bring stale status and device states. Rejected.
- **A new, cleaner model (gateways / boards / devices as lists).** Easier to read
  for a stranger, but the runtime has no list support in YAML, and a second model
  must be mapped to and from the plan forever. Rejected for schema 1; a later
  schema may present it if the plan itself moves to it.
- **JSON canonical.** Not what the plan is, no comments, and no parser in the
  runtime. JSON stays accepted and offered.
- **A deny-list (as `cross_template_lines`).** A new runtime key would leak into
  every export until someone noticed. Rejected in favour of the allow-list.
