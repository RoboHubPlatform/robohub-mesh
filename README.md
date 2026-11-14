# RoboHub Mesh

Plans, builds and runs a RoboHub mesh: a gateway, the boards under it, the modules
on those boards and the devices beside them. Mesh draws the network as a tree,
writes each board's firmware from the plan, programs it, starts the gateway, and
keeps the readings that come back in the telemetry store the other applications
read.

It is a keeper-hosted application: the keeper serves it at `/app/mesh/mesh`, and it
listens on its own loopback port, `127.0.0.1:8478`. It builds with the Rho toolchain
beside a robohub checkout, which holds the registry of boards, drivers and devices
and the shared modules Mesh is made of:

```sh
# ${ROBOHUB_WORKSPACE} holds robohub, robohub-drivers, robohub-devices and this repository.
rho build .
./build/mesh                 # or install and start it from the keeper
rho test .                   # the unit suite and the HTTP smoke test
```

`plugins/mesh-telemetry` is Mesh's telemetry store, the Go port it is built with.

Its guide is [docs/index.md](docs/index.md); the running application serves it
under Documentation.
