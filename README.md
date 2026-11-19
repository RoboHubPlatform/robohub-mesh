# RoboHub Mesh

Plans, builds and runs a RoboHub mesh: a gateway, the boards under it, the modules
on those boards and the devices beside them. Mesh draws the network as a tree,
writes each board's firmware from the plan, programs it, starts the gateway, and
keeps the readings that come back in the telemetry store the other applications
read.

It is a keeper-hosted application: the keeper installs and runs it, and it is
reached on its own loopback port, `127.0.0.1:8478` (robohub:ADR-0016); the keeper's
old address `/app/mesh/…` redirects there. It builds with the Rho toolchain
beside a robohub checkout, which holds the registry of boards, drivers and devices
and the shared modules Mesh is made of:

```sh
# ${ROBOHUB_WORKSPACE} holds robohub, robohub-drivers, robohub-devices and this repository.
rho build .
./build/mesh                 # or install and start it from the keeper
rho test .                   # the unit suite and the HTTP smoke test
```

`plugins/mesh-telemetry` is Mesh's telemetry store, the Go port it is built with, and
`plugins/mesh-projectfile` the port its project files are read, written and sealed
with. A mesh is exported and imported as a project file whose schema is published in
`schemas/mesh-project.v2.json` (schema 1 is `v1`).

Its guide is [docs/doc/index.md](docs/doc/index.md); the running application serves it
under Documentation.

## License

MIT. See [LICENSE](LICENSE).
