#!/usr/bin/env python3
"""On-target: the settlement node — a Pico 2 W with an SHT4x, built from
robohub-node's template by Mesh, reporting through the gateway (robohub
ADR-0026; DRV-ENV-0007, DRV-BRD-0005, PICO-0015).

Run with a Pico 2 W on USB and an SHT4x breakout wired to it (VCC 3V3, GND,
SDA GP8, SCL GP9); under WSL keep the board attached across its resets, and no
other gateway may hold its serial port:

    python3 tests/settlement_on_target.py

A Mesh of its own plans one Pico 2 W with the SHT4x module fitted, builds and
flashes it, starts the gateway on its USB link, and waits for the board's
temperature and humidity: plausible values (-40…85 °C, 0…100 %), not faults.
What leaves the installation from there is robohub-feed's `http` publisher
(FEED-0008, tests/http_publisher_smoke.py in robohub-feed) and what receives it
is nest's `nest-feed` module (nest tests/test_feed.py).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import update_chain_on_target as chain

NODE_ID = "5e771e0e5e771e01"
MODULE_ID = "5e771e0e5e771e02"


def readings(rest: int) -> dict:
    """The board's latest temperature and humidity from the gateway, or {}."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{rest}/api/v1/nodes", timeout=3) as answer:
            listing = json.loads(answer.read().decode())
    except (urllib.error.URLError, ConnectionError, OSError, ValueError):
        return {}
    nodes = listing if isinstance(listing, list) else listing.get("nodes", [])
    for node in nodes:
        if isinstance(node, dict) and "temperature_c" in node and "humidity" in node:
            return node
    return {}


def main() -> int:
    serial = chain.pico_serial()
    if not serial:
        print("skipped: no Pico 2 W on USB (/dev/serial/by-id)")
        return 77
    built = subprocess.run([chain.RHO_CMD, "build", "."], cwd=chain.MESH, text=True, capture_output=True)
    if built.returncode != 0:
        raise SystemExit("mesh build failed:\n" + built.stdout[-3000:] + built.stderr[-3000:])

    port = chain.free_port()
    base = f"http://127.0.0.1:{port}"
    root = Path(tempfile.mkdtemp(prefix="mesh-settlement-"))
    projects = root / "state" / "projects"
    project = projects / "Chain"
    project.mkdir(parents=True)
    rest = chain.free_port()
    (project / "project.txt").write_text(
        "project.name=Chain\narea_id=9\ngateway=native\n"
        f"rest_port={rest}\ngateway_serial={serial}\n"
        "node.count=2\n"
        f"node.1.name=Settlement\nnode.1.parent=gateway\nnode.1.role=node\nnode.1.id={NODE_ID}\n"
        "node.1.kind=compute\nnode.1.board=pico2w\nnode.1.link=usb\n"
        f"node.2.name=Air\nnode.2.parent=node.1\nnode.2.role=module\nnode.2.id={MODULE_ID}\n"
        "node.2.kind=module\nnode.2.module=sht4x-module\n")
    home = root / "home"
    chain.TOKEN = chain.mint_token(str(home))
    env = dict(os.environ)
    env.pop("RHO_CLIENT_ID", None)
    env.update(ROBOHUB_WORKSPACE=str(chain.PROJECTS), RHO_MESH_STATE_DIR=str(root / "state"),
               ROBOHUB_MESH_PROJECTS=str(projects), RHO_HTTP_ADDR=f"127.0.0.1:{port}",
               ROBOHUB_ROOT=str(chain.PROJECTS / "robohub"), RHO_AUTH_STANDALONE="1",
               PYTHONUSERBASE=env.get("PYTHONUSERBASE", os.path.expanduser("~/.local")),
               IDF_TOOLS_PATH=env.get("IDF_TOOLS_PATH", os.path.expanduser("~/.espressif")))
    env.setdefault("RHO_HOME", os.path.expanduser("~/.rho"))
    env["HOME"] = str(home)
    log = open(root / "mesh.log", "w")
    process = subprocess.Popen([str(chain.MESH / "build" / "mesh")], cwd=chain.MESH, env=env, stdout=log,
                               stderr=subprocess.STDOUT, text=True)
    try:
        chain.wait_for(base)
        status, _ = chain.call(base, "POST", "/mesh/p/Chain/apply", {"only": f"node:{NODE_ID}"})
        chain.check(status == 200, f"build and flash is queued ({status})")
        built = chain.follow(base, project, "build", 1200)
        sensors = project / "boards" / "node_1" / "src" / "sensors.c"
        chain.check(built[2] == "done" and sensors.exists() and "sht4x" in sensors.read_text(),
                    f"Mesh builds the template with the SHT4x fitted ({built})")
        flashed = chain.follow(base, project, "flash", 300, go=True)
        chain.check(flashed[2] == "done", f"and flashes it ({flashed})")
        chain.call(base, "POST", "/mesh/p/Chain/apply", {"only": "gateway"})
        gateway = chain.follow(base, project, "gateway", 600)
        chain.check(gateway[2] == "done", f"the gateway is started ({gateway})")

        node = {}
        end = time.time() + 120
        while time.time() < end and not node:
            node = readings(rest)
            time.sleep(2)
        temperature = node.get("temperature_c")
        humidity = node.get("humidity")
        chain.check(isinstance(temperature, (int, float)) and -40.0 <= temperature <= 85.0,
                    f"the board reports a plausible temperature through the gateway ({temperature})")
        chain.check(isinstance(humidity, (int, float)) and 0.0 <= humidity <= 100.0,
                    f"and a plausible relative humidity ({humidity})")
    finally:
        chain.call(base, "POST", "/mesh/gateway/stop", {"project": "Chain"})
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()
        if chain.failures:
            print(f"logs kept in {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)

    if chain.failures:
        print("FAILED:", *chain.failures, sep="\n  ")
        return 1
    print("OK: the settlement node read its SHT4x and reported through the gateway")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
