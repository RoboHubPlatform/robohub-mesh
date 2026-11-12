# Changelog

All notable changes to this module are recorded here, one section per release.

## [0.1.0] - 2026-11-01

The RoboHub Mesh application, extracted from the keeper. It runs on its own
port and is either started directly or launched from the keeper's applications
registry, where it declares `kind: keeper-hosted`.

It plans a gateway and its nodes, records each project in a workspace folder,
configures the connections between them, builds and programs the firmware, and
verifies the running deployment. Boards, drivers and transports are discovered
from the local registry at runtime.

The application has its own left menu: Dashboard, the active project's
operations, every project in the workspace, and its documentation. A project is
activated by opening it, from its card or from its settings, and made inactive
again from the same places; with no active project the Operations group is
hidden and the step pages return to the dashboard. The menu also returns to the
keeper when the keeper started the application.

## [0.2.0] - 2026-11-02

A node is one computer, and a sensor module is a node of its own (`kind: module`)
attached to it, rather than a field on the node. Module nodes appear in the plan
tree under the node they extend, are added with **+ Add module** or **+ module**,
and are never built or programmed separately: the node's image carries them. A
native gateway cannot take modules; the controls appear only when the gateway is
a real board.

The module picker is read from `robohub-drivers/boards` on every load, grouped by
board category. Each module declares its bus and, where it matters, its I2C
address, address range or SPI chip select; the node page shows the resulting bus
plan. 1-Wire and I2C lines are shared, an SPI device gets its own chip select,
and an address or exclusive instance that is already in use is reported and left
out of the image so the parent node always builds. The attached modules are
linked into the node's image.

A failed plan, build or verify action raises an alert through the application's
configured dispatcher. The local log channel is enabled by default; e-mail,
Telegram and webhook channels are declared but disabled until configured.

## [0.3.0] - 2026-11-02

Device nodes. A device from `robohub-devices` is a node of its own in the Mesh
tree, attached to a gateway or a relay; it is not built or programmed. The
editor discovers device integrations from the checkout; the Shelly Plug M Gen3
is the first, over HTTP RPC or MQTT. An HTTP device can be polled and switched
from its node page; an MQTT device is carried by its relay or gateway.

## [0.3.1] - 2026-11-02

Device nodes are reachable from the tree and from a native gateway too:
"+ Add device" is beside "+ Add node", and a compute node offers "+ device"
beside "+ module". A native host can carry network devices even though it has
no pins for modules.

## [0.3.2] - 2026-11-02

Device picker reads the `devices:` list in robohub-devices' module.yml, so
every device the collection carries is offered and a new one appears on the next
render. The transport list includes Modbus, GPIO and UART alongside HTTP and
MQTT.

## [0.3.3] - 2026-11-03

A device can be **free standing**: its parent is the mesh/network rather than a
gateway or relay. It appears at the root of the tree, is not carried by a node,
and is reached directly over its own transport.

## [0.3.4] - 2026-11-03

Mesh settings name the gateway URL; `/mesh/api/gateway` serves it while the
gateway answers, and `/mesh/api/devices` serves the device nodes. Another
application can connect to Mesh and discover where the devices are.

## [0.3.5] - 2026-11-03

The gateway belongs to a project, not to the Mesh application. A project's
settings carry its gateway URL; `/mesh/api/projects` lists the projects,
`/mesh/api/projects/{project}/gateway` serves that project's gateway while it
answers, and `/mesh/api/devices` serves its device nodes.

## [0.4.0] - 2026-11-03

Mesh has the same visual language as the Aquarium: a photographic background,
a fixed top bar for the operations and documentation, and a fixed bottom bar
that names the active project and lists the workspace's projects. The left
menu is gone.

The plan, build and network tree is now the centerpiece: a branching graph of
frosted cards connected by the transport on each branch, with the gateway at
the root. Node, module and device cards keep their role, board and status
information.

Form pages — node, module, device, gateway, project settings and the guide —
are wrapped in the same frosted panel, so the background never shows through
their labels and tables.

Accepting a topology no longer treats a module or a device as an incomplete
compute node: only compute nodes are asked for a role, board and link. A
free-standing device now branches from the gateway beside the relay nodes
instead of stacking under the first branch. Deleting a node also carries a
device's integration, transport, address and network settings to the row that
takes its place.

The dashboard now shows the last gateway action. A failed restart is a red
notice with the tail of the build output and the gateway log, and a restart
pins the build step so that output is captured in the file the notice reads.

A project publishes its signals for another application: devices, modules and
sensors each contribute the inputs and outputs their `signals:` contract names,
with direction, unit and the widget kinds they can feed. The values are served
by Mesh, which reads the project's gateway, so a dashboard does not need the
gateway address or a part's type.

## [0.4.1] - 2026-11-04

Mesh signs browser users in through Keeper and accepts signed service JWTs for /mesh/api, so Aquarium server-to-server calls keep working with authentication enforced.

## [0.5.0] - 2026-11-05

Device telemetry follows the same descriptor model as sensors. Mesh polls HTTP
devices from `robohub-devices` using the manifest's `telemetry.http_path` and
`telemetry_http_fields`, parses the document by JSON path, scales the value, and
stores it under the device node's plan identity. A Modbus or GPIO device
publishes through its device node under the same `AREA` and `NODE`, which the
device page displays. The device source resolves to that series, and the Poll
button and switch controls are driven by the device descriptor rather than a
single Shelly model.

A node is built from every compatible module in the plan rather than from one
driver's example. Mesh copies the telemetry node template, generates
`sensors.c` from each driver's `telemetry_fields` and each board module's
`telemetry`/`signals` descriptors, and prefixes a module's field with its plan
index so two sensors measuring the same quantity on one node stay two series.
The module picker now includes output and transceiver boards too; a module with
no read path is linked and configured but contributes no reading.

Mesh keeps a telemetry store for the other RoboHub applications. A background
worker polls each project's `robohub-gateway` outbox, writes a page in one SQL
transaction, advances the project's cursor and acknowledges the sequence. The
store is SQLite in WAL mode with a series index and a latest-value table, and a
retention pass removes raw readings older than 30 days.

`/mesh/api/projects/{project}/telemetry/status`, `/latest` and `/series` serve
the store, and a source object now carries `history_path` and `history_url`. The
gateway contract gained `gateway_source_history` and `gateway_telemetry_status`,
so an application can chart a source without knowing where the reading was
stored. Mesh also passes the gateway its mode and area and records the URL of a
native gateway it starts.

The feed can require a bearer token: a project's `gateway_token` (or
`ROBOHUB_GATEWAY_TOKEN`) is sent to the outbox and passed to a gateway Mesh
starts. Retention is per project (`telemetry_retention_days`), and the store
keeps one-minute and hourly rollups after the raw rows are pruned. A network
device's readings are matched to the id the gateway assigned it by the device's
name and address in the plan.

## [0.5.1] - 2026-11-06

Security: per-project scope enforcement on all Mesh API handlers. Device HTTP
polling supports per-device bearer tokens. Recovery mode added to generated
node firmware (re-init on sensor failure). Frames no longer polluted by sensor
entries. Chart widget now handles decimal values and limited time window.

## [0.5.2] - 2026-11-08

English/Romanian UI and settings, host gateway/auth/alert/storage tabs, and
HTMX/SSE updates for the tree and actionable faults. Telemetry now bounds WAL
and retention, supports incremental/full vacuum, exposes storage status, stores
faults in a dedicated table with live events, and backs off a gateway that stops
answering.

## [0.5.3] - 2026-11-09

Applications can register as clients of a project, subscribe to topics and
publish messages. Mesh stores the client, its callback, its project and its
last-seen time, and routes a published message only to the clients whose
topic patterns match. Registration and publishing use a Mesh service token; callbacks carry a
per-client secret. Project settings manage one token per application: each is
scoped to the project, has its own expiry, can be copied or revoked, and does
not replace another application's token. The project list is filtered to the
projects the calling token can access. The project-context items (plan, network,
build, verify, gateway and project settings) moved from the top menu to the
bottom-left beside “Operations for …”; the top menu keeps the global items and
both settings links read simply “Settings”. Settings has a Logging tab: level,
text/JSON, a rotating file under the user state directory, size, retention and
stderr.

## [0.6.0] - 2026-11-12

Mesh lives in its own repository, robohub-mesh, with its history. It builds
beside a robohub checkout: the modules it declares under `deps: robohub:` are
found in the workspace's robohub repository, and the keeper finds it through
the orchestrator's registry.

Forms answer with a redirect, so a reload never repeats an action, and each
outcome is shown once. A name, a description or a device address is saved
without reopening the topology; a change to board, parent, link, modules or
device still reopens it, and the form says so before it is saved. Back links
lead to the plan. The node form keeps a link set in the network step, a console
link keeps its baud, devices are no longer offered as module parents, and fault
Clear posts to the route that exists.

## [0.6.1] - 2026-11-12

Mesh imports the alert interface instead of including the alert contract, and
binds its channels in `module.yml`; the alert plugins it named are gone. The
Settings page's Alerts tab saves each channel's settings through the binding,
including whether e-mail and Telegram are on, and the Configuration tab lists
the bound channels. The alert rule tests moved to the interface, and the smoke
test saves the Alerts tab and reads the setting back.

## [0.7.0] - 2026-11-12

A project's plan is plan.yml in its folder: the nodes, links, modules and steps
as a YAML document a person can read, diff and correct by hand, written by Mesh
in one step. Every save raises the plan's revision and keeps the plan it
before each edit under revisions/ (the last twenty), and a save changes the plan
under its lock, so two saves at once no longer lose a key. A project written before
plan.yml is imported the first time Mesh starts, and its project.txt is kept as
project.txt.imported. The pages read and write the plan as before.

Every node has a number of its own, which its firmware (CONFIG_RH_NODE_ID) and
its telemetry carry. It stays with the node: removing a node moves the rows
after it up, in one write of the plan with every key they have, but not their
numbers, so a board that is already flashed still reports as itself. A node
planned before numbers existed keeps its row number, the one it was flashed
with. Removing a node used to copy a fixed list of fields row by row, one save
per field, and lost any field not on the list.

Several meshes run side by side on one machine. fleet.yml, in Mesh's state
folder, gives each mesh an area and a REST and an uplink port no other mesh on
the machine has; a project's own plan values win, and a mesh deployed before
the fleet existed keeps the defaults it was built with while they are free. A
native gateway is started with its process id written beside its project and
stopped by that id, so stopping or deleting one mesh no longer stops every
gateway on the machine, and each mesh has its own gateway log.

plan.yml names a node's parent by the parent's number (`parent_node: 7`), which
stays with the parent whatever happens to the rows around it; the pages still
read and write the row form, translated on every read and write. A plan written
before is converted the first time it is saved.

The planning rules have a home of their own, src/mesh/domain/, with unit tests:
the plan as text (the stored and the row form, numbering, removing a node), the
rule for which link joins two boards, and the impact table that says which node
fields a build depends on and which steps reopen after which.

An operation link names its project (`?project=`), and opening it makes that
project active, so a page can be bookmarked or shared and shows the mesh it was
for. Every form carries its project, and a save drawn for another project than
the active one — a second tab switched projects since — is refused rather than
written into the wrong mesh.

## [0.8.0] - 2026-11-12

The live agora and the fleet view, read-only. A mesh's agora (`Live` in the top
bar, `/mesh/agora?project=<id>`) shows whether its gateway answers, its area and
the boards that need building; the tree of its gateway, boards and devices, each
card with its online state, when it was last seen and its newest readings; an
inspector for the node selected, linking to the page that edits it; and a strip
with its faults and its steps. The fleet view (`Fleet`, `/mesh/fleet`) shows one
card per mesh with its miniature tree, health, readings and where its gateway
runs, one inbox of faults across them, and the hosts they run on. Both update
over one server-sent stream each.

Every mesh's gateway is checked in the background every six seconds and the
answer kept beside the project, so drawing a page no longer waits on a gateway.
A mesh gets its area and ports when it is created. The smoke test has Mesh start
a native gateway on a port of the test's choosing and checks that the agora and
the fleet see it online and that stopping it ends that process.

## [0.8.1] - 2026-11-12

The agora starts, rebuilds and stops a native gateway, and a restart builds it
from its sources every time instead of starting the last build. A plan that
names no gateway is a native one in the agora as everywhere else, and a native
gateway Mesh has not started is shown as not started rather than polled and
reported unreachable. Faults say what failed in words, with their age. The
fleet is one panel as proposed: a card per mesh with its tree drawn, the inbox
and hosts beside, and the fleet's activity below.

## [0.9.0] - 2026-11-13

Editing in place. Selecting the gateway, a card or a link on the agora opens
the inspector beside the tree, and every edit the wizard's pages offered is made
there: a board's name, role, board and parent, its link and the link's settings,
the modules it carries, a device's integration and address, a new node, device
or module from + on any card, and a delete that lists what goes with it and asks
where what hangs from it moves. The project and the selection are in the
address (/mesh/p/<project>?select=node:<id>). An edit builds nothing and resets
nothing: each board's signature (what its image is built from) is recorded when
it is programmed, and a board whose plan differs shows as out of date with the
reason. A save from a form opened before the node's last save is refused.

## [0.10.0] - 2026-11-13

Apply changes and jobs. The agora's bar offers "Apply changes" when boards or
the gateway differ from what they run; applying queues jobs: build each changed
board, restart or program the gateway, then flash each board. Jobs are shell
scripts beside the project, each with its own log, run in order by one runner
per mesh, so no page waits on a build and one mesh's build never waits on
another's. A flash waits on its board's card, with the board's own instruction
(BOOTSEL for a Pico), until the person says the board is ready; it can be
cancelled, and a failed job tried again. The background worker records what a
board runs once it is flashed, and the gateway's plan once it started. Boards
are built in a folder of their own under each project.

## [0.11.0] - 2026-11-13

The wizard is retired. /mesh is the fleet, with the form that starts a new mesh;
a mesh opens as its agora at /mesh/p/<project>, and the wizard's step, network,
build and node addresses open the agora with what they showed selected. A new
mesh is guided on its own canvas: where the gateway runs, the first node,
apply. The menus name Live, Gateway and Apply changes for the open mesh instead
of the steps. On a narrow screen the inspector is a sheet at the bottom. The
wizard's pages, its step documents and the code only they used are gone; what
remains of mesh.rho and wizard.rho is split into store, catalog, build, views
and api.

## [0.12.0] - 2026-11-13

Linked meshes and work across meshes. A mesh reports through another when its
gateway's inspector says so: its gateway runs as a relay of the parent's, with
an id of its own and the key the parent derives for it, and the parent listens
on its uplink. The parent's agora shows the linked mesh as a node that opens to
its tree, and the fleet says who reports through whom. A board moves to another
mesh with its id, modules and history; a mesh is copied as a template; the
fleet applies every mesh's changes at once and searches nodes in every mesh, and
its inbox lists running and waiting jobs. Needs rho-robohub 0.4.2.
