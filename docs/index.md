# RoboHub Mesh

The Mesh application plans and runs deployments: a gateway, the nodes that
report through it, the connections between them, and the firmware on their
boards. It is the part of the ecosystem that knows about boards, not only about
modules.

It is a keeper-hosted application: it runs on its own port, standalone or
launched from the keeper's `/applications` registry. Its front door is `/mesh`,
the fleet of every mesh; a mesh opens at `/mesh/p/<project>`.

The fixed top bar has Fleet, Faults, Settings and Documentation, and, for the
mesh that is open, Live, Gateway, Apply changes and its settings. The fixed
bottom bar lists every mesh, with **New mesh** beside them. When the keeper
started the application, the top bar also has **Keeper**, which returns to it.

## Signing in

Mesh trusts sessions and tickets the keeper issues. Started by the keeper, it
fetches the keeper's public key from the keeper on its machine; started
elsewhere, it is told where the keeper is with `RHO_AUTH_URL`, sends a person
there to sign in, and fetches the key from `<RHO_AUTH_URL>/auth/public-key`,
keeping a copy so a keeper that is briefly down locks nobody out.

Without a keeper, `RHO_AUTH_STANDALONE=1 ./build/mesh` runs Mesh on its own: it
answers requests from its own machine as the local user and refuses any from
another machine or through a proxy.

## The fleet

`/mesh` shows every mesh this Mesh manages as one panel:

- a card per mesh with its tree drawn small — the gateway, the boards and
  devices under it, their modules — its counts and readings, and whether its
  gateway answers;
- **+ New mesh**, which takes a name and gives the mesh its own folder, area,
  REST port and uplink port, so two meshes run side by side on one machine;
- the inbox of faults across every mesh, and the hosts their gateways run on;
- an activity strip with how many meshes are down and how many faults are open.

A mesh is saved as a folder in a workspace:

| What | Where |
|---|---|
| Default workspace | `~/.rho/mesh/projects` |
| Override | `ROBOHUB_MESH_PROJECTS` |
| Plan | `<project>/plan.yml`, earlier revisions under `revisions/` |
| What each board runs | `<project>/deployed.txt` |
| Jobs and their logs | `<project>/jobs/` |
| Board sources and builds | `<project>/boards/node_<number>/` |
| Fleet (areas and ports per mesh) | `<state>/fleet.yml` |

Renaming a mesh changes its display name, not its folder. Deleting it stops its
native gateway, removes the folder and frees its place in the fleet.

## The agora

A mesh opens as its agora: the live tree, a bar above it, an inspector beside
it and an activity strip below.

- The gateway is the root, every board branches from its parent, and the badge
  on a branch is the link and names its transport. Modules hang under the
  board that carries them; devices under the gateway or board that owns them.
- Each card says what the mesh reports about it: online with its last reading,
  quiet, or no readings — and, for a board, whether it runs what is planned.
- The bar says whether the gateway answers and offers **Apply changes** when a
  board or the gateway differs from what it runs.

Selecting the gateway, a card or a link badge opens it in the inspector. The
project and the selection are in the address — `/mesh/p/<project>?select=
node:<id>` — so two tabs show two meshes and a link opens the node it names.
The wizard's old addresses (`/mesh/plan`, `/mesh/step/...`, `/mesh/node/<n>`)
open the agora with what they showed selected.

A new mesh opens on the same canvas with three prompts: choose where the
gateway runs, add the first node, apply. Each opens the control that does it,
and the guide goes once the mesh has a node.

On a narrow screen the inspector is a sheet at the bottom of the page.

## Editing in place

Every edit is made in the inspector and saved at once:

- **Node** — a board's name, description, role (`node`, `relay` or
  `gateway`), board and parent. Moving a board under another keeps its link
  when the new parent can serve it and picks one both can otherwise.
- **Link** — the transport and its settings, for the link the board reaches
  its parent over.
- **Modules** — the modules fitted to the board, with its bus plan.
- **Data** — what the board or device last reported; a device can be polled and
  switched here.
- **Delete** — says what goes with the node (its modules) and asks where what
  hangs from it moves. No other node changes its number or its id.
- **+ node**, **+ module** and **+ device** under any card add what hangs from
  it.
- The gateway's own inspector sets where it runs and its ports, and builds,
  starts and stops it.

An edit builds nothing and resets nothing. What a board's image is built from —
its board, role, parent, link and the link's settings, its modules, the links it
serves, and the gateway's address when it dials it — is its signature, and the
signature is recorded when the board is programmed. A board whose plan differs
shows as out of date, with what changed: a new name changes nothing, a new
module puts that board alone out of date, a new link puts the board out of date
and asks the gateway or relay serving it to restart.

Each save raises the node's own revision. A form opened before the node's last
save is refused — "changed elsewhere" — rather than written over it.

### Nodes and modules

A node is one computer. A sensor module is a **node of its own** — `kind: module`
— attached to a node, and a node can carry any number of them. The tree shows
them as module cards under the node they extend, so the plan reads as the tree
of hardware it describes. **+ module** under a board's card, or the Modules tab of its inspector, adds
one.

A module node is not a build target. It has no role, board or link of its own
and is never built or programmed separately; it is carried by the image of the
node it extends. The node itself is the only thing built and programmed, and
that image carries all of its modules.

Modules are read from `robohub-drivers/boards` on every load of the editor, so a
board added to that checkout appears without rebuilding Mesh or the keeper.
Output and transceiver boards are left out — they are boards, but not sensors
fitted to a node. The list is grouped into collapsible sections by board
category, each with its own colour and three cards to a row; a section with a
module selected stays open and shows the selected name in its header. The first
module attached to a node names the example its image is built from.

A native host has no pins. The gateway can take module nodes only when it is a
real board; on a native gateway the module controls are absent, and a module
attached to a gateway is hidden if the gateway is changed back to `native`.

### Device nodes

A network device is a node of its own too. It is an integration from
`robohub-devices` — the Shelly Plug M Gen3 is the first — attached to a gateway
or a relay. A device node is never built or programmed; the node it is attached
to carries it, and the Mesh tree shows it under that parent.

The device editor chooses the integration, the transport (`http` or `mqtt`), an
HTTP address and the device id. An HTTP device can be polled and switched from
the Data tab of its inspector; an MQTT device is carried by the relay or gateway that owns the
broker connection. A file from a device can also reach a gateway over
`POST /api/v1/frames` as an encoded RoboHub frame, so the same device record is
produced whether it arrived on a serial link or over the network.

Each module says how it reaches a bus in its own manifest:

```yaml
board:
  bus:
    type: i2c
    instance: i2c0
    address: "0x77"        # a part with one address
    addresses: "0x76-0x77" # or the range it can be configured in
    cs: auto               # SPI: the node picks a chip select
```

The Modules tab shows a **bus plan**, which is how several modules become one
node:

- 1-Wire modules share `onewire0`.
- I2C modules share `i2c0`; two fixed addresses on the same bus conflict, and a
  module with an address range is assigned one that is free.
- SPI modules share the bus lines and each gets a distinct chip select.
- ADC, PWM, GPIO and UART instances are exclusive.

A conflict is shown on the node and that module is left out of the image, so the
parent still builds. A module that says nothing about its bus is listed without
a plan rather than guessed at.

## Links

A connection page shows both ends and offers only transports both boards can
serve. The set is the intersection of:

- the transports declared by the `robohub-transport` module
  (`capabilities.transports`), and
- each board's `capabilities.links`.

The transports today are UART, USB, Console, WiFi, Socket, Ethernet, RS-485, SPI
and I2C, subject to what the two boards support. `Console` is offered only when
the parent is a host — the native host or a board that declares `host` and
`console` — because the console is the host's byte stream.

The fields follow the transport:

| Transport | Fields |
|---|---|
| UART / USB / Console / RS-485 | parent endpoint, node endpoint, baud (RS-485 also its address) |
| WiFi | SSID, password, parent host, node host, TCP port |
| Ethernet / Socket | parent interface or host, node address, port |
| SPI / I2C | parent bus, node bus, address |

## Apply changes and jobs

**Apply changes** lists what differs from what runs and what applying will do:
build each changed board and then flash it, and restart the gateway (or build
and program it, when it is a board). A board's inspector can build and flash
that board alone, and the gateway's can rebuild and restart it.

Applying queues jobs and answers at once. Each job is a script beside the
project with its own log; one runner per mesh works through them in order, so a
long build in one mesh never waits on another, and no page waits on a build. A
job that depended on one that failed is skipped. The activity strip and each
card show what is running, what waits, and what failed; a job opens its log in
the inspector, where a failed one can be tried again.

The node build carries the connection's transport into the firmware:

| Transport | Node link |
|---|---|
| UART | a hardware UART (`uart1` on the Pico 2W) |
| USB / Console | USB CDC |
| WiFi / Socket | the TCP sink |

The command is the one the examples use:

```sh
rho build -target <board> <node project>
rho flash -target <board> -project <node project> <image>
```

The commands are the ones the examples use:

```sh
rho build -target <board> <project>/boards/node_<number>
rho flash -target <board> -project <project>/boards/node_<number> <image>
```

A flash waits on its board's card, with that board's instruction, until you say
the board is in its programming mode — for a Pico 2 W, BOOTSEL: hold BOOT, tap
RST, release RST, then BOOT. The platform's flash entry then:

- shares the board with WSL when Windows has not yet (Windows asks an
  administrator once) and attaches it,
- uses passwordless `sudo` when it is already available,
- re-attaches the board after the reboot into BOOTSEL,
- falls back to the bootrom's drive.

When a flash succeeds the board runs what is planned and stops showing as out
of date; a started gateway likewise.

## Linked meshes

A mesh can report through another: in its gateway's inspector, **Reports
through** names the mesh whose gateway carries it. Its gateway then runs as a
relay (robohub-gateway's relay mode): it dials the parent gateway's uplink as a
board of the parent's mesh, with an id of its own (`relay_uid` in its plan) and
the key the parent derives for that id from its own gateway key, written beside
the child as `relay.key`. The parent listens on its uplink while a mesh reports
through it. Linking or unlinking changes both gateways' signatures, so both show
that a restart applies it.

The parent's agora shows each linked mesh as a node under its gateway that
opens to its own tree; the fleet says which mesh reports through which. A mesh
cannot report through itself or through a mesh that reports through it.

## Across meshes

- **Move to another mesh**, on a board's or device's Node tab, moves it with its
  id, its modules and its history; what hangs from it stays, under its parent.
  It hangs from the other mesh's gateway and is out of date until it is built
  with that mesh's key.
- **Copy as a template**, on a mesh's settings page, starts a new mesh with the
  plan — gateway, boards, modules, links — and its own area and ports, and
  nothing of what was deployed.
- **Apply in all meshes**, on the fleet's bar while changes are waiting, queues
  each mesh's jobs at once.
- The fleet's search finds a node by its name, id, board, module or device in
  every mesh, and opens it.

## Where the lists come from

Nothing in the planner names a board or a driver that a manifest does not.

- Boards are found by scanning the local registry for platform modules and
  reading the manifests under their `boards/` directories.
- A board declares its roles and hardware in `capabilities`:

  ```yaml
  capabilities:
    gateway: true
    relay: true
    node: true
    host: false
    wireless: true
    ethernet: false
    links: "uart console usb wifi socket"
    buses: "uart usb i2c spi onewire gpio adc pwm"
    deploy: "flash"
  ```

- A driver declares what it is and how it is deployed:

  ```yaml
  capabilities:
    sensor: true
    buses: "onewire"
    measures: "temperature"

  deployment:
    boards: "pico2w"
    example: "examples/temperature/ds18b20/c/telemetry"
    binary: "build/output/telemetry.elf"
    kconfig: "CONFIG_RH_HAL_ONEWIRE=y CONFIG_RH_BOARD_DS18B20_MODULE=y ..."
  ```

- A platform board also says how a gateway is deployed to it:

  ```yaml
  deployment:
    method: "adb"          # adb, ssh, flash or process
    arch: "arm64"          # for a cross-built gateway
    kconfig: "CONFIG_RH_BOARD_ARDUINO_UNO_Q=y"
    apps_dir: "/home/arduino/apps"
    user: "arduino"        # for ssh
    path: "/home/arduino/apps"
  ```

Adding a board or a driver to the ecosystem therefore needs no keeper rebuild;
the next page load discovers it. A transport appears when both ends declare it
and `robohub-transport` lists it.

## The gateway page

![The gateway page](./images/gateway.jpg)

The gateway's own page at `http://<gateway>:<rest>/` shows:

- mode, area, uptime, devices and frames,
- the latest values per node,
- a frame history, newest first, with selectable
  **10 / 25 / 50 / 100 / 500 / 1000** rows,
- **Export CSV** for the same window.

A node's `timestamp_ms` is its own uptime. The gateway records the wall-clock
`received_at_ms` beside it; the API and CSV keep both, and the page renders
`received_at_ms` as local date/time in the browser.

## Alerts and plugins

Mesh raises an alert when a step fails: the action, the stage and the level go
through the alert dispatcher configured in its `module.yml`. The dispatcher fans
out to the channels named in its targets; each channel has its own threshold.
The default is the local log, and e-mail, Telegram and webhook channels are
present but disabled until configured.

The alert rule engine in `contracts/alert/rule.rho` is available to application
logic that has a sensor reading: a pending window filters a spike, hysteresis
decides when the condition cleared, a cooldown recharges the rule, and a rolling
window limits repeated alerts.

## Logging

The keeper uses `rho.log` through the `logemit` port. Records carry a UTC
timestamp, a level and a subject:

```text
[2026-11-01T12:48:27.695Z] [info] [keeper] starting build_node on build
[2026-11-01T12:49:28.534Z] [info] [mesh] node 1 built
```

`RHO_LOG_FILE` also appends them to a file. The web console timestamps live
lines and the server-side is reset before each action.

## Troubleshooting

- **Gateway answers but no readings**: the board is running but its serial path
  changed. A Pico's USB CDC is renumbered by a flash; the Mesh app uses its
  stable `/dev/serial/by-id/...` path and attaches a Shared Pico to WSL.
- **Pico will not program**: put it in BOOTSEL (hold BOOT, tap RST, release
  RST, then BOOT), then press **The board is ready: flash it** on its flash job.
  Under WSL the flash shares and attaches the board; if Windows asks for an
  administrator, allow it once. The job's log says which way it was written.
- **A board build stops at the foreign interface or the target**: the
  `rho-cimport` and `rho-target` components installed are older than the
  board's manifest, which names dependencies under `${ROBOHUB_WORKSPACE}`.
  Install the current ones from the registry and enable them
  (`rho module install rho-target@<version>`, then `rho module enable`).
- **FAULT temperature**: the node is heard but the sensor is not answering.
  Check the DS18B20 wiring (GP28, 3V3, GND) and its 4.7 kΩ pull-up. The node
  retries the sensor every 5 seconds.
- **The page looks stale after a rebuild**: hard-refresh the browser and restart
  the Mesh process. Templates are embedded in the binary.
- **The native gateway address**: `127.0.0.1` on the Mesh host, and the
  host's network address for another machine.

## Tests

```sh
rho test .
```

The Rho unit suites check the string helpers and the domain: the plan as text,
the transport rule, and the signatures that say what a board's image is built
from. The smoke suite builds Mesh and walks it over HTTP: a mesh planned in
place (gateway, boards, modules, links), the bus plan and its conflicts, the
plan's revisions, numbers kept across a delete, the old addresses, the fleet
and its ports, editing with stale saves refused, a native gateway started as a
job and found online, and a Pico 2 W board built in the background with its
flash waiting for BOOTSEL.

### Gateway outage behaviour

Mesh backs off a gateway that does not answer: the delay starts at two seconds,
doubles to the configured maximum, and resets on the next successful poll. The
first failure in an outage is recorded as an actionable telemetry fault so the
fault page and Keeper loops see it without a repeated log line every two
seconds.

`GET /mesh/api/projects/{project}/telemetry/gateway` reports the backoff,
next poll time, last success, whether a fault is open, and the last error. The
maximum backoff is set in Mesh Settings → Storage.

The gateway keeps its own sequenced outbox, so a Mesh outage does not require
the gateway to discard frames; Mesh's cursor and unique key already make
repeated pages idempotent. Node-side buffering and gateway outbox retention are
the remaining store-and-forward pieces, and belong to the firmware/gateway
repositories because they run before this worker sees a frame.

## Connecting another application

A project's settings name the gateway URL that project owns. The project list is
served at `/mesh/api/projects`; a project's gateway is served at
`/mesh/api/projects/{project}/gateway`, and its devices at `/mesh/api/devices`.
Another application can point at Mesh, choose a project, and learn the gateway
automatically without storing an address of its own.

A project's signals are served at `/mesh/api/projects/{project}/sources`, and
the active project's at `/mesh/api/sources`. A source names its owner, signal,
direction (input or output), unit, the field it reads, the widget kinds it can
feed, and its value and action paths. `/value` returns a plain reading;
`/action` drives an output switch. The contract is the `signals:` list in a
device integration's or board's `module.yml`, so a new part is visible to a
dashboard without the dashboard knowing its type. A signal is listed even when
the gateway does not carry its field yet; its value is empty until the node or
device reports it.

## Application clients

An application that reads a project registers itself instead of being known to
Mesh in advance. The handshake is HTTP because the gateway provider is already
an HTTP client and the applications are separate processes; the same facts a
socket frame would carry travel in the registration.

```http
POST /mesh/api/clients/register
Authorization: Bearer <mesh service token>

{"app_name":"robohub-energy","project":"default",
 "instance_id":"energy-8482",
 "callback_url":"http://127.0.0.1:8482/api/energy/mesh/events",
 "topics":"energy.commands,grid.status.*"}
```

Mesh stores the client, the project it attached to, its callback and its
last-seen time, and returns a per-client secret.

The token that authorizes this is **per application and per project**. The
Mesh project settings page has an *Application tokens* table: generate a token
for the application name, choose its lifetime, then copy it into that
application's Mesh token setting. A project can hold several of these tokens, so
the aquarium and energy tokens are independent; rotating or revoking one does
not affect the other. A token is scoped to the project, so the application's
project list shows that project only. A deliberately broader `mesh:*` token is
an administrative choice, not the default. Every later call carries the
service token; every callback Mesh makes carries
`Authorization: Bearer <client secret>`, so an application callback is not open
to any local process.

```http
POST /mesh/api/clients/heartbeat
{"client_id":"robohub-energy-energy-8482"}

POST /mesh/api/clients/publish
{"project":"default","topic":"energy.fault",
 "payload":"South roof: back-sheet temperature above limit"}

GET /mesh/api/clients
```

`GET /mesh/api/clients` lists each client with its project, topics, last-seen
time and online flag. A client is online when its last heartbeat is less than
ninety seconds old, so a crashed application disappears without a separate
unregister.

Topic matching is exact or a trailing `.*` prefix match: `grid.status.*` matches
`grid.status.online`, and `energy.*` matches every energy topic. Publishing
routes to the clients whose project and topics match and does not send the
message to anyone else.

The aquarium registers with `energy.fault,energy.mode`; energy registers its own
callbacks and publishes faults and mode changes. The context is in the client
list, which is what Keeper uses to show who is online and which project each
application is attached to.

## Telemetry store

Mesh keeps a durable series of what the project's gateway records. The gateway
exposes a sequenced outbox; Mesh's background worker polls every project's
gateway, writes a page in one transaction, advances the cursor and acknowledges
the sequence. The unique key is `(gateway_id, seq)`, so a repeated page after a
failed acknowledgement is ignored rather than stored twice.

Mesh is the only telemetry store in the installation: applications read it
through `/mesh/api`, never by opening its file. A project's gateway may be another
service that produces readings, such as energy: the project names its
`gateway_audience`, and Mesh polls it with a short-lived service token for that
audience with the `<audience>:gateway` scope.

The store lives at `$RHO_TELEMETRY_DB` (or `$ROBOHUB_MESH_TELEMETRY_DB`), or
`~/.rho/mesh/telemetry.db` when neither is set. It is SQLite in WAL mode
with a series index, a latest-value table and one-minute and hourly rollups.
Raw readings are kept for the project's `telemetry_retention_days` (30 by
default); minute rollups are kept for 180 days and hourly rollups for five
years, so an old range still has a series. A project's `gateway_token` is the
bearer token its gateway requires, and `ROBOHUB_GATEWAY_TOKEN` is the fallback
for a deployment that uses one token everywhere. The schema is
`db/schema.sql` and the host half is the `telemetry` port.

An application reads it through the gateway contract, or directly through Mesh:

- `GET /mesh/api/projects/{project}/telemetry/status` — rows, newest time and
  gateway count for the project.
- `GET /mesh/api/projects/{project}/telemetry/latest?source=<source>`
- `GET /mesh/api/projects/{project}/telemetry/series?source=<source>&from=<ms>&to=<ms>&step=<ms>`
  — raw readings when `step` is absent or zero, averaged into buckets otherwise.

A source is the same ID `/sources` publishes: `<node id>.<signal>`, such as
`ba06f37ca34ba32c.power`. Every node in the plan has an id, made once when it is
first needed and kept in the plan; it moves with the node when the plan is
reordered and goes with it when it is removed, so a node added later is never
given another's history. A board opens its session with its id, and its
readings are kept under it. The older form, `<kind>.<index>.<signal>`
(`device.3.power`), still resolves and is published as `legacy_id`, so a
dashboard saved before keeps working. The object also carries `module_id` (the
device integration or board), `driver_id` (the driver that produces the signal,
when one does), `source` (`<driver_id or module_id>.<signal.id>`), `project` and
`instance`, which is the node id.

The store keys a series by source and field, never by area and node. Mesh tells
it where each board and device sits (`telemetry_source`), so a reading that
arrives without its board's identity is still placed, and a store written
before sources is migrated in place, each old series adopted by the node at its
area and node. A board that reports from a place the plan gives another is
recorded as a fault and kept as its own series, never merged. Its object carries `history_path` and, when Mesh
knows its own origin, `history_url`, so a dashboard can ask for a chart without
knowing the area, node or field the reading was stored under.

Mesh records the URL of a native or SSH gateway it starts in the project's
`gateway_url`, and passes the gateway its tree position (`GATEWAY_MODE`) and
`GATEWAY_AREA`, so a project's gateway and the project's plan agree. A gateway
reached over ADB still needs its address set in the project. A network device's
readings are stored under the id the gateway assigned it; a device source is
matched to that id by the name and address in the plan, so its chart does not
need to know the gateway's numbering.

## Building a node from modules

A node is built from the modules in the plan, not from one driver's example.
Mesh copies the telemetry node template, writes `src/sensors.c` from the
descriptors in `robohub-drivers`, adds every compatible module and its driver
to the build, writes the Kconfig the board and modules need, and builds the
image for the node's board.

The descriptors are the contract:

- a driver names its device type and one `signal|field|unit|type|member|read|output`
  line per readable signal under `telemetry_fields:`;
- a board module names its BSP header and entry point under `telemetry:` and
  carries the signals it exposes under `signals:`.

The generated field is `<tag>.<field>`, where the tag is the start of the
module's own id. Two modules that both measure temperature on one node therefore
become two series, and a module put where a removed one was is a new series.
Firmware built before the tag wrote `m<node-index>.<field>`, and older firmware
the bare field; a source reads whichever of the three its board reports.
The source object also names the board (`module_id`) and the driver
(`driver_id`), which is what makes the series identifiable when two
repositories use the same module name.

Every board module in `robohub-drivers/boards` appears in the picker, including
relays and transceivers. A module with no read path is linked and configured
but contributes no reading. The dual-axis joystick is the one board whose
descriptor is not yet expressible, because it presents two analog axes and a
button through three separate init calls.

## Device telemetry

Devices from `robohub-devices` are described the same way as drivers: each
manifest names the interface it presents and, for an HTTP device, the path to
read and one line per signal.

- **HTTP devices** — the Shelly plug and a Hoymiles through an OpenDTU — are
  polled by Mesh's background worker. The response is read by JSON path, scaled
  to the unit the signal declares, and written to the telemetry store under the
  device node's plan identity: project area and node index.
- **Modbus and GPIO devices** — a Growatt or a relay — are carried by a device
  node (`robohub-devices/nodes/gateway-node` or a composed application). That
  node publishes `AREA` and `NODE` from the plan; the device page shows the two
  values, and the gateway outbox turns the frame into one series per signal.

A device source (`device.3.pv_power`) resolves to the same area, node and field,
so a chart and the live value read the same series. The device page's Poll
button uses the same descriptor as the background poller; it is not Shelly-only.
Switch actions appear only for a plug, switch or actuator, so an inverter does
not offer a meaningless switch button.
## Scope enforcement

Every `/mesh/api` handler verifies the authenticated token's audience and
project scope. A service token must carry `mesh:<project>`, `mesh:*`, or `*`;
a browser session behind the keeper is exempt. Mint a scoped service token with
`rho auth ticket --scopes mesh:default`. A token that was revoked is refused
without consulting the keeper: the middleware reads the same `revoked.txt` the
`rho auth revoke --jti` command writes.

## Authenticated boards

Every frame between a board and its gateway is authenticated (robohub-transport's
`secure.h`). Mesh makes the keys and keeps them out of every repository:

- each gateway project has a key, made the first time it is needed and kept in the
  project's folder (`gateway.key`), readable by Mesh's user only; Mesh installs it
  with the gateway it starts, pushes or deploys, as `GATEWAY_KEY_FILE`;
- each planned board has an id (`node.<n>.uid` in the plan), made once;
- a board's key is derived from the two and written into its build as
  `src/robohub_secret.h`, which the firmware includes when it is there and no
  repository tracks.

A board built that way says hello to its gateway, opens a session, and sends only
sealed frames; its gateway accepts nothing else unless `ALLOW_PLAIN_FRAMES=1`.

