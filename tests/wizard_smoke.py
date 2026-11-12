#!/usr/bin/env python3
"""Smoke test for the RoboHub Mesh application.

Builds Mesh, starts it against a temporary state directory, and walks the
application the way a browser does: the table of contents, the plan, a saved
network, and the event stream that reports a long action.

The test uses the repository's own registry and wizard documents; it does not
need a board or a gateway. A missing registry is a failed test, because the
planner's promise is that its lists come from there.

Run from anywhere:

    python3 tests/wizard_smoke.py
"""

import json
import os
import re
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
# The workspace: the directory the ecosystem's checkouts sit in, robohub (the
# registry of boards and drivers) and rho among them.
PROJECTS = Path(os.environ.get("ROBOHUB_WORKSPACE") or os.environ.get("RHO_WORKSPACE") or MESH.parent)
RHO = PROJECTS / "rho" / "bin" / "rho"
# The bearer token every request carries: a ticket the keeper would issue,
# minted by the test with a key pair of its own (see mint_token).
TOKEN = ""


# The processes of the native gateways the test had Mesh start.
LIVE_PIDS = []


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
    request = urllib.request.Request(base + path)
    request.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read().decode("utf-8", "replace")


def post(base: str, path: str, fields: dict, timeout: float = 20) -> str:
    body = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(base + path, data=body, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    request.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def get_until(base: str, path: str, needle: str, timeout: float = 5.0) -> str:
    """Read a page until an action the previous step started has settled."""
    deadline = time.time() + timeout
    body = ""
    while time.time() < deadline:
        body = get(base, path)
        if needle in body:
            return body
        time.sleep(0.2)
    return body


def plan_flat(path: Path) -> str:
    """A project's plan.yml as the flat key=value lines Mesh reads it as.

    The plan is a YAML document of nested maps with quoted values; a key "_"
    holds the value of the map it is in.
    """
    out = []
    stack = []
    for raw in path.read_text().splitlines():
        stripped = raw.lstrip(" ")
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(stripped)
        key, _, value = stripped.partition(":")
        key = key.strip().strip('"')
        value = value.strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if not value:
            stack.append((indent, key))
            continue
        if value.startswith('"'):
            value = json.loads(value)
        parts = [name for _, name in stack] + ([] if key == "_" else [key])
        out.append(".".join(parts) + "=" + value)
    return "\n".join(out) + "\n"


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
    # The application finds the toolchain (the documentation generator, the
    # components) by its home; the test gives the application a HOME of its own,
    # so the toolchain's home is named before that happens.
    env.setdefault("RHO_HOME", os.path.join(os.path.expanduser("~"), ".rho"))
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


def mint_token(home: str) -> str:
    """A ticket for audience mesh, as the keeper would issue one.

    Mesh verifies tickets against ~/.rho/auth/public.pem in its own home, so
    the test makes a key pair there and signs the ticket with it.
    """
    rho = str(RHO) if RHO.exists() else "rho"
    keys = os.path.join(home, ".rho", "auth")
    os.makedirs(keys, exist_ok=True)
    subprocess.run([rho, "auth", "keygen", "--dir", keys, "--force"],
                   check=True, capture_output=True, text=True)
    result = subprocess.run(
        [rho, "auth", "ticket", "--private", os.path.join(keys, "private.pem"),
         "--issuer", "keeper", "--audience", "mesh", "--subject", "smoke",
         "--scopes", "*", "--ttl", "1h"],
        check=True, capture_output=True, text=True)
    return result.stdout.strip()


def hx(base: str, path: str, fields: dict = None) -> str:
    """A request as htmx sends it: the inspector answers with its fragment."""
    data = urllib.parse.urlencode(fields).encode() if fields is not None else None
    request = urllib.request.Request(base + path, data=data, method="POST" if fields is not None else "GET")
    request.add_header("Authorization", f"Bearer {TOKEN}")
    request.add_header("HX-Request", "true")
    if data is not None:
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def plan_node_id(plan: str, row: int) -> str:
    """A node's stable id from plan.yml, by its row."""
    import re
    block = plan.split("\nnode:\n", 1)[1]
    match = re.search(r"\n  " + str(row) + r":\n((?:    .*\n)+)", "\n" + block)
    found = re.search(r'    id: "([0-9a-f]{16})"', match.group(1)) if match else None
    return found.group(1) if found else ""


def check_editor(base: str, project: Path) -> None:
    import re
    plan_file = project / "plan.yml"
    page = get(base, "/mesh/p/default")
    assert_contains(page, ["id=\"agora-inspector\"", "id=\"agora-canvas\"", "+ module", "+ device",
                           "hx-get=\"/mesh/p/default/gateway\""], "the agora with its inspector")
    relay = plan_node_id(plan_file.read_text(), 1)
    if not relay:
        raise AssertionError("the relay has no id in the plan")
    fragment = hx(base, f"/mesh/p/default/node/{relay}")
    assert_contains(fragment, ["Relay one", "inspector-tabs", "Save node", "name=\"rev\""], "the relay's inspector")
    if "<html" in fragment:
        raise AssertionError("htmx was answered with a whole page instead of the inspector")
    # A board programmed before signatures were kept is taken to run what is
    # planned: the plan is told so, as a build that programmed it would have.
    text = plan_file.read_text()
    text = re.sub(r'(\nstatus:\n(?:  .*\n)*?    1: )"[a-z]*"', r'\1"programmed"', text, count=1)
    plan_file.write_text(text)
    page = get(base, f"/mesh/p/default?select=node:{relay}")
    if "runs what is planned" not in page:
        raise AssertionError("a programmed board does not show as running what is planned")
    rev = re.search(r'name="rev" value="(\d+)"', page).group(1)
    # A new name builds nothing.
    renamed = hx(base, f"/mesh/p/default/node/{relay}", {
        "tab": "node", "rev": rev, "name": "Relay prime", "description": "", "role": "relay",
        "board": "pico2w", "parent": "gateway"})
    assert_contains(renamed, ["Saved.", "runs what is planned", "hx-swap-oob=\"innerHTML:#agora-canvas\"",
                              "Relay prime"], "a rename")
    if "out of date" in renamed.split("hx-swap-oob")[0]:
        raise AssertionError("a rename marked the board out of date")
    # A form opened before that save is refused.
    stale = hx(base, f"/mesh/p/default/node/{relay}", {
        "tab": "node", "rev": rev, "name": "Somebody else", "role": "relay", "board": "pico2w", "parent": "gateway"})
    assert_contains(stale, ["changed elsewhere"], "a save from a stale form")
    if "Somebody else" in plan_file.read_text():
        raise AssertionError("a stale save was written")
    rev = str(int(rev) + 1)
    # A module fitted to it is in its image: it alone is out of date.
    fitted = hx(base, f"/mesh/p/default/node/{relay}", {
        "tab": "modules", "rev": rev, "action": "add", "module": "ds18b20-temperature-module"})
    assert_contains(fitted, ["fitted.", "out of date (modules changed)"], "a module fitted")
    rev = str(int(rev) + 1)
    # A device under the gateway is added in place, and deleted again.
    count = int(re.search(r'\n  count: "(\d+)"', plan_file.read_text()).group(1))
    added = hx(base, "/mesh/p/default/node", {
        "tab": "new", "kind": "device", "name": "Bench lamp", "device": "robohub-device-shelly-plug-m-gen3",
        "transport": "http", "address": "http://192.0.2.77", "device_id": "", "parent": "gateway"})
    assert_contains(added, ["Bench lamp added."], "a device added")
    lamp = plan_node_id(plan_file.read_text(), count + 1)
    if not lamp or "Bench lamp" not in get(base, "/mesh/p/default"):
        raise AssertionError("the device added is not on the tree")
    deleted = hx(base, f"/mesh/p/default/node/{lamp}/delete", {"tab": "delete", "rev": "0", "action": "delete"})
    assert_contains(deleted, ["Bench lamp deleted."], "a device deleted")
    if "Bench lamp" in plan_file.read_text():
        raise AssertionError("the deleted device is still in the plan")
    if plan_node_id(plan_file.read_text(), 1) != relay:
        raise AssertionError("deleting another node changed the relay's id")
    # The link is edited on its own: a transport the boards cannot share is
    # refused, and new settings put the board out of date for its link.
    refused = hx(base, f"/mesh/p/default/edge/{relay}", {"tab": "transport", "rev": rev, "transport": "spi"})
    assert_contains(refused, ["Both ends must be able to serve that transport"], "a transport neither end has")
    linked = hx(base, f"/mesh/p/default/edge/{relay}", {
        "tab": "settings", "rev": rev, "node.1.parent_endpoint": "/dev/ttyUSB3",
        "node.1.child_endpoint": "uart1", "node.1.baud": "57600"})
    assert_contains(linked, ["Link saved."], "a link's settings")
    if "link, modules changed" not in linked:
        raise AssertionError("new link settings did not put the board out of date: " + linked[:900])
    # Without htmx a form is answered with the page it came from.
    request = urllib.request.Request(base + f"/mesh/p/default/node/{relay}", method="POST",
        data=urllib.parse.urlencode({"tab": "node", "name": "Relay one", "role": "relay", "board": "pico2w",
                                     "parent": "gateway"}).encode())
    request.add_header("Authorization", f"Bearer {TOKEN}")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(request, timeout=20) as response:
        landed = response.geturl()
        body = response.read().decode("utf-8", "replace")
    if f"select=node:{relay}" not in landed or "Relay one" not in body:
        raise AssertionError(f"a plain form post did not come back to its node ({landed})")

    # Apply: the relay is out of date, so "Apply changes" offers to build and
    # flash it. Building it is a job that runs in the background; flashing it
    # waits on its card for the person to put the board in BOOTSEL.
    apply = hx(base, "/mesh/p/default/apply")
    assert_contains(apply, ["Apply changes", "Relay one", "build, then flash"], "the apply dialog")
    queued = hx(base, "/mesh/p/default/apply", {"only": f"node:{relay}"})
    assert_contains(queued, ["Queued."], "the relay queued")
    jobs = project / "jobs"
    build_job, flash_job = sorted(p.stem for p in jobs.glob("*.job"))[-2:]
    if (project / "boards").exists() is False:
        raise AssertionError("the board was not prepared beside its project")
    deadline = time.time() + 900
    while time.time() < deadline and not (jobs / f"{build_job}.exit").exists():
        time.sleep(2)
    code = (jobs / f"{build_job}.exit").read_text().strip() if (jobs / f"{build_job}.exit").exists() else "none"
    if code != "0":
        log = (jobs / f"{build_job}.log").read_text() if (jobs / f"{build_job}.log").exists() else ""
        raise AssertionError(f"the relay's build job ended {code}:\n{log[-3000:]}")
    flash = hx(base, f"/mesh/p/default/job/{flash_job}")
    assert_contains(flash, ["Flash Relay one", "waiting", "BOOTSEL", "The board is ready: flash it"], "the flash waiting")
    if "waiting for you" not in get(base, "/mesh/p/default"):
        raise AssertionError("the card does not say the flash waits for the person")
    cancelled = hx(base, f"/mesh/p/default/job/{flash_job}/cancel", {})
    assert_contains(cancelled, ["Cancelled."], "the flash cancelled")
    deadline = time.time() + 20
    while time.time() < deadline and not (jobs / f"{flash_job}.done").exists():
        time.sleep(1)
    if (jobs / f"{flash_job}.exit").read_text().strip() != "130":
        raise AssertionError("a cancelled flash did not end as cancelled")
    after = hx(base, f"/mesh/p/default/node/{relay}")
    if "out of date" not in after or "runs what is planned" in after:
        raise AssertionError("a flash that never ran recorded what the board runs")


def main() -> int:
    global TOKEN
    validate_wizard_schema()
    binary = build_mesh()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="mesh-app-") as state:
        env = dict(os.environ)
        env["ROBOHUB_WORKSPACE"] = str(PROJECTS)
        env["RHO_MESH_STATE_DIR"] = state
        env["ROBOHUB_MESH_PROJECTS"] = os.path.join(state, "projects")
        # A project written before plan.yml is imported when Mesh first reads it.
        legacy = Path(state) / "projects" / "Old-mesh"
        legacy.mkdir(parents=True)
        (legacy / "project.txt").write_text(
            "project.name=Old mesh\nnode.count=2\nnode.1.name=Pump: main\nnode.1.parent=gateway\n"
            "node.2.name=Probe\nnode.2.parent=node.1\n")
        home = os.path.join(state, "home")
        TOKEN = mint_token(home)
        env.setdefault("RHO_HOME", os.path.join(os.path.expanduser("~"), ".rho"))
        # Tools installed with pip --user (cmake among them) find their package
        # under the user base, which follows HOME unless it is named.
        env.setdefault("PYTHONUSERBASE", os.path.join(os.path.expanduser("~"), ".local"))
        # Mesh builds the gateway with Go; the machine's caches are named before
        # HOME changes, so a build does not start from an empty cache.
        for name in ("GOCACHE", "GOMODCACHE", "GOPATH"):
            value = subprocess.run(["go", "env", name], capture_output=True, text=True).stdout.strip()
            if value:
                env.setdefault(name, value)
        env["HOME"] = home
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
            old_plan = Path(state) / "projects" / "Old-mesh" / "plan.yml"
            if not old_plan.exists() or not (old_plan.parent / "project.txt.imported").exists():
                raise AssertionError("the project written before plan.yml was not imported")
            assert_contains(plan_flat(old_plan),
                            ["project.name=Old mesh", "node.1.name=Pump: main", "node.1.parent=gateway",
                             "node.1.number=1", "node.2.number=2", "node.2.parent_node=1", "revision="],
                            "imported plan")
            assert_contains(mesh, ["Old mesh"], "imported project listed")
            assert_contains(
                mesh,
                ["RoboHub Mesh", "Projects", "Create project", "Documentation"],
                "mesh overview",
            )
            if "Operations for" in mesh:
                raise AssertionError("Operations must be hidden when no project is active")

            docs = get(base, "/mesh/docs")
            assert_contains(docs, ["RoboHub Mesh", "Projects", "top bar"], "documentation")

            # The Alerts tab is the alert interface's binding: what it saves is
            # a channel's setting, kept in the application's own bind.conf and
            # read back on the next page.
            alerts = get(base, "/settings?tab=alerts")
            assert_contains(alerts, ["log_path", "email_to", "telegram_chat"], "alerts settings")
            csrf = re.search(r'name="csrf" value="([^"]*)"', alerts)
            if not csrf:
                raise AssertionError("the alerts form must carry its CSRF token")
            post(
                base,
                "/settings?tab=alerts",
                {
                    "csrf": csrf.group(1),
                    "log_path": os.path.join(state, "alerts.log"),
                    "log_threshold": "WARNING",
                    "email_enabled": "false",
                    "email_to": "ops@example.com",
                    "email_threshold": "critical",
                    "telegram_enabled": "false",
                    "telegram_chat": "",
                    "telegram_token": "",
                    "telegram_threshold": "critical",
                },
            )
            bind_conf = Path(home) / ".rho" / "apps" / "mesh" / "bind.conf"
            if not bind_conf.exists():
                raise AssertionError("the alert settings must be saved in the application's bind.conf")
            assert_contains(
                bind_conf.read_text(),
                ["robohub-provider-email.to_address=ops@example.com",
                 "robohub-provider-log.severity_threshold=WARNING"],
                "saved alert settings",
            )
            if bind_conf.stat().st_mode & 0o077:
                raise AssertionError("bind.conf may hold secrets and must be its owner's only")
            assert_contains(get(base, "/settings?tab=alerts"), ["ops@example.com"], "alert settings read back")

            # A new project is active from the start when none was.
            post(base, "/mesh/project/new", {"name": "default"})
            active = get(base, "/mesh")
            assert_contains(
                active,
                ["Operations for", "<strong>default</strong>", "nav-badge\">Active",
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
                    'name="parent"',
                    'name="link"',
                    'value="pico2w"',
                    "form-actions",
                ],
                "node editor",
            )
            if 'name="sensors"' in node_form:
                raise AssertionError("modules are nodes of their own, not a field on the node")

            native_gateway = get(base, "/mesh/gateway")
            assert_contains(native_gateway, ["native host has no pins"], "native gateway")
            if "Add module" in native_gateway:
                raise AssertionError("a native host cannot take modules")

            post(
                base,
                "/mesh/gateway",
                {
                    "name": "Gateway",
                    "description": "The UNO Q gateway.",
                    "gateway": "arduino-uno-q",
                },
            )
            board_gateway = get(base, "/mesh/gateway")
            assert_contains(board_gateway, ["+ Add module"], "board gateway")

            post(
                base,
                "/mesh/node/new",
                {
                    "action": "save",
                    "kind": "compute",
                    "name": "Relay one",
                    "description": "First node",
                    "role": "relay",
                    "board": "pico2w",
                    "parent": "gateway",
                    "link": "uart",
                },
            )
            post(
                base,
                "/mesh/node/new",
                {
                    "action": "save",
                    "kind": "compute",
                    "name": "Leaf two",
                    "description": "Second node",
                    "role": "node",
                    "board": "pico2w",
                    "parent": "node.1",
                    "link": "wifi",
                },
            )

            module_form = get(base, "/mesh/node/new?kind=module&parent=node.1")
            assert_contains(
                module_form,
                [
                    "Add a module",
                    'name="module"',
                    'name="parent"',
                    'value="ds18b20-waterproof-probe-module"',
                    'value="ky-001-temperature-sensor-module"',
                    'value="bmp180-module"',
                    "Module board",
                ],
                "module editor",
            )

            for name, module, parent in [
                ("Temperature probe", "ds18b20-temperature-module", "node.1"),
                ("Humidity probe", "dht11-module", "node.1"),
                ("Gateway pressure", "bmp180-module", "gateway"),
                ("Leaf thermistor", "ky-013-analog-temperature-sensor-module", "node.2"),
                ("Leaf light", "ky-018-photoresistor-module", "node.2"),
            ]:
                post(
                    base,
                    "/mesh/node/new",
                    {
                        "action": "save",
                        "kind": "module",
                        "name": name,
                        "module": module,
                        "parent": parent,
                    },
                )

            post(base, "/mesh/accept", {"action": "accept"})

            accepted = get(base, "/mesh/plan")
            assert_contains(
                accepted,
                ["Edit topology", "Next: network", "Relay one", "Leaf two",
                 "mesh-children", "link-badge", "UART", "WiFi", "module-branch",
                 "Temperature probe", "Humidity probe", "Gateway pressure"],
                "accepted mesh",
            )

            sources = get(base, "/mesh/api/sources")
            assert_contains(
                sources,
                [
                    "module.3.temperature",
                    "\"direction\":\"input\"",
                    "\"unit\":\"°C\"",
                    "\"kinds\":\"numeric chart table\"",
                    "value_path",
                    "action_path",
                ],
                "module sources",
            )

            relay_page = get(base, "/mesh/node/1")
            assert_contains(
                relay_page,
                [
                    "+ Add module",
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
            gateway_network = get_until(base, "/mesh/network/gateway", "Gateway host")
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

            gateway_build = get_until(base, "/mesh/build/gateway", "Program gateway")
            assert_contains(
                gateway_build,
                ["Build gateway", "Program gateway", "Change gateway", "Gateway"],
                "gateway build page",
            )

            wizard_state = Path(state) / "projects" / "default" / "plan.yml"
            saved = plan_flat(wizard_state)
            # Every save is a revision, and the plan before each edit is kept:
            # one copy per burst of saves a form makes, the last twenty.
            revision = int(dict(line.split("=", 1) for line in saved.splitlines() if "=" in line)["revision"])
            if revision < 10:
                raise AssertionError(f"the plan has had many saves but is at revision {revision}")
            kept = sorted(int(p.stem) for p in (wizard_state.parent / "revisions").glob("*.yml"))
            if not kept or kept[-1] >= revision or len(kept) > 20:
                raise AssertionError(f"the revisions kept are {kept} at revision {revision}")
            assert_contains(
                saved,
                [
                    "gateway=arduino-uno-q",
                    "gateway_name=Gateway",
                    "node.count=7",
                    "node.1.name=Relay one",
                    "node.1.board=pico2w",
                    "node.1.kind=compute",
                    "node.2.parent_node=1",
                    "node.3.kind=module",
                    "node.3.module=ds18b20-temperature-module",
                    "node.3.parent_node=1",
                    "node.4.module=dht11-module",
                    "node.5.module=bmp180-module",
                    "node.5.parent=gateway",
                    "node.6.module=ky-013-analog-temperature-sensor-module",
                    "node.7.module=ky-018-photoresistor-module",
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

            # The agora shows the mesh's real nodes, live; the inspector shows
            # the node selected, and links to the page that edits it.
            agora = get(base, "/mesh/agora?project=default")
            assert_contains(agora, ["Relay one", "Gateway", "Inspector", "sse-connect=\"/mesh/agora/events?project=default\"",
                                    "192.0.2.10 · arduino-uno-q"], "agora")
            inspected = get(base, "/mesh/agora?project=default&node=1")
            # The old address of a node in the agora opens the project's own page
            # with that node in the inspector.
            assert_contains(inspected, ["Relay one", "Save node", "pico2w", "agora-node-1", "inspector-tabs"],
                            "agora inspector")
            fleet_page = get(base, "/mesh/fleet")
            assert_contains(fleet_page, ["Fleet", "<title>Relay one</title>", "<svg", "Hosts", "192.0.2.10", "gateways online", "New mesh",
                                         "sse-connect=\"/mesh/fleet/events\""], "fleet view")
            for path, events in (("/mesh/agora/events?project=default", ["event: agora-bar", "event: agora-tree", "event: agora-strip"]),
                                 ("/mesh/fleet/events", ["event: fleet-bar", "event: fleet-cards", "event: fleet-side", "event: fleet-activity"])):
                request = urllib.request.Request(base + path)
                request.add_header("Authorization", f"Bearer {TOKEN}")
                with urllib.request.urlopen(request, timeout=10) as stream:
                    chunk = b""
                    while not all(e.encode() in chunk for e in events) and len(chunk) < 400000:
                        part = stream.read1(65536)
                        if not part:
                            break
                        chunk += part
                assert_contains(chunk.decode("utf-8", "replace"), events, path)
            # A tree event replaces the inside of the element that holds the
            # stream: one that carried the element would replace the connection
            # too, and the page would reconnect after every event.
            request = urllib.request.Request(base + "/mesh/events")
            request.add_header("Authorization", f"Bearer {TOKEN}")
            with urllib.request.urlopen(request, timeout=10) as stream:
                chunk = b""
                while b"event: mesh-tree-edit" not in chunk and len(chunk) < 400000:
                    part = stream.read1(65536)
                    if not part:
                        break
                    chunk += part
            tree_events = chunk.decode("utf-8", "replace")
            assert_contains(tree_events, ["event: mesh-tree", "Relay one"], "tree events")
            if "sse-connect" in tree_events:
                raise AssertionError("a tree event carries the stream's connection")
            # Gateway health is checked in the background, not when a page is
            # drawn, and kept beside the project.
            health = Path(state) / "projects" / "default" / "health.txt"
            deadline = time.time() + 20
            while not health.exists() and time.time() < deadline:
                time.sleep(0.5)
            if not health.exists() or health.read_text().split("|")[0] not in ("online", "offline", "stopped", "none"):
                raise AssertionError("the background did not check the gateway's health")

            # Removing a node moves the rows after it up in one write, and each
            # node keeps the number its firmware and its telemetry carry. A
            # parent is stored by its number, so it needs no rewriting.
            before = int(dict(l.split("=", 1) for l in plan_flat(wizard_state).splitlines() if "=" in l)["revision"])
            post(base, "/mesh/node/5", {"action": "delete", "kind": "module"})
            removed = dict(l.split("=", 1) for l in plan_flat(wizard_state).splitlines() if "=" in l)
            expected = {
                "node.count": "6",
                "node.5.module": "ky-013-analog-temperature-sensor-module",
                "node.5.number": "6",
                "node.6.module": "ky-018-photoresistor-module",
                "node.6.number": "7",
                "node.2.parent_node": "1",
                "node.2.number": "2",
                "node.next_number": "8",
            }
            for key, value in expected.items():
                if removed.get(key) != value:
                    raise AssertionError(f"after removing node 5, {key} = {removed.get(key)!r}, want {value!r}")
            if "node.7.module" in removed:
                raise AssertionError("the last row was not removed")
            if int(removed["revision"]) - before > 20:
                raise AssertionError("removing a node wrote the plan once per key")

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
            if "Operations for" in inactive:
                raise AssertionError("Operations must be hidden when no project is active")
            assert_contains(inactive, ["No active project"], "inactive project menu")
            assert_contains(get(base, "/mesh/plan"), ["Create project"], "step without a project")
            post(
                base,
                "/mesh/project/active",
                {"project": "default", "active": "1", "return": "dashboard"},
            )
            assert_contains(get(base, "/mesh"), ['<strong>default</strong>'], "reactivated project")

            # A project is a folder of its own; creating one starts empty, and
            # an existing project can be opened again.
            post(base, "/mesh/project/new", {"name": "Second project"})
            current_project = (Path(state) / "current-project.txt").read_text().strip()
            if current_project == "default":
                raise AssertionError("a new project must get its own folder")
            second_state = Path(state) / "projects" / "Second-project" / "plan.yml"
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
            saved_project = plan_flat(second_state)
            assert_contains(
                saved_project,
                ["project.name=Renamed project", "project.description=A second test mesh"],
                "renamed project",
            )

            # Two meshes on one machine never share an area or a port.
            get(base, "/mesh/plan")
            get(base, "/mesh/project/default")
            get(base, "/mesh/plan")
            fleet = dict(l.split("=", 1) for l in plan_flat(Path(state) / "fleet.yml").splitlines() if "=" in l)
            for key in ("area", "rest_port", "uplink_port"):
                one, two = fleet.get(f"mesh.default.{key}"), fleet.get(f"mesh.Second-project.{key}")
                if not one or not two or one == two:
                    raise AssertionError(f"the two meshes share {key}: {one!r} and {two!r} ({fleet})")
            if len({fleet[k] for k in fleet if k.endswith(".rest_port")}) != len([k for k in fleet if k.endswith(".rest_port")]):
                raise AssertionError(f"two meshes share a REST port: {fleet}")

            # An operation link names its project, and opening it makes that
            # project the active one; a form drawn for another project is
            # refused rather than written into this one.
            plan_page = get(base, "/mesh/plan?project=Second-project")
            if (Path(state) / "current-project.txt").read_text().strip() != "Second-project":
                raise AssertionError("a link naming a project did not open it")
            if 'name="project" value="Second-project"' not in plan_page:
                raise AssertionError("a page's forms do not carry their project")
            before_default = plan_flat(Path(state) / "projects" / "default" / "plan.yml")
            post(base, "/mesh/reopen", {"project": "default"})
            if plan_flat(Path(state) / "projects" / "default" / "plan.yml") != before_default:
                raise AssertionError("a form for another project changed it")
            refused = plan_flat(second_state)
            if "another project" not in refused:
                raise AssertionError("a form for another project was not refused")
            get(base, "/mesh/project/default")

            post(base, "/mesh/project/delete", {"project": "Second-project"})
            if second_state.parent.exists():
                raise AssertionError("deleting a project must remove its folder")
            if "mesh.Second-project.area" in plan_flat(Path(state) / "fleet.yml"):
                raise AssertionError("a deleted project kept its place in the fleet")
            get(base, "/mesh/project/default")
            current_project = (Path(state) / "current-project.txt").read_text().strip()
            if current_project != "default":
                raise AssertionError("opening a project must make it current")

            # A device is a node of its own, but it is not a compute node: a
            # topology that carries a device must accept without asking the
            # device for a role, board and link.
            post(base, "/mesh/project/new", {"name": "Device project"})
            post(
                base,
                "/mesh/node/new",
                {
                    "action": "save",
                    "kind": "device",
                    "name": "Test plug",
                    "device": "robohub-device-shelly-plug-m-gen3",
                    "transport": "http",
                    "address": "http://192.0.2.50",
                    "device_id": "",
                    "parent": "gateway",
                },
            )
            device_plan = get(base, "/mesh/plan")
            assert_contains(device_plan, ["Test plug", "device"], "device plan")
            device_sources = get(base, "/mesh/api/sources")
            assert_contains(
                device_sources,
                [
                    "device.1.switch",
                    "device.1.power",
                    "\"direction\":\"output\"",
                    "\"direction\":\"input\"",
                    "\"unit\":\"W\"",
                ],
                "device sources",
            )
            accepted_device = post(base, "/mesh/accept", {"action": "accept"})
            assert_contains(accepted_device, ["Topology accepted"], "device topology")
            post(base, "/mesh/project/delete", {"project": "Device-project"})
            get(base, "/mesh/project/default")

            # A native gateway that Mesh starts for a mesh of its own, on a port
            # the test chose: the background finds it online, the agora and the
            # fleet say so, and stopping it ends that process by its pid.
            post(base, "/mesh/project/new", {"name": "Live mesh"})
            post(base, "/mesh/gateway", {"name": "Gateway", "description": "This machine.", "gateway": "native"})
            live_port = free_port()
            # The network is set after the topology is accepted, as the wizard has
            # it, and a topology has at least one node: a plug on the network.
            post(base, "/mesh/node/new", {
                "action": "save", "kind": "device", "name": "Live plug",
                "device": "robohub-device-shelly-plug-m-gen3", "transport": "http",
                "address": "http://192.0.2.51", "device_id": "", "parent": "gateway",
            })
            post(base, "/mesh/accept", {"action": "accept"})
            post(base, "/mesh/network/gateway",
                 {"action": "save_gateway", "gateway_host": "127.0.0.1", "rest_port": str(live_port)})
            # A native gateway Mesh has not started is not down: the agora says it
            # is not started and offers to start it, and no fault is raised.
            live_health = Path(state) / "projects" / "Live-mesh" / "health.txt"
            deadline = time.time() + 20
            while time.time() < deadline and not (live_health.exists() and live_health.read_text().startswith("stopped|")):
                time.sleep(0.5)
            if not live_health.exists() or not live_health.read_text().startswith("stopped|"):
                raise AssertionError("a native gateway never started is not shown as stopped: " +
                                     (live_health.read_text() if live_health.exists() else "no health"))
            assert_contains(get(base, "/mesh/agora?project=Live-mesh"),
                            ["gateway not started", "Build and start the gateway", "name=\"only\" value=\"gateway\""],
                            "agora of a mesh whose gateway is not started")
            if "gateway unreachable" in get(base, "/faults"):
                raise AssertionError("a gateway nobody started was reported unreachable")
            # The agora's button starts the gateway as a job: the page answers at
            # once, the job builds and starts it in the background, and once it
            # has, the gateway runs what is planned.
            queued = hx(base, "/mesh/p/Live-mesh/apply", {"only": "gateway"})
            assert_contains(queued, ["Queued."], "the gateway queued as a job")
            live_jobs = Path(state) / "projects" / "Live-mesh" / "jobs"
            deadline = time.time() + 300
            while time.time() < deadline and not any(live_jobs.glob("*.done")):
                time.sleep(1)
            exits = [p.read_text().strip() for p in live_jobs.glob("*.exit")]
            if exits != ["0"]:
                logs = "".join(p.read_text() for p in live_jobs.glob("*.log"))
                raise AssertionError(f"the gateway job did not finish well ({exits}):\n{logs[-3000:]}")
            if "gateway\t" not in (Path(state) / "projects" / "Live-mesh" / "deployed.txt").read_text():
                raise AssertionError("the gateway job did not record what the gateway runs")
            job_pid = int((Path(state) / "projects" / "Live-mesh" / "gateway.pid").read_text().strip())
            LIVE_PIDS.append(job_pid)
            # A restart builds the gateway before it starts it, and returns to
            # the agora it was asked from, showing the gateway online.
            gateway_binary = PROJECTS / "robohub-gateway" / "build" / "robohub-gateway"
            built_before = gateway_binary.stat().st_mtime if gateway_binary.exists() else 0
            answered = post(base, "/mesh/gateway/restart", {"return": "agora"}, timeout=300)
            assert_contains(answered, ["Live · gateway online", "Rebuild and restart the gateway"],
                            "the agora after the gateway was started")
            if not gateway_binary.exists() or gateway_binary.stat().st_mtime <= built_before:
                raise AssertionError("starting the gateway did not build it")
            live_dir = Path(state) / "projects" / "Live-mesh"
            pid_file = live_dir / "gateway.pid"
            if "rest_port: \"" + str(live_port) + "\"" not in (live_dir / "plan.yml").read_text():
                raise AssertionError("the gateway's REST port was not saved")
            if not pid_file.exists():
                raise AssertionError("the native gateway was not started with its pid written:\n" +
                                     ((live_dir / "gateway.log").read_text() if (live_dir / "gateway.log").exists() else ""))
            pid = int(pid_file.read_text().strip())
            LIVE_PIDS.append(pid)
            healthy = live_dir / "health.txt"
            deadline = time.time() + 30
            while time.time() < deadline and not (healthy.exists() and healthy.read_text().startswith("online|")):
                time.sleep(0.5)
            if not healthy.exists() or not healthy.read_text().startswith("online|"):
                raise AssertionError("the background did not find the native gateway online")
            live_agora = get(base, "/mesh/agora?project=Live-mesh")
            assert_contains(live_agora,
                            ["Live · gateway online", "this machine · native gateway · :" + str(live_port), "Live plug"],
                            "agora of a live mesh")
            assert_contains(get(base, "/mesh/fleet"), ["Live mesh", ":" + str(live_port)], "fleet with a live mesh")
            # A restart of a running native gateway builds it again, rather than
            # starting the last build, and replaces the process.
            built_before = gateway_binary.stat().st_mtime
            post(base, "/mesh/gateway/restart", {"return": "agora"}, timeout=300)
            if gateway_binary.stat().st_mtime <= built_before:
                raise AssertionError("restarting the gateway did not build it again")
            pid = int(pid_file.read_text().strip())
            LIVE_PIDS.append(pid)
            post(base, "/mesh/gateway/stop", {})
            deadline = time.time() + 5
            while time.time() < deadline:
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("stopping the mesh's gateway did not end its process")
            if pid_file.exists():
                raise AssertionError("a stopped gateway kept its pid file")
            post(base, "/mesh/project/delete", {"project": "Live-mesh"})
            get(base, "/mesh/project/default")

            # Editing in place: the inspector beside the tree saves what the
            # wizard's pages did, and an edit marks out of date only the boards
            # whose image it changes.
            check_editor(base, Path(state) / "projects" / "default")

            # A long action writes its progress to a file and the page follows
            # that file. The page connects first and the action writes after,
            # as a real one does, so a fast action's lines — its end among them —
            # must still reach the page. Checked here without a board attached.
            progress = urllib.request.Request(base + "/mesh/progress/verify")
            progress.add_header("Authorization", f"Bearer {TOKEN}")
            with urllib.request.urlopen(progress, timeout=10) as stream:
                (Path(state) / "progress-verify.log").write_text(
                    "one\ntwo\n__DONE__:0\n", encoding="utf-8"
                )
                events = stream.read().decode("utf-8", "replace")
            assert_contains(
                events,
                ["data: one", "data: two", "event: done", "data: 0"],
                "progress stream",
            )

            print("mesh smoke test ok")
            return 0
        finally:
            # A gateway the test had Mesh start does not outlive the test.
            for pid in LIVE_PIDS:
                try:
                    os.kill(pid, 15)
                except OSError:
                    pass
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    sys.exit(main())
