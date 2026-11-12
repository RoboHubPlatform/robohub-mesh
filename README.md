# RoboHub Mesh

Plans, builds and runs a RoboHub mesh: a gateway, the boards under it, the
modules on those boards and the devices beside them. Mesh draws the network as a
tree, writes each board's firmware from the plan, programs it, starts the
gateway, and keeps the readings that come back.

It is a Rho application. It builds with the rho toolchain beside a robohub
checkout, which holds the registry of boards, drivers and devices and the
shared modules Mesh is made of:

```sh
# ${ROBOHUB_WORKSPACE} holds robohub, robohub-drivers, robohub-devices and this repository.
rho build .
./build/mesh                 # or start it from the keeper's Applications page
rho test .                   # the unit suite and the HTTP smoke test
```

Its guide is [docs/index.md](docs/index.md); it is also served by the running
application under Help.
