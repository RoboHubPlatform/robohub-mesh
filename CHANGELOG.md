# Changelog

All notable changes to this module are recorded here, one section per release.

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

## [0.3.5] - 2026-11-03

The gateway belongs to a project, not to the Mesh application. A project's
settings carry its gateway URL; `/mesh/api/projects` lists the projects,
`/mesh/api/projects/{project}/gateway` serves that project's gateway while it
answers, and `/mesh/api/devices` serves its device nodes.

## [0.3.4] - 2026-11-03

Mesh settings name the gateway URL; `/mesh/api/gateway` serves it while the
gateway answers, and `/mesh/api/devices` serves the device nodes. Another
application can connect to Mesh and discover where the devices are.

## [0.3.3] - 2026-11-03

A device can be **free standing**: its parent is the mesh/network rather than a
gateway or relay. It appears at the root of the tree, is not carried by a node,
and is reached directly over its own transport.

## [0.3.2] - 2026-11-02

Device picker reads the `devices:` list in robohub-devices' module.yml, so
every device the collection carries is offered and a new one appears on the next
render. The transport list includes Modbus, GPIO and UART alongside HTTP and
MQTT.

## [0.3.1] - 2026-11-02

Device nodes are reachable from the tree and from a native gateway too:
"+ Add device" is beside "+ Add node", and a compute node offers "+ device"
beside "+ module". A native host can carry network devices even though it has
no pins for modules.

## [0.3.0] - 2026-11-02

Device nodes. A device from `robohub-devices` is a node of its own in the Mesh
tree, attached to a gateway or a relay; it is not built or programmed. The
editor discovers device integrations from the checkout; the Shelly Plug M Gen3
is the first, over HTTP RPC or MQTT. An HTTP device can be polled and switched
from its node page; an MQTT device is carried by its relay or gateway.
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
