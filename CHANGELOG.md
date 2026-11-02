# Changelog

All notable changes to this module are recorded here, one section per release.

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
