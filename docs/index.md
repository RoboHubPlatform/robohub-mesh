# RoboHub Mesh

The Mesh application plans and runs deployments: a gateway, the nodes that
report through it, the connections between them, and the firmware on their
boards. It is the part of the ecosystem that knows about boards, not only about
modules.

It is a keeper-hosted application: it runs on its own port, standalone or
launched from the keeper's `/applications` registry. The page is at `/mesh`; the
plan wizard for a project is at `/mesh/plan`.

The left menu belongs to the application. It has Dashboard, a Projects group
with every project in the workspace, and Documentation. When a project is
active, an Operations group appears, headed by the project it acts on, with
Plan, Network, Build & program, Verify, Gateway and that project's settings.
An active project is marked **Active** in the project list; the others are not.

A project is made active by opening it, by **Set active** on its card, or from
its settings page. **Make inactive** clears the selection: the Operations group
then disappears and the plan, network, build and verify pages return to the
dashboard, while the projects remain listed. Creating a project when none is
active makes the new one active.

When the keeper started the application, the menu also has **Go to Keeper** at
the top, which returns to the keeper that launched it.

## Projects

A mesh is saved as a **project**: a folder in a workspace.

| What | Where |
|---|---|
| Default workspace | `~/.rho/mesh/projects` |
| Override | `ROBOHUB_MESH_PROJECTS` |
| Project settings | `<workspace>/<project>/project.txt` |
| Current project pointer | `RHO_KEEPER_STATE_DIR/current-project.txt` |

A project's folder holds its plan, network, build and verify state. Renaming a
project changes its display name, not its folder; the folder is the stable
location a person can open and copy.

The dashboard lists every project with:

- its name and folder,
- the gateway recorded for it and the address it answers on,
- running / not-running status,
- the last time the gateway received a frame,
- **Restart**, **Stop** and **Delete**, and a link to **Project settings**.

**Create project** makes a new empty project and opens its plan. **Open** on a
project loads it as the current project and opens its plan. The dashboard does
not show the Plan/Network/Build/Verify steps; those belong to the project being
created or edited.

Deleting the current project selects another project; deleting the last one
leaves no active project. If the deleted project's native gateway is running it
is stopped first.

Every wizard page carries a **← Mesh dashboard** breadcrumb.

## Plan and topology

The plan is a tree of cards:

- the gateway is the root,
- every child is a node shown under its parent,
- the branch badge is the connection and names the transport,
- each card carries the node's role and status.

A card opens the node or gateway editor. A node is one computer: a user-chosen
name, a short description, a role (`node`, `relay` or `gateway`), a board, a
parent and a link. Boards are filtered by the role.

### Nodes and modules

A sensor is not a node. It is a module fitted to a node, and a node can carry
any number of them. The editor's **Attached modules** list is read from
`robohub-drivers/boards` on every render, so a board added to that checkout
appears without rebuilding Mesh or the keeper. Output and transceiver boards are
left out — they are boards, but not sensors fitted to a node. The list is grouped
into collapsible sections by board category, each with its own colour and three
cards to a row; a section with a module selected stays open and shows the
selected names in its header. The first attached module names
the example the node's image is built from; the node is the only thing built and
programmed, and the image carries the modules fitted to it.

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

The node page shows a **bus plan**, which is how several modules become one
node:

- 1-Wire modules share `onewire0`.
- I2C modules share `i2c0`; two fixed addresses on the same bus conflict, and a
  module with an address range is assigned one that is free.
- SPI modules share the bus lines and each gets a distinct chip select.
- ADC, PWM, GPIO and UART instances are exclusive.

A conflict is shown on the node and that module is left out of the image, so the
parent still builds. A module that says nothing about its bus is listed without
a plan rather than guessed at.

**Accept topology** locks the tree and opens Network. **Edit topology** unlocks
it again; editing resets the network, build and verify steps that depended on
it.

## Network is about connections

Once the topology is accepted, every branch is editable as a connection. Its
transport badge opens that connection; the node card opens the node's network
page, which lists its incoming connection and the connections to its children.

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

The gateway's own page sets the gateway host and REST port and lists its
outgoing connections. **Accept network** unlocks Build & Program; editing any
connection locks it again until it is accepted.

## Build and program

The build step is the same tree with a status on every card:

`not built`, `built`, `programmed`, `running` or `failed`.

- Clicking a node offers **Build node** and **Program node**.
- Clicking the gateway offers **Build gateway** and **Program gateway**. Program
  builds first when needed, then deploys or starts the gateway.
- The gateway page can change the gateway to another gateway board, or to
  `native` (this host). Changing it resets the network and build steps.

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

Before a Pico 2W is programmed, its page shows the boot-mode instruction and
requires the acknowledgement. The platform flash entry:

- attaches a Shared board to WSL for PICOBOOT,
- uses passwordless `sudo` when it is already available,
- re-attaches the board after the reboot into BOOTSEL,
- falls back to the bootrom's drive.

The console follows the action over Server-Sent Events. Each action has its own
progress file, so a previous run cannot replay into a new one. The console keeps
the latest line in view until you scroll away and has **Clear console**. Long
actions are not cut off by a server write or read deadline.

## Verify

Verification asks the gateway for its nodes and diagnostics:

- the gateway must answer,
- at least one reading must be present.

A native gateway is verified at `127.0.0.1`; a board gateway at the address
saved for it. If the gateway answers but no reading has arrived, the step says
so; check the connection's parent serial port and that the node is running.

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

The gateway's own page at `http://<gateway>:<rest>/` shows:

- mode, area, uptime, devices and frames,
- the latest values per node,
- a frame history, newest first, with selectable
  **10 / 25 / 50 / 100 / 500 / 1000** rows,
- **Export CSV** for the same window.

A node's `timestamp_ms` is its own uptime. The gateway records the wall-clock
`received_at_ms` beside it; the API and CSV keep both, and the page renders
`received_at_ms` as local date/time in the browser.

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
- **Pico will not program**: put it in BOOTSEL (hold BOOT, tap RST/EN, release),
  tick the acknowledgement, and press Program. The page detects when the board
  is already in BOOTSEL.
- **FAULT temperature**: the node is heard but the sensor is not answering.
  Check the DS18B20 wiring (GP28, 3V3, GND) and its 4.7 kΩ pull-up. The node
  retries the sensor every 5 seconds.
- **The page looks stale after a rebuild**: hard-refresh the browser and restart
  the Mesh process. Templates are embedded in the binary.
- **The native gateway address**: `127.0.0.1` on the Mesh host, and the
  host's network address for another machine. The dashboard shows both.

## Tests

```sh
rho test applications/mesh
```

The Rho unit suite checks the wizard's string helpers. The smoke suite builds
Mesh, walks its pages over HTTP, saves and accepts a two-node
topology, edits a connection, creates, renames and deletes a project, checks the
locked tree, and reads the event stream.
