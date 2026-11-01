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

A node is now one computer with any number of sensor modules attached to it,
rather than one node per driver. The module list is read from
`robohub-drivers/boards` at render time, so a board added there appears without
a rebuild. Each module declares its bus and, where it matters, its I2C address,
address range or SPI chip select; the node page shows the resulting bus plan.
1-Wire and I2C lines are shared, an SPI device gets its own chip select, and an
address or exclusive instance that is already in use is reported and left out of
the image so the parent node always builds. The attached modules are linked into
the node's image; only the parent node is built and programmed.
