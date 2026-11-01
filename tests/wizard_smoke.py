#!/usr/bin/env python3
"""Smoke test for the RoboHub Mesh application.

Builds Mesh, starts it against a temporary state directory, and walks the
application the way a browser does: the table of contents, the plan, a saved
network, and the event stream that reports a long action.

The test uses the repository's own registry and wizard documents; it does not
need a board or a gateway. A missing registry is a failed test, because the
planner's promise is that its lists come from there.

Run from anywhere:

    python3 applications/mesh/tests/wizard_smoke.py
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve()
MESH = HERE.parents[1]
ROBOHUB = MESH.parents[1]
PROJECTS = ROBOHUB.parent
RHO = PROJECTS / "rho" / "bin" / "rho"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(base: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/healthz", timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.2)
    raise SystemExit(f"mesh did not start at {base}")


def get(base: str, path: str) -> str:
    with urllib.request.urlopen(base + path, timeout=10) as response:
        return response.read().decode("utf-8", "replace")


def post(base: str, path: str, fields: dict) -> str:
    body = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(base + path, data=body, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", "replace")


def assert_contains(haystack: str, needles: list, label: str) -> None:
    for needle in needles:
        if needle not in haystack:
            raise AssertionError(f"{label} does not contain {needle!r}")


def build_mesh() -> Path:
    binary = MESH / "build" / "mesh"
    rho = str(RHO) if RHO.exists() else "rho"
    result = subprocess.run(
        [rho, "build", "."],
        cwd=MESH,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise SystemExit("mesh build failed:\n" + result.stdout + result.stderr)
    if not binary.exists():
        raise SystemExit(f"mesh build wrote no binary at {binary}")
    return binary


def validate_wizard_schema() -> None:
    """Ask the Rho schema validator about the documents the mesh reads."""
    go = PROJECTS / "rho"
    if not (go / "src" / "schema").is_dir():
        return
    env = dict(os.environ)
    env["ROBOHUB_MESH_WIZARD_DIR"] = str(MESH / "wizard")
    result = subprocess.run(
        ["go", "test", "./src/schema", "-run", "TestKeeperWizardDocuments"],
        cwd=go,
        env=env,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise SystemExit("wizard schema validation failed:\n" + result.stdout + result.stderr)


def main() -> int:
    validate_wizard_schema()
    binary = build_mesh()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="mesh-app-") as state:
        env = dict(os.environ)
        env["ROBOHUB_WORKSPACE"] = str(PROJECTS)
        env["RHO_MESH_STATE_DIR"] = state
        env["ROBOHUB_MESH_PROJECTS"] = os.path.join(state, "projects")
        env["RHO_HTTP_ADDR"] = f":{port}"
        process = subprocess.Popen(
            [str(binary)],
            cwd=MESH,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            wait_for(base)

            mesh = get(base, "/mesh")
            assert_contains(
                mesh,
                ["RoboHub Mesh", "Projects", "Create project", "Documentation"],
                "mesh overview",
            )
            if 'nav-title">Operations' in mesh:
                raise AssertionError("Operations must be hidden when no project is active")

            docs = get(base, "/mesh/docs")
            assert_contains(docs, ["RoboHub Mesh", "Projects", "left menu"], "documentation")

            # A new project is active from the start when none was.
            post(base, "/mesh/project/new", {"name": "default"})
            active = get(base, "/mesh")
            assert_contains(
                active,
                ["nav-title\">Operations", "for <strong>default", "nav-badge\">Active",
                 "Project settings"],
                "active project menu",
            )

            node_form = get(base, "/mesh/node/new")
            assert_contains(
                node_form,
                [
                    'name="name"',
                    'name="role"',
                    'name="board"',
                    'name="sensors"',
                    'name="parent"',
                    'name="link"',
                    'value="pico2w"',
                    'value="ds18b20-temperature-module"',
                    'value="ds18b20-waterproof-probe-module"',
                    'value="ky-001-temperature-sensor-module"',
                    'value="bmp180-module"',
                    "form-actions",
                ],
                "node editor",
            )

            post(
                base,
                "/mesh/gateway",
                {
                    "name": "Gateway",
                    "description": "The UNO Q gateway.",
                    "gateway": "arduino-uno-q",
                },
            )
            post(
                base,
                "/mesh/node/new",
                {
                    "action": "save",
                    "name": "Relay one",
                    "description": "First node",
                    "role": "relay",
                    "board": "pico2w",
                    "sensors": "ds18b20-temperature-module dht11-module",
                    "parent": "gateway",
                    "link": "uart",
                },
            )
            post(
                base,
                "/mesh/node/new",
                {
                    "action": "save",
                    "name": "Leaf two",
                    "description": "Second node",
                    "role": "node",
                    "board": "pico2w",
                    "sensors": "ky-013-analog-temperature-sensor-module ky-018-photoresistor-module",
                    "parent": "node.1",
                    "link": "wifi",
                },
            )
            post(base, "/mesh/accept", {"action": "accept"})

            accepted = get(base, "/mesh/plan")
            assert_contains(
                accepted,
                ["Edit topology", "Next: network", "Relay one", "Leaf two",
                 "mesh-children", "link-badge", "UART", "WiFi"],
                "accepted mesh",
            )

            relay_page = get(base, "/mesh/node/1")
            assert_contains(
                relay_page,
                [
                    "DS18B20_MODULE (ds18b20-temperature-module)",
                    "DHT11_MODULE (dht11-module)",
                    "1-Wire onewire0",
                    "gpio dht0",
                    "Bus plan",
                ],
                "relay modules",
            )
            leaf_page = get(base, "/mesh/node/2")
            assert_contains(leaf_page, ["conflict", "adc adc0 channel 0"], "bus conflict")
            # The conflicted module is reported and left out of the image, so
            # the node itself still has a build path.

            dashboard = get(base, "/mesh")
            assert_contains(
                dashboard,
                ["RoboHub Mesh", "Projects", "Create project", "Gateway",
                 "Restart", "Stop", "Delete", "Project settings", "Last update"],
                "mesh dashboard",
            )

            network = get(base, "/mesh/step/network")
            assert_contains(
                network,
                ["Network", "Click a connection badge", "needs setup", "Relay one", "Leaf two"],
                "network page",
            )
            if 'href="/mesh/node/' in network:
                raise AssertionError("the topology tree must be read-only on the network step")

            link_one = get(base, "/mesh/network/link/1")
            assert_contains(
                link_one,
                ["Use transport", "Save connection", "Parent serial port", "Node serial port",
                 "UART", "USB", '<option value="console">', "WiFi"],
                "connection 1 page",
            )
            post(base, "/mesh/network/link/1", {"action": "choose_transport", "transport": "uart"})
            post(
                base,
                "/mesh/network/link/1",
                {
                    "action": "save_settings",
                    "node.1.parent_endpoint": "/dev/ttyACM0",
                    "node.1.child_endpoint": "/dev/ttyACM1",
                    "node.1.baud": "115200",
                },
            )

            link_two = get(base, "/mesh/network/link/2")
            assert_contains(link_two, ["Use transport", "Save connection"], "connection 2 page")
            if '<option value="console">' in link_two:
                raise AssertionError("Console must not be offered when the parent is a relay")
            post(base, "/mesh/network/link/2", {"action": "choose_transport", "transport": "wifi"})
            post(
                base,
                "/mesh/network/link/2",
                {
                    "action": "save_settings",
                    "node.2.ssid": "TestNet",
                    "node.2.password": "secret",
                    "node.2.parent_host": "192.0.2.10",
                    "node.2.child_host": "192.0.2.20",
                    "node.2.port": "5001",
                },
            )

            node_network = get(base, "/mesh/network/node/1")
            assert_contains(
                node_network,
                ["Incoming connection", "Connections to children", "Relay one"],
                "node network page",
            )
            gateway_network = get(base, "/mesh/network/gateway")
            assert_contains(
                gateway_network,
                ["Gateway host", "Save gateway network", "Connections to nodes"],
                "gateway network page",
            )
            post(
                base,
                "/mesh/network/gateway",
                {"action": "save_gateway", "gateway_host": "192.0.2.10", "rest_port": "8080"},
            )
            post(base, "/mesh/network/accept", {"action": "accept"})

            accepted_network = get(base, "/mesh/step/network")
            assert_contains(
                accepted_network,
                ["Network accepted", "Next: build"],
                "accepted network",
            )

            build_page = get(base, "/mesh/step/build")
            assert_contains(
                build_page,
                ["Build", "Select the gateway card", "not built", "Relay one", "Leaf two"],
                "build page",
            )
            node_build = get(base, "/mesh/build/node/1")
            assert_contains(
                node_build,
                ["Build node", "Program node", "Program uses the last successful build", "Relay one", "Console", "Clear console"],
                "node build page",
            )
            prompt = post(base, "/mesh/build/node/1", {"action": "program_prompt"})
            assert_contains(
                prompt,
                ["BOOTSEL", "I have put the board in boot mode", "Program"],
                "program prompt",
            )

            (Path(state) / "progress-build.log").write_text("old output\n__DONE__:0\n", encoding="utf-8")
            post(base, "/mesh/console/clear/build", {})
            if (Path(state) / "progress-build.log").read_text() != "":
                raise AssertionError("clear console must empty the progress log")

            gateway_build = get(base, "/mesh/build/gateway")
            assert_contains(
                gateway_build,
                ["Build gateway", "Program gateway", "Change gateway", "Gateway"],
                "gateway build page",
            )

            wizard_state = Path(state) / "projects" / "default" / "project.txt"
            saved = wizard_state.read_text()
            assert_contains(
                saved,
                [
                    "gateway=arduino-uno-q",
                    "gateway_name=Gateway",
                    "node.count=2",
                    "node.1.name=Relay one",
                    "node.1.board=pico2w",
                    "node.1.sensors=ds18b20-temperature-module dht11-module",
                    "node.2.sensors=ky-013-analog-temperature-sensor-module ky-018-photoresistor-module",
                    "node.2.parent=node.1",
                    "step_topology=done",
                    "step_network=done",
                    "node.1.link=uart",
                    "node.1.parent_endpoint=/dev/ttyACM0",
                    "node.1.child_endpoint=/dev/ttyACM1",
                    "node.2.link=wifi",
                    "node.2.ssid=TestNet",
                    "node.2.parent_host=192.0.2.10",
                    "gateway_host=192.0.2.10",
                ],
                "mesh state",
            )

            # A project can be made inactive; the Operations menu disappears
            # and a step page returns to the dashboard.
            settings_default = get(base, "/mesh/project/default/settings")
            assert_contains(settings_default, ["Make inactive", "active one"], "active settings")
            post(
                base,
                "/mesh/project/active",
                {"project": "default", "active": "0", "return": "settings"},
            )
            inactive = get(base, "/mesh")
            if 'nav-title">Operations' in inactive:
                raise AssertionError("Operations must be hidden when no project is active")
            assert_contains(get(base, "/mesh/plan"), ["Create project"], "step without a project")
            post(
                base,
                "/mesh/project/active",
                {"project": "default", "active": "1", "return": "dashboard"},
            )
            assert_contains(get(base, "/mesh"), ['for <strong>default'], "reactivated project")

            # A project is a folder of its own; creating one starts empty, and
            # an existing project can be opened again.
            post(base, "/mesh/project/new", {"name": "Second project"})
            current_project = (Path(state) / "current-project.txt").read_text().strip()
            if current_project == "default":
                raise AssertionError("a new project must get its own folder")
            second_state = Path(state) / "projects" / "Second-project" / "project.txt"
            if not second_state.exists():
                raise AssertionError("the new project folder must exist")

            settings = get(base, "/mesh/project/Second-project/settings")
            assert_contains(
                settings,
                ["Project settings", "Project name", "Short description", "Save project"],
                "project settings",
            )
            post(
                base,
                "/mesh/project/Second-project/settings",
                {"name": "Renamed project", "description": "A second test mesh"},
            )
            saved_project = second_state.read_text()
            assert_contains(
                saved_project,
                ["project.name=Renamed project", "project.description=A second test mesh"],
                "renamed project",
            )

            post(base, "/mesh/project/delete", {"project": "Second-project"})
            if second_state.parent.exists():
                raise AssertionError("deleting a project must remove its folder")
            get(base, "/mesh/project/default")
            current_project = (Path(state) / "current-project.txt").read_text().strip()
            if current_project != "default":
                raise AssertionError("opening a project must make it current")

            # A long action writes its progress to a file and the page follows
            # that file. Seed one so the stream's framing and its closing event
            # are checked without a board attached.
            (Path(state) / "progress-verify.log").write_text(
                "one\ntwo\n__DONE__:0\n", encoding="utf-8"
            )
            with urllib.request.urlopen(base + "/mesh/progress/verify", timeout=10) as stream:
                events = stream.read().decode("utf-8", "replace")
            assert_contains(
                events,
                ["data: one", "data: two", "event: done", "data: 0"],
                "progress stream",
            )

            print("mesh smoke test ok")
            return 0
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    sys.exit(main())
