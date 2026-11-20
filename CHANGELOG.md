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

## [0.13.0] - 2026-11-13

The agora looks like the proposal. It is one panel the height of the window: a
bar with the mesh (a switcher), whether its gateway answers, its area, what is
out of date and Apply changes; the canvas on a dotted grid with Fit, zoom, Add
node and Add device; the inspector as a column on the right; the activity strip
below. Cards are compact — a name, one pill (building, waiting for you, needs
build, online, quiet), one line of what it is and when it was last seen — and
carry their modules as chips with their newest reading, or a device's readings;
modules are no longer cards of their own. Link badges say the transport and its
baud or port. The inspector has a kicker and a title, dark full-width fields
with small-caps labels, board tiles filtered by what the parent can talk to, the
transport as a segmented choice with the impossible ones crossed out (a new one
is shown with its settings before it is saved), an "Applying this change" box,
and Discard and Save at its foot. A module is fitted from a searchable list by
category, one click each, with its summary; the fitted ones are rows with the
bus they take. Apply lists each change and runs its jobs.

## [0.14.0] - 2026-11-13

A node is moved by dragging it onto its new parent: the parents it may move
under are outlined while it is dragged and the others fade, and a drop moves it,
keeping its link where the new parent can serve it. "Applying this change" is
worked out from the form as it stands each time a field changes — every board
it would rebuild and why, and whether the gateway restarts — or says nothing is
rebuilt. Apply changes has Review diff: each part of each board and of the
gateway, as it runs and as it is planned. On a narrow screen the tree scrolls
from its left, the tools sit above it, and the inspector folds to its title.
The registry's lists are read at most every fifteen seconds, so a page that
asks for them many times is quick.

## [0.15.0] - 2026-11-13

The activity strip shows a build's progress and the last job finished; the
gateway's card counts its sealed links. A finished or failed job, and a flash
waiting for the person, is a toast on every open page, through the shared
notifications module. The pages around the agora — project settings, faults,
the shared settings — have its buttons and say times as ages. The agora, the
fleet and search are in the viewer's language for every whole label in the
catalog (locales/phrases.<locale>.txt, Romanian first), including what the
live stream redraws.

## [0.16.0] - 2026-11-13

Mesh draws the RoboHub top bar from the shared `robohub.ui` module: the brand,
the app switcher — filled from the keeper, so the keeper and every other
application are one click away — Mesh's own places in the middle, and the
person's menu with the language and signing out at the keeper. The keeper link
in Mesh's own menu is gone; the switcher is the way there. Mesh stays dark.

Mesh follows the grants in a person's ticket: `mesh:*` is everything, `mesh:read`
reads every mesh, `mesh:<mesh>` reads and changes that mesh and
`mesh:<mesh>:read` reads it. Someone granted particular meshes sees a fleet of
those, without the live stream of the others, and is refused the rest; a new mesh,
copying one, applying in every mesh and Mesh's settings need `mesh:*`.

Mesh asks the keeper for the orchestrator's registry (`/api/v1/registry`, with
its own client credential) instead of reading the orchestrator's checkout, and
keeps the answer for a minute — and while the keeper cannot be reached. Started
without a keeper, it reads the checkout as before. Settings says which.

MQTT devices: a device on `transport: mqtt` is reached through the bus
(robohub.bus, bound to the keeper's broker), its address being its topic prefix — its
readings from the newest status it published, its switch by publishing what its
manifest says. What a device can be told is its manifest's `actions` for both
transports, so Shelly's Switch.Set is no longer written into Mesh, and a device read
directly is read by its manifest's HTTP path and field map.


## [0.17.0] - 2026-11-13

Each project's live telemetry feed is also relayed on the bus, as
`mesh/<project>/telemetry`, a line per message exactly as the HTTP stream sends
it. An application whose gateway provider uses the broker (robohub-provider-mesh
0.4.0, `transport: auto` or `bus`) follows that topic instead of holding an HTTP
stream to Mesh. Mesh's broker user may now publish there.

## [0.18.0] - 2026-11-13

`GET /mesh/api/projects/<project>/nodes` is a project's tree as an application
sees it: the gateway and every node under it — boards, modules and devices —
each with its stable id, its parent's id, what it is (its board, device or
module, its transport) and its state. The gateway interface's `nodes()` reads
it, so an application can draw the mesh (energy draws it from the energy side)
without reading the plan.

## [0.18.1] - 2026-11-13

Security fixes from the 2026-11 review.

- `shell_quote` escapes a quote inside a value; before, a value holding one ended the quoting, and a gateway host, a deployment path or a port setting could run commands as Mesh's user. The values inside the commands run on a remote host (ssh, adb) are quoted as well, and a deployment's user and host must be plain words and its path a path, so neither can become an option of ssh.
- curl is run with one argument per line and no shell (`rho_exec_args`): a client's callback URL, a device's address or a gateway URL can no longer add options. A callback is only http or https.
- Client and token records are written readable by Mesh's user alone (0600). A client secret is 16 random bytes or registration is refused; the time-based fallback is gone, and the file openssl writes it to is emptied after reading.
- `same_origin` refuses a form posted from another origin.

## [0.18.2] - 2026-11-13

The clock is read from the process (`rho_now_s`, rho 0.6.0) instead of from `date` written to a file and read back, which a request and the background loop could truncate under each other: entries dated 1970 and modes that expired at once. Built on provider-mesh 0.5.1 and rho-sql 0.2.1.

## [0.18.3] - 2026-11-13

Built on provider-email 0.2.1, provider-webhook 0.2.1 and provider-log 0.2.1: alert headers that cannot be injected, an SMTP password off the command line, webhook failures reported as failures, a log that rotates again.

## [0.18.4] - 2026-11-13

Access and housekeeping fixes from the 2026-11 review.

- A signed-in request is held to its grants: one whose ticket carries no scopes may do nothing (it was treated like a path the middleware does not guard, and allowed everything), and a subject revoked on a mesh may do nothing there, on every route that checks grants — the gateway's restart and stop, device polls and settings, fault clearing and the editor among them.
- A page in the editor works on the mesh its address names, not the one last opened by any request.
- Mesh's state keeps from growing: progress logs older than a week are removed, and a gateway's or a job runner's log keeps its last megabyte once it passes five; both are started appending, so trimming them under a running process is safe.
- The smoke test checks that a ticket for another mesh is refused.

## [0.18.5] - 2026-11-13

A gateway started on this machine is given its key again. 0.18.1 quoted the key file's path a second time, so the gateway was handed a path with the quotes in it, read no key, and answered no board's hello: a sealed board was not heard. The key and relay-key paths are now plain values, quoted once where they become part of the command.

## [0.18.6] - 2026-11-13

A mesh keeps its gateway key. The key file holds the key and a line break, and the trim it was read with took spaces only, so every read found 65 characters, took the key for missing and wrote a new one: each firmware build and each gateway start had a different key, and no sealed board's hello authenticated. Values read from files are trimmed of line breaks, and a key file that exists is never replaced, since every board's key is derived from it.

## [0.18.7] - 2026-11-13

A module's chip on its board's card shows the board's newest reading: firmware built since modules have stable ids names a reading by the module's tag (`042fda5b.temperature_c`), and the chip read only the number-prefixed name (`m2.temperature_c`) an earlier build sent, so a reflashed board kept showing its last reading from before. The tag is preferred, the number kept for boards not reflashed yet.

## [0.18.8] - 2026-11-13

A device whose address the plan writes without a scheme (`192.0.2.10`) is read again: requests now go through the HTTP client, which needs `http://`, and a Shelly written that way went quiet. The scheme is supplied when it is missing, for polls, reads and commands alike. A device read returns its answer rather than writing it to a file two polls could share.

## [0.18.9] - 2026-11-13

A board's sensor that fails a read is brought up again every five seconds at most, with one fault reported per try. The generated firmware re-initialised it on every poll, twice a second, which kept a struggling DS18B20 from settling (each re-initialisation wrote its EEPROM) and filled the history with faults while the chart held the last good value. Boards pick this up when they are rebuilt.

## [0.18.10] - 2026-11-14

Trimming, URL decoding and form fields come from the toolchain (rho 0.7.0) rather than four copies of each in Mesh's own sources; a `%` not followed by two hex digits in a form is kept as written instead of becoming a zero byte. Needs rho 0.7.0.

## [0.18.11] - 2026-11-14

From a security review: a gateway's or device's token and a client's secret reach curl from an owner-only file rather than its command line, where any user of the machine could read them, and a board's `project.conf` (its Wi-Fi password) is written owner-only.

## [0.18.12] - 2026-11-14

Uses the toolchain's `str_lower` and `json_str_field` instead of its own copies (rho 0.8.0).

## [0.18.13] - 2026-11-14

Every page, panel, confirmation and live view of Mesh is written with i18n keys (480 in each of English and Romanian, the same set), where hard-coded English was translated afterwards by matching whole phrases — six in ten were missed, and every message with a value in it. The live views the worker draws (the tree, its bar and strip, the fleet, the faults) are drawn once per language a page has opened, each in an event stream of its own. The phrase catalog and its pass are gone; internal codes (pill states, job states, deploy reasons) stay English and are translated where shown.

## [0.18.14] - 2026-11-14

Mesh's telemetry store — the Go port it is built with — is Mesh's own, in `plugins/mesh-telemetry`, versioned with Mesh, where it was part of the toolchain's RoboHub plugin (needs rho 0.8.2). The top bar is drawn in the request's language by robohub.ui itself.

## [0.18.15] - 2026-11-14

Mesh presents each gateway a token for that gateway only (`gateway:<project>:*`, which the gateway already checked by its GATEWAY_ID) instead of one valid on every gateway: a token taken from the network opens one gateway. A project that names `gateway_tls_cert` and `gateway_tls_key` (paths on the gateway's machine) deploys its gateway serving HTTPS, with `gateway_url` an https:// address.

## [0.18.16] - 2026-11-14

The live faults stream of each language is appended to instead of read and written whole on every update, and starts over every 500 writes, so it no longer grows without end (one had reached about 10 MB, and was rewritten on each update). A device manifest's template is filled with the toolchain's `str_replace` (rho 0.8.3), which it needs, in place of its own copy.

## [0.18.17] - 2026-11-14

The tree in the agora and the plan no longer draws a board's children over its neighbour's: a branch with children is as wide as they are, and a tree wider than the canvas scrolls from its left edge instead of losing it. The wizard smoke test listens on loopback only.

## [0.19.0] - 2026-11-14

Mesh writes the node manifest each gateway checks (`nodes.txt` beside the gateway key) and sends it with the key, created private before anything is written. A board can be revoked from its inspector and admitted again. Mesh's folders, keys and built firmware are private to its user (0700/0600), including those made before. A gateway with a certificate is recorded and checked over https. A host node fetches its board key with its enrollment credential (`/mesh/api/nodes/key`, rate limited). Needs rho 0.9.0.

## [0.19.1] - 2026-11-14

Building and flashing a board no longer waits for the gateway to stop. The job runner's lock was passed to every program a job started, so a native gateway Mesh had started kept it, and new jobs queued with nothing running them until the gateway stopped. The lock now stays with the runner (and has a new name, which a gateway started by an older Mesh does not hold). A job that waits in the queue with nothing running it is said, on every open page and on the job strip, with what holds the runner.

## [0.20.0] - 2026-11-14

A board's Access tab shows its key epoch and the gateway's word on it, and can rotate its key; firmware is built with the current key, its epoch and the mesh's key scope, so a board moved to another mesh does not keep a key from the old one. A board whose platform has a production mode can be built and flashed for production: off by default, a signing key named per mesh (0600, never in the plan), and a flash that shows every step, the irreversible ones marked, and waits for the board's name to be typed.

## [0.20.1] - 2026-11-15

Every `let` the code assigned to is declared with `var`, as rho 0.19.0 asks (assigning to a `let` is deprecated there and refused in the next minor release). Nothing else changes.

## [0.20.2] - 2026-11-19

Security fixes; mesh-telemetry 0.1.1 with it.

- A request the auth middleware did not authenticate is refused by Mesh's own checks. `mesh_may` let through a request with no audience and no scopes, `mesh_admin_request` treated one with no audience as an administrator and the fleet treated one with no scopes as reading every mesh, so Mesh's access control held only as long as the middleware guarded every path. It did not on the guide (`/mesh/docs`, public on purpose), which listed every mesh by name to anyone. The rules are now one place (`src/mesh/domain/access.rho`), and a scope is matched whole (`mesh:` inside another application's scope no longer counts as a mesh grant).
- Pages that checked no grant at all now do: the faults page and its stream, the agora's stream, Mesh's settings page, `/mesh/api/gateway`, and opening a mesh by `?project=`, which switched the active mesh for anyone signed in. Search finds only in the meshes the person may read; it searched every mesh.
- The telemetry store skips a sequence at or below the gateway feed's cursor, or one a page carries twice, before writing anything. The reading itself was already ignored by its unique key, but a replayed page still overwrote the latest values with old ones, raised its faults again and was sent to the live feed, so whatever answered at a gateway's address could put stale values on every dashboard.
- New tests: an access smoke over the running application (the public routes, every other route class refused without a ticket or with forged identity headers, `/mesh/api` refusing a token for another mesh or without a mesh scope, a gateway's `gateway:<mesh>:*` token refused on Mesh's routes, standalone mode refusing a caller that is not this machine, and the kept copy of the keeper's key: used while the keeper is down, refusing another key, replaced when the keeper rotates); unit tests of the grant rules and the poll backoff; and the telemetry store's first behavioural tests, run by a new `telemetry-store` suite against a real SQLite database (ingest once per gateway and sequence, replays, series keying, roll-up and retention).

Bug fixes from the requirements review (same release).

**Breaking (API, additive):** `GET /mesh/api/devices` answers a device's `state` as the device reports it, or `pending` / `unconfirmed` while a switch has not been confirmed, with new `reported` and `requested` fields; `POST /mesh/api/devices/{id}/switch` answers `200` with `"confirmed":true` only once the device reported the requested state and `202` with `"state":"pending"` (or `"unconfirmed"`) otherwise, and refuses a state other than `on` or `off` with `400`; a source action answers `202` with `"confirmed":false` until the device confirms. A caller that only checks for a 2xx is unaffected; one that read `state` back as the device's must now read `confirmed`.

- **A switch shows the state the device reported, not the one requested.** Mesh recorded the new state as soon as the device took the command, so a relay that stuck, or an MQTT command the broker only accepted, was shown as switched — a page could say a heater was off when it was not (SYS-0025, SYS-0059). A switch is now recorded as pending (`switch-<n>.txt` beside the plan); the state comes from the device's HTTP status read back, or an MQTT status message newer than the request, and a request not confirmed in 30 seconds is shown as "requested, unconfirmed" beside the state the device does report. A device whose integration names no `state` field for its transport is always shown unconfirmed rather than done. Through a gateway, its own confirmation (robohub-gateway 0.7.3) is what counts. A switch API request with `state=banana` was sent as "off"; it is refused.
- **Mesh raises alerts.** It bound `robohub.alert` but never raised one; faults went only to its inbox. A gateway that stops answering (ERROR, once per outage, and INFO when it answers again), a failed build or flash job (ERROR), and a revocation or key rotation that cannot be sent to the gateway (ERROR — until it is, that gateway still admits the revoked board) are now raised on every bound channel, with a 15-minute cooldown per mesh and subject so one outage is one alert.
- **Paths with a space.** `mkdir -p`, `cp`, `mv`, `rm -rf`, `ls`, `find`, `realpath` and a job's stage script were run with their arguments in one string split at spaces, so with a space in Mesh's state or projects path a mesh could not be created or its plan saved — and deleting a mesh ran `rm -rf` on the path's first word (`~/my` for `~/my meshes/...`). Every path is now passed as one argument.
- **A board that names the function `switch` has a switch signal.** The fallback for a board or module without `signals:` knew only input functions, so robohub-drivers' GPIO switch and the KY-019 relay board gave no signal to bind; `switch` is now an output on the field `state`, and a device of the kind `switch` gets the same signal as an actuator. The two fallbacks moved to `src/mesh/domain/signals.rho`, where they are unit-tested.
**Breaking (API):** a source's action (`/mesh/api/projects/{project}/sources/{source}/action`) is a `POST` only; a `GET` answers `405` and switches nothing. The `POST` needs a grant to change the mesh, `state=on` or `off` (none is `400`, no longer "on"), and from a session cookie the form token; a program with a bearer ticket is unaffected. robohub-provider-mesh, the one caller, already posts with a bearer ticket for `mesh:<project>`.

- **A source's action could be switched by any page, and with a read grant.** `GET .../sources/{source}/action` switched the output, asked only for a read grant, and with no `state` switched it on. A page on any site could switch a person's heater or pump with `<img src=".../action">` in a signed-in browser (the same-origin check guards only writes, and a GET is not one), and a token meant only to read could drive outputs. The action now needs a control grant (`mesh:<project>`, `mesh:*` or `*`), refuses a state other than `on`/`off` with `400` instead of assuming on, and refuses a cookie session without the form token (forms 0.3.7 `forms_machine_ok`), as the gateway's board diagnostics became a `POST` with the `command` scope today; `mesh_source_action` no longer turns an unknown state into "on" either (MESH-API-0010).
- New tests: `devices-smoke` (`tests/devices_smoke.py`) — a state folder whose path has a space, a stand-in Shelly plug (confirmed, stuck relay pending then unconfirmed, refused state, plug down; a source's action refused as a GET, with a read-only token, from a cookie session without the form token and without a state, and switched by a control token as the provider sends it and by a session with the form token), a gateway outage and an unsent revocation through the log provider; domain tests of the switch outcome, the alert cooldown and the function signals.

## [0.21.0] - 2026-11-19

A mesh is exported as a project file and imported from one (MESH-0025 to MESH-0028, MESH-XFER-0001 to MESH-XFER-0030, ADR-0004 to ADR-0008); mesh-projectfile 0.1.0, a new Go port in Mesh's tree, with it.

- **Export.** A mesh's settings page downloads it as YAML or JSON (`GET /mesh/api/projects/{project}/export[?format=json]`, or `/mesh/project/{project}/export`, for anyone who may read the mesh). The file is the plan's definition keys under `plan:` — by an allow-list, so no revision, step, status, device state, telemetry, job, token or Mesh-wide setting — beside `format: robohub-mesh-project`, `schema: 1`, `exported.by`/`exported.at` and the mesh's id, name, description, area and ports. It is written by the runtime's YAML writer, sorted and quoted, so two exports of an unchanged mesh differ only in `exported.at`. The schema is published as `schemas/mesh-project.v1.json` (JSON Schema 2020-12), and a test holds it to the rules Mesh checks by (`src/mesh/domain/project_file.rho`).
- **Secrets.** The gateway key, the relay key, the Wi-Fi passwords, a legacy token and the signing key are left out and listed by name under `omitted:`. On request, for `*` or `mesh:*`, the gateway key, the Wi-Fi passwords and the record of what each board runs are sealed in `sealed:` — AES-256-GCM under PBKDF2-HMAC-SHA256 (600 000 iterations) of a passphrase of at least 12 characters, bound to the file's format, schema, mesh id and export time (`POST .../export`, or the settings page with the passphrase twice). The passphrase is taken from the body only and never kept, logged or passed to another program.
- **Import**, as a new mesh with its identity (the fleet page), as a copy with none of it (the fleet page), or over a mesh (its settings page, with the mesh's name typed), from YAML or JSON (`POST /mesh/api/projects/import`). Every import is first a preview that writes nothing — nodes added, changed and removed, areas and ports reassigned, secrets kept and missing, boards out of date, what the node manifest admits, refuses or revokes, conflicts and warnings — and the confirmation names the file by its SHA-256 (`412` for another). A file is checked whole first: every YAML line is a key, a key and a value, a comment or blank, and every problem is named with its key and line (`422`); a newer schema is refused naming the versions; a file over 1 MiB is `413`. A mesh id or node id another mesh has, or its sealed key, refuse an import with identity and offer a copy (`409`); an area or port taken is reassigned and a link to a mesh not here left out, both listed. A new mesh is written in a hidden folder and renamed into place, and Mesh clears what an interrupted import left when it starts. An overwrite is one plan write that keeps the mesh's id, area, ports, gateway key, deployed record, tokens, jobs and history and keeps the plan it replaced as a revision; one that changes nothing writes nothing. A mesh moved with its sealed key, area, uplink port, numbers and ids keeps its boards current; any other import shows them never programmed. An import queues no job and touches no gateway.
- **Held back after an import**: a Wi-Fi board whose password the file left out is not built until a password is entered, and a board whose board, driver or module this Mesh's catalog lacks is not built until the catalog has it (`import.wifi_needed`, `import.catalog_missing` in the plan). The gateway's serial device and TLS paths are imported as written and named in the preview as warnings.
- **mesh-projectfile 0.1.0** (`plugins/mesh-projectfile`, port `projectfile`): JSON read as the flat lines the YAML reader gives and written sorted, sealing and opening, and SHA-256 digests, from the Go standard library alone (`crypto/pbkdf2`). Its changes are recorded here, as mesh-telemetry's are.
- New tests: `tests/project_file_test.rho` (the allow-list, the YAML line check, every class of value, schema versions and migration, every problem, the import's decisions), `project-schema` (the published schema held to the Rho rules), `projectfile-port` (the port's Go tests) and `project-file-smoke` (export and import across several running Meshes); the access smoke covers the new routes.

## [0.21.1] - 2026-11-19

A board was shown as running what is planned after its mesh moved to another
area or its gateway key changed, while it could no longer talk to its gateway: the
image signature Mesh compares to decide what is out of date was computed from the
plan alone, and the area a board reports from and the key its own key is derived
from are not in the plan's rows. The signature now covers every input the build
reads, as the build resolves it: the mesh's area, the gateway key (by its scope,
never the key itself), the board's number, the mesh's Wi-Fi for a board without
its own, the fleet's uplink port for a plan without one and the address a board
dials; the gateway's also names the area, the key and, for a mesh that reports
through another, that mesh's address and key. Another area or key now puts every
board and the gateway out of date, saying "area changed" or "gateway key changed".
A board's key epoch stays out: a rotation reaches the board by REKEY, not by a
build (MESH-BUILD-0001, MESH-BUILD-0002, ADR-0001 amendment, proposed).

A board's Wi-Fi password was kept in clear in its signature, and so in
`deployed.txt`, in each job's record and in the apply page's diff; it is now a
keyed digest, derived from the gateway key, that still changes with the password
(MESH-BUILD-0013, proposed). A record written by an earlier Mesh is replaced, the
first time it is read, by one that keeps nothing of it.

Upgrading: a record of what runs written by 0.21.0 or earlier does not say which
area or key a board was built with, and that cannot be reconstructed, so it is not
migrated. Every such board and the gateway show out of date once, "recorded by an
earlier Mesh without the area and gateway key it was built with", until the board
is flashed or the gateway restarted (MESH-BUILD-0012, proposed). A board that was
programmed and has no record at all is still taken to run what is planned, as
before. A project file's record of what runs still travels only with the gateway
key, area, uplink port and every node's identity (MESH-XFER-0017), and a record that
travels with them compares equal on the other machine.

## [0.22.0] - 2026-11-19

A device that reports over several MQTT topics with ON/OFF words — the Sonoff
S60TPF reflashed with Tasmota (robohub-devices ADR-0002) — is read and switched
from its manifest, with no code of its own (ADR-0009, MESH-0029..0032, approved):

- `telemetry_mqtt.status_topics` lists several status topics, merged into one
  reading: a message changes only the fields whose path it carries, each field
  takes the value of the newest message carrying it, a field none carries keeps
  its last value. `status_topic` keeps its meaning; the Shelly plug reads as it
  did.
- A field path of `$` is the whole payload, for a bare word such as Tasmota's
  `stat/POWER`; a path a message does not carry, or a JSON path into a payload
  that is not JSON, changes nothing and is no error.
- A new field type, `onoff`: ON/OFF, true/false, 1/0 in any case, stored as 1 or
  0; anything else is a protocol error that keeps the field's last value and is
  logged once. `bool` is unchanged. HTTP devices are read through the same code,
  so both types mean the same on both transports.
- `telemetry_mqtt.online_up` names the online word of `online_topic` (`true` when
  not given): a device is shown online or unreachable on its Data tab and as
  `reachability` in `GET /mesh/api/devices`.
- A switch is confirmed from the newest status message carrying the state, on
  whichever topic it came (MESH-0022 amended): Tasmota's `stat/RESULT` or
  `stat/POWER`, not a later meter reading.
- An MQTT device's Data tab has the Poll and switch buttons an HTTP device has,
  rather than saying it is carried by another node.
- Mesh's broker grants add publishing `+/cmnd/POWER` and reading `+/stat/#` and
  `+/tele/#`; a device's prefix must be one topic level.
- A quoted field-map line may carry a comment after it.

The devices smoke plans a stand-in Tasmota plug on a broker of its own that holds
Mesh to the grants its manifest asks for.

## [0.23.0] - 2026-11-20

Zigbee: a coordinator is a device node Mesh runs Zigbee2MQTT for, and the Zigbee
devices that join it are MQTT devices under it (ADR-0010, MESH-0033..0035,
MESH-ZB-0001..0017).

- **Two additions to the MQTT device contract**, for any device:
  `telemetry_mqtt.online_path` reads the online word from inside a JSON payload
  (Zigbee2MQTT's `{"state":"online"}`), a payload that is not JSON or lacks the path
  being unreachable; and an action's payload may carry `{ONOFF}`, filled with `ON`
  or `OFF`, beside `{on}`. A single-quoted payload in a manifest is read without
  its quotes. A device whose status topic is its prefix itself is read.
- **Boards declare `capabilities.usb_host`**, read into the board rows; the native
  gateway counts as a host and a USB host.
- **Coordinators.** A device of kind `coordinator` is added from its own form,
  offered only under the gateway or a board whose capabilities meet its manifest's
  `needs:`, at most one per board, refused elsewhere saying which capability is
  missing. Its name is its address and a topic level; its serial port is picked
  from `/dev/serial/by-id/` (the manifest's `usb.by_id` matches marked) or typed
  and checked as a device path; its channel is 15 unless changed before the first
  start. The coordinator is no device in the list or the API.
- **Running Zigbee2MQTT** from the coordinator's Zigbee tab: a broker user of its
  own from the keeper (`POST /api/v1/bus/users`, confined to
  `zigbee/<mesh>/<coordinator>`), its password owner-only and only in
  `secret.yaml`; the configuration written from the robohub-zigbee2mqtt plugin's
  template before the first start and afterwards only Mesh's keys rewritten, so
  the network key and PAN ids Zigbee2MQTT made are kept; on this machine `node
  index.js` from `~/.rho/toolchains/zigbee2mqtt/<version>` with its data folder
  and `Z2M_ONBOARD_NO_SERVER=1`, stopped by its pid only while it is that program,
  independent of the gateway and started again when Mesh starts; on another host
  board that deploys with docker, the pinned image over the board's login, refused
  while the broker listens on loopback only. A missing Node.js or Zigbee2MQTT is
  said, with an install job for Zigbee2MQTT. The bridge's state, version, firmware
  and channel are shown. Removing a coordinator stops it, removes its broker user
  and keeps its data folder; removing a mesh removes its coordinators' users.
- **Pairing**: joining opened for at most 254 s with the time left and *Stop
  joining*; the devices that joined and are not in the plan, matched to a manifest
  by `zigbee.model`/`zigbee.vendor`; *Adopt* adds a `zigbee` device node and renames
  the device to its address, the node marked not yet renamed until the bridge
  answers its transaction with ok; removing the node removes the device from the
  network, saying when it could not. Every action needs the plan's grant and
  Mesh's form token.
- **Reading and switching**: a Zigbee device is read, polled, switched, confirmed
  and listed exactly as an MQTT device with the prefix
  `zigbee/<mesh>/<coordinator>/<address>`. Mesh's bus grants add `zigbee/#`,
  `zigbee/+/+/+/set`, `zigbee/+/+/bridge/request/#` and `issue: [zigbee]`; a mesh
  id, coordinator or device address that is not a topic level is refused.
- **Project file schema 2** (`schemas/mesh-project.v2.json`): the node fields
  `channel` and `ieee`; each coordinator's network key, PAN ids, channel and
  coordinator backup sealed, or named under `omitted:`; an import writes them for
  the coordinator's first start. Schema 1 files are read as before.

The Zigbee smoke runs a broker, a keeper and a Zigbee2MQTT of its own and opens no
serial port.


## [0.24.0] - 2026-11-20

Supervision: when the keeper runs Mesh, a mesh's native gateway and each native
coordinator's Zigbee2MQTT are keeper helpers — units of their own that a restart
or redeploy of Mesh leaves running and systemd restarts when they fail (robohub
ADR-0018, MESH-0037, keeper KEEP-APPS-0020).

- **The manifest declares `helpers: [gateway-*, z2m-*]`** and
  `deploy.limits: {memory: 4G, tasks: 4096}`: Mesh builds firmware and the
  gateway inside its own unit, which needs more than the keeper's defaults.
- **Helpers when the keeper offers them.** With `RHO_CLIENT_SECRET_FILE` and a
  keeper on this machine that answers `GET /api/v1/helpers`, Mesh asks the keeper
  (`Authorization: App mesh:<client secret>`, read by curl from an owner-only
  header file, never a word of a command line; on loopback, without proxy
  headers) to run the gateway as `gateway-<mesh>` and a coordinator's Zigbee2MQTT
  as `z2m-<mesh>-<coordinator>`. Starting is `POST /api/v1/helpers` with the
  program, its arguments, folder and environment — no secret in any of them: the
  broker password stays in `secret.yaml`, and the gateway's key files, whose
  variable names the keeper refuses, are passed as paths through `env` on the
  helper's command line. A coordinator's state is the helper's state, stopping it
  is `POST …/stop`, removing it or its mesh is `DELETE`; the gateway's start is
  part of its job, after the build, and its stop and its mesh's deletion go
  through the keeper too. The Zigbee tab says the coordinator runs as a helper,
  names its unit and shows its log — the helper's log the keeper answered, or
  `mesh-z2m.log` for a process.
- **A restart of Mesh leaves helpers alone**: the once-a-minute start of wanted
  coordinators asks the keeper, leaves a helper that runs, and starts one that
  was stopped behind Mesh's back. A coordinator that ran as a helper is not
  started as a process while the keeper that runs Mesh is not answering.
- **Run on its own** (by hand, CI, a keeper without the API), Mesh runs both as
  processes with their pid and log beside the mesh, exactly as before.
- **Zigbee after a standby**: a serial port that exists but does not answer
  being opened within 3 s (a usbipd attachment lost in a standby) is said before
  Zigbee2MQTT is started on it; a Zigbee2MQTT running for over a minute without
  its bridge online is said on the tab; a wanted coordinator is started again
  every minute while it is not running, and with the broker user it was issued
  before when the keeper cannot be reached at all.

The new helpers smoke runs Mesh under a stand-in keeper offering the helpers API
and checks the definitions, the credential, the state, stop, removal and that a
restart of Mesh stops nothing.

`/mesh/api` accepts the read-only `mesh:<project>:read` and `mesh:read` for the requests that only read (GET, HEAD), so an application that only shows readings — robohub-feed — reads with a grant that changes nothing; writing still needs `mesh:<project>`, `mesh:*` or `*` (MESH-API-0002).

## [0.25.0] - 2026-11-20

A board's health (MESH-0038, robohub ADR-0021). After a poll of a mesh's gateway succeeds, Mesh reads its node listing — at most every 30 s, under the outbox poll's grant — for each board's last `HEALTH` report (robohub-gateway 0.8.0), keeps the newest beside the plan (`health-<number>.txt`), and raises an alert for a new one whose reason is the watchdog or a fault, naming the board, the reason, the fault, its task and its program counter; a board resetting in a loop is one alert per cooldown. The board's Data tab shows the last report: the reset reason, boots since power-on, the firmware, the fault before, and when it came. The rules are `src/mesh/domain/health.rho`, tested in `tests/domain_test.rho`; `tests/devices_smoke.py` runs it against a stand-in gateway.
