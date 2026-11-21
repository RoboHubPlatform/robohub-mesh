#!/usr/bin/env python3
"""On-target: the settlement node — a Pico 2 W with its sensor module, built
from robohub-node's template by Mesh, reporting through the gateway (robohub
ADR-0026, as amended; PICO-0015, and DRV-ENV-0007/DRV-BRD-0005 with an SHT4x).

Run with a Pico 2 W on USB and the breakout wired to it (VCC 3V3, GND, SDA GP8,
SCL GP9); under WSL keep the board attached across its resets, and no other
gateway may hold its serial port:

    python3 tests/settlement_on_target.py                    # a GY-68 (BMP180)
    SETTLEMENT_MODULE=sht4x-module python3 tests/settlement_on_target.py

A Mesh of its own plans one Pico 2 W with the module fitted, builds and flashes
it, starts the gateway on its USB link, and waits for the board's readings —
plausible values, not faults: temperature (-40…85 °C) and pressure
(30 000…110 000 Pa) from a BMP180, temperature and humidity (0…100 %) from an
SHT4x.
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
MODULE = os.environ.get("SETTLEMENT_MODULE", "bmp180-module")
# The second reading each module gives, and the range a real one falls in.
SECOND = {"bmp180-module": ("pressure_pa", 30000.0, 110000.0, "pressure"),
          "sht4x-module": ("humidity", 0.0, 100.0, "relative humidity")}
MODULE_ID = "5e771e0e5e771e02"


LAST_LISTING = [""]


def readings(rest: int) -> dict:
    """The board's latest readings from the gateway, once both have come, or {}."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{rest}/api/v1/nodes", timeout=3) as answer:
            text = answer.read().decode()
            LAST_LISTING[0] = text
            listing = json.loads(text)
    except (urllib.error.URLError, ConnectionError, OSError, ValueError):
        return {}
    nodes = listing if isinstance(listing, list) else listing.get("nodes", [])
    for node in nodes:
        if not isinstance(node, dict) or not node.get("sensors"):
            continue
        # The gateway lists every field a node reported under "sensors", keyed as
        # the firmware named it (perhaps with a module's prefix): found by name.
        sensors = node["sensors"]

        def value(field):
            for key, got in sensors.items():
                if (key == field or key.endswith("." + field) or key.endswith("_" + field)) and \
                        isinstance(got, (int, float)):
                    return got
            return None

        found = {"temperature_c": value("temperature_c"), SECOND[MODULE][0]: value(SECOND[MODULE][0])}
        if all(v is not None for v in found.values()):
            return found
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
        f"node.2.kind=module\nnode.2.module={MODULE}\n")
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
        part = MODULE.split("-")[0]
        chain.check(built[2] == "done" and sensors.exists() and part in sensors.read_text(),
                    f"Mesh builds the template with the {part} fitted ({built})")
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
        field, low, high, what = SECOND[MODULE]
        second = node.get(field)
        chain.check(isinstance(temperature, (int, float)) and -40.0 <= temperature <= 85.0,
                    f"the board reports a plausible temperature through the gateway ({temperature})")
        chain.check(isinstance(second, (int, float)) and low <= second <= high,
                    f"and a plausible {what} ({second})")
        if not node:
            print("the gateway's last listing:", LAST_LISTING[0][:1500])
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
    print(f"OK: the settlement node read its {MODULE} and reported through the gateway")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
