---
id: ADR-0010
title: A Zigbee coordinator is a device node Mesh runs Zigbee2MQTT for, and Zigbee devices are MQTT devices under it
status: accepted
date: 2026-11-20
requirements: [MESH-0033, MESH-0034, MESH-0035, MESH-ZB-0001, MESH-ZB-0002, MESH-ZB-0003, MESH-ZB-0004, MESH-ZB-0005, MESH-ZB-0006, MESH-ZB-0007, MESH-ZB-0008, MESH-ZB-0009, MESH-ZB-0010, MESH-ZB-0011, MESH-ZB-0012, MESH-ZB-0013, MESH-ZB-0014, MESH-ZB-0015, MESH-ZB-0016, MESH-ZB-0017, SYS-0069]
supersedes: []
superseded_by: []
---

# A Zigbee coordinator is a device node Mesh runs Zigbee2MQTT for, and Zigbee devices are MQTT devices under it

## Context

robohub ADR-0017 decides that Mesh owns Zigbee coordinators: a coordinator is
part of a mesh, placed under a board of the mesh that can host a USB radio, and
Mesh runs Zigbee2MQTT for it on that board. robohub-devices ADR-0003 declares the
coordinators (kind `coordinator`, `usb`, `needs`, `zigbee.adapter`) and the
S60ZBTPF (a `zigbee` block, the MQTT contract). robohub-keeper ADR-0006 lets Mesh
be issued a broker user under a root it declares.

This decision is how Mesh does its part. What Mesh has today:

- **Device nodes** sit under the gateway, a board node or the mesh itself, with
  a device (a robohub-devices manifest), a transport (`http` or `mqtt`) and an
  address; an MQTT device is read from its manifest's topics and field lines and
  switched by its action (ADR-0009).
- **Boards** come from the platforms' manifests with their `capabilities`; the
  native gateway is a synthetic board row. `transport_fits` decides links from
  capabilities alone.
- **The native gateway** is started by a shell job as a `nohup` process whose
  pid is written beside the project; it is stopped by that pid only when the
  process is a `robohub-gateway`. A board gateway is deployed over the board's
  `deployment` (ssh, adb, docker).
- **The project file** is strict (an unknown key is refused) and versioned
  (schema 1; a newer schema is refused, an older one carried forward); secrets
  travel only sealed (ADR-0004, ADR-0005).
- **Mesh's bus access** is fixed in its manifest: publish `+/rpc`,
  `+/cmnd/POWER`, `mesh/+/telemetry`; subscribe `+/status/#`, `+/online`,
  `+/stat/#`, `+/tele/#`.

Zigbee2MQTT's own facts (robohub ADR-0017): availability is JSON
(`{"state":"online"}`); a switch takes `"ON"`/`"OFF"`, not booleans; joining
is opened for at most 254 s by `bridge/request/permit_join` `{"time":n}`; joined
devices are listed, retained, on `bridge/devices` with `ieee_address`,
`definition.model`, `definition.vendor`, `supported` and `interview_state`;
renaming and removing are bridge requests answered with `status: ok|error`
and an echoed `transaction`.

## Decision

1. **A coordinator is a device node** whose device is of kind `coordinator`
   (MESH-ZB-0001), with a name that is its address, a serial port and a channel.
   It is offered and accepted only under the gateway or a board node whose board
   declares every capability in the manifest's `needs:` — for a USB coordinator
   `host` and `usb_host` — with the native gateway counting as both; at most one
   per board (MESH-ZB-0002). The serial port is picked from the board's
   `/dev/serial/by-id/` entries, those matching the manifest marked
   (MESH-ZB-0003).
2. **Zigbee devices are device nodes of transport `zigbee` under their
   coordinator**, read and switched by the MQTT device path with the prefix
   `zigbee/<mesh>/<coordinator>/<address>` (MESH-ZB-0014). No second reading
   path: `zigbee` differs from `mqtt` only in where the prefix comes from and in
   pairing.
3. **Two contract additions, usable by any MQTT device:** `online_path` to read
   availability from inside JSON (MESH-0034), and `{ONOFF}` in an action's
   payload (MESH-0035). `{on}` and a whole-payload `online_up` keep their
   meaning.
4. **Running Zigbee2MQTT** (MESH-ZB-0004…0008):
   - the broker user is issued by the keeper (`zigbee/<mesh>/<coordinator>`),
     its password owner-only in the mesh's folder;
   - the configuration is written from robohub-zigbee2mqtt's template before the
     first start, with the password and network key as `!secret` references in
     an owner-only `secret.yaml`, the adapter always named, the network key and
     PAN ids `GENERATE`, availability on, frontend and Home Assistant off,
     `Z2M_ONBOARD_NO_SERVER` set; afterwards Mesh rewrites only the keys it owns;
   - **native:** a process from `~/.rho/toolchains/zigbee2mqtt/<version>`, data
     in `<mesh folder>/zigbee/<coordinator>/`, pid and log beside it, stopped by
     pid only when the process is that Zigbee2MQTT; the gateway's start and stop
     leave it alone;
   - **another host board:** the pinned container over the board's deployment
     login, refused while the keeper says the broker is loopback-only;
   - missing Node.js or Zigbee2MQTT is said, with an install job for the latter.
5. **Pairing is Mesh's** (MESH-ZB-0009…0013): coordinator state from
   `bridge/state` and `bridge/info`; *Allow joining* for at most 254 s with a
   countdown and *Stop*; a list of joined devices not yet in the plan, matched to
   a manifest by `zigbee.model`/`zigbee.vendor`; *Adopt* adds the node and renames
   the device to its address, the node marked unrenamed until the response says
   `ok`; removing a node removes the device from the network. All of it needs the
   plan's grant and a form from Mesh's own pages.
6. **Bus patterns** (MESH-ZB-0015): subscribe `zigbee/#`; publish
   `zigbee/+/+/+/set` and `zigbee/+/+/bridge/request/#`; `bus.issue: [zigbee]`.
   Mesh ids and addresses must be valid single topic levels.
7. **The project file goes to schema 2** (MESH-ZB-0016): coordinator and Zigbee
   device nodes with `ieee`, `serial_port`, `channel`; the network key, PAN ids,
   channel and `coordinator_backup.json` sealed, or listed as omitted.

## Consequences

- A new module `src/mesh/zigbee.rho` (coordinator lifecycle, bridge requests,
  discovered devices) beside `devices.rho`; `catalog.rho` reads `usb_host` and
  device kinds; `editor.rho` gains the coordinator and pairing pages;
  `projectfile.rho` and `schemas/mesh-project.v2.json` the new fields.
- The keeper must reload the broker with Mesh's new patterns: the Mesh release
  that ships this is installed or restarted through the keeper.
- Removing a coordinator stops its Zigbee2MQTT, removes its broker user and
  keeps its data folder until the mesh is removed, so a coordinator put back
  keeps its network.
- A native coordinator stops when the machine (or WSL) stops and is started again
  by Mesh when Mesh starts, as the native gateway is; a container on a board
  restarts by its restart policy.
- Zigbee devices appear in history, widgets and the API exactly as MQTT devices
  do; their reachability comes from Zigbee2MQTT's availability (10 min for mains
  devices by default).
- Existing manifests, plans and project files of schema 1 read as before.

## Alternatives considered

- **A coordinator as a new node kind** rather than a device node — clearer in the
  plan's text, but a second way of adding hardware with its own editor and
  project-file shape. Rejected; the device manifest's kind is what differs.
- **Zigbee devices as transport `mqtt` with a hand-typed prefix** — no new
  transport, but the person would type `zigbee/<mesh>/<coordinator>/<name>` and
  pairing would have no node to attach to. Rejected.
- **Reading `bridge/event` instead of `bridge/devices`** for joined devices —
  events are lost while Mesh is down; the retained device list is not. The event
  may still be used to refresh the page.
- **Letting Zigbee2MQTT keep the friendly name it gives** (the IEEE address) — no
  rename request, but the topic would be an address no person reads and the plan's
  name would not be the topic. Rejected.
