#!/usr/bin/env python3
"""Smoke test for Mesh's devices, its alerts and its paths.

Builds Mesh and starts it against a temporary state directory whose path has a
space in it, beside a stand-in Shelly plug on the loopback, and checks:

- paths with a space (MESH-0024): a mesh is created, its plan saved and kept as
  revisions, and deleting it removes that mesh only — not a folder that shares
  the first word of the path;
- a switch shows the state the device reports (MESH-0022): a plug that switches
  is confirmed (200) from its status read back; one whose relay sticks is
  pending (202) and never shown as switched, then unconfirmed once the wait is
  over; a state that is neither on nor off is refused; a plug that does not
  answer is 502 and nothing is recorded;
- a source's action (MESH-API-0010) is a POST that needs a control grant: a GET
  is 405 and switches nothing, a read-only token is refused, a cookie session
  needs the form token, no state is 400, and the provider's request (a bearer
  ticket for mesh:<project>) is answered confirmed (200) or pending (202);
- a device that reports over several MQTT topics (MESH-0029..0032, MESH-0022):
  a stand-in Tasmota plug on a broker of the smoke's own, planned from a
  manifest in tests/fixtures that follows the contract with robohub-devices —
  with Mesh held to the broker grants its manifest's `bus:` block asks for —
  its stat/RESULT, stat/POWER, tele/STATE and tele/SENSOR merged into one
  reading, a switch confirmed only once stat/RESULT or stat/POWER reports the
  new state (a newer meter reading does not), a relay that sticks pending and
  then unconfirmed, a word that is neither on nor off kept out of the store,
  and tele/LWT Offline shown as unreachable;
- alerts (MESH-0023), through the log provider: a gateway that stops answering
  is one alert however many polls fail, and a board's revocation that cannot
  be sent to its gateway is an alert too;
- a board's health (MESH-0038), from a stand-in gateway whose listing carries a
  watchdog report: one alert naming the board, the reason, the task and the
  code, and the report on the board's Data tab;
- an update over the link (MESH-0039, MESH-0040): an update job whose build is
  done is run by the job runner; Mesh makes the mesh's update key (owner-only),
  signs the manifest for the image, hands both to the stand-in gateway, follows
  it to confirmed and records the deployed signature; a second update the board
  rolls back is an alert naming the board.

    python3 tests/devices_smoke.py
"""

import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve()
MESH = HERE.parents[1]
PROJECTS = MESH.parent
RHO = PROJECTS / "rho" / "bin" / "rho"
RHO_CMD = str(RHO) if RHO.exists() else "rho"
TOKEN = ""
failures = []


def check(ok: bool, what: str) -> None:
    print(("[PASS] " if ok else "[FAIL] ") + what)
    if not ok:
        failures.append(what)


class Plug:
    """A Shelly Plug's Gen2 RPC, as much of it as Mesh uses."""
    output = False
    stuck = False     # takes Switch.Set and answers, but the relay does not move
    sets = 0


class PlugHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        if url.path == "/rpc/Switch.Set":
            Plug.sets += 1
            was = Plug.output
            if not Plug.stuck:
                Plug.output = query.get("on", ["false"])[0] == "true"
            body = {"was_on": was}
        elif url.path in ("/rpc/Shelly.GetStatus",):
            body = {"switch:0": {"id": 0, "output": Plug.output, "apower": 12.5 if Plug.output else 0.0,
                                 "voltage": 230.1, "current": 0.05, "aenergy": {"total": 1000.0}}}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class Tasmota:
    """A plug running Tasmota, as much of it as Mesh uses: it takes cmnd/POWER,
    answers on stat/RESULT and/or stat/POWER, and reports tele/STATE,
    tele/SENSOR and tele/LWT. Everything goes through the smoke's broker."""

    def __init__(self, port: int, prefix: str):
        self.port, self.prefix = port, prefix
        self.relay = False
        self.stuck = False        # takes the command, the relay does not move
        self.answer = "result"    # result | power | silent
        self.commands = []
        self.sensor = True
        self.listener = None

    def publish(self, suffix: str, payload: str, retain: bool = False) -> None:
        command = ["mosquitto_pub", "-h", "127.0.0.1", "-p", str(self.port), "-u", "s60", "-P", BUS_PASSWORD,
                   "-t", f"{self.prefix}/{suffix}", "-m", payload]
        subprocess.run(command + (["-r"] if retain else []), check=True, capture_output=True, timeout=10)

    def word(self) -> str:
        return "ON" if self.relay else "OFF"

    def start(self) -> None:
        self.listener = subprocess.Popen(
            ["mosquitto_sub", "-h", "127.0.0.1", "-p", str(self.port), "-u", "s60", "-P", BUS_PASSWORD,
             "-t", f"{self.prefix}/cmnd/POWER"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        threading.Thread(target=self.listen, daemon=True).start()
        threading.Thread(target=self.report, daemon=True).start()
        time.sleep(0.5)

    def listen(self) -> None:
        for line in self.listener.stdout:
            command = line.strip()
            self.commands.append(command)
            if not self.stuck and command.lower() in ("on", "off", "true", "false", "1", "0"):
                self.relay = command.lower() in ("on", "true", "1")
            if self.answer == "result" or self.stuck:
                self.publish("stat/RESULT", json.dumps({"POWER": self.word()}))
            elif self.answer == "power":
                self.publish("stat/POWER", self.word())

    def report(self) -> None:
        # A short TelePeriod: the meter every second, never the relay.
        while self.listener and self.listener.poll() is None:
            if self.sensor:
                power = 12.5 if self.relay else 0.0
                self.publish("tele/SENSOR", json.dumps({"Time": "2026-11-19T20:00:00", "ENERGY": {
                    "Power": power, "Voltage": 230.1, "Current": round(power / 230.1, 3), "Today": 0.42, "Total": 7.5}}))
            time.sleep(1)

    def stop(self) -> None:
        if self.listener:
            self.listener.terminate()
            self.listener.wait(timeout=5)


def bus_acl(manifest: Path) -> str:
    """Mesh's broker grants as the keeper writes them (robohub-keeper's
    mqtt_acl_lines): a `topic write` per `bus.publish` entry and a `topic read`
    per `bus.subscribe` entry of Mesh's module.yml, so the smoke's Mesh is held to
    exactly what an installation's broker allows it."""
    text = manifest.read_text()
    block = re.search(r"^bus:\n((?:[ \t]+.*\n)+)", text, re.M)
    lists = dict(re.findall(r"^\s+(publish|subscribe):\s*\[(.*)\]", block.group(1), re.M)) if block else {}
    out = "user mesh\n"
    for kind, verb in (("publish", "write"), ("subscribe", "read")):
        for topic in lists.get(kind, "").split(","):
            if topic.strip():
                out += f"topic {verb} {topic.strip()}\n"
    return out + "\n"


BUS_PASSWORD = "smoke-bus-password"


def start_broker(root: Path):
    port = free_port()
    config = root / "mosquitto.conf"
    passwd = root / "mosquitto.passwd"
    passwd.write_text(f"mesh:{BUS_PASSWORD}\ns60:{BUS_PASSWORD}\n")
    os.chmod(passwd, 0o600)
    subprocess.run(["mosquitto_passwd", "-U", str(passwd)], check=True, capture_output=True)
    (root / "mesh-bus.secret").write_text(BUS_PASSWORD + "\n")
    acl = root / "mosquitto.acl"
    acl.write_text(bus_acl(MESH / "module.yml") + "user s60\ntopic readwrite s60/#\n")
    os.chmod(acl, 0o600)
    config.write_text(f"listener {port} 127.0.0.1\nallow_anonymous false\npassword_file {passwd}\n"
                      f"acl_file {acl}\npersistence false\n")
    broker = subprocess.Popen(["mosquitto", "-c", str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 10
    while time.time() < deadline:
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return broker, port
        time.sleep(0.1)
    broker.kill()
    raise SystemExit("the smoke's broker did not start")


def device_catalog(root: Path) -> Path:
    """An orchestrator root whose robohub-devices holds the Shelly manifest of the
    devices checkout and the Tasmota fixture, so the smoke plans both without
    depending on which manifests the checkout carries."""
    robohub = root / "robohub"
    devices = root / "devices"
    (robohub / "registry" / "modules").mkdir(parents=True)
    shelly = devices / "devices" / "plug" / "shelly-plug-m-gen3"
    tasmota = devices / "devices" / "plug" / "tasmota-plug-fixture"
    shelly.mkdir(parents=True)
    tasmota.mkdir(parents=True)
    shutil.copy(PROJECTS / "robohub-devices" / "devices" / "plug" / "shelly-plug-m-gen3" / "module.yml", shelly / "module.yml")
    shutil.copy(HERE.parent / "fixtures" / "devices" / "tasmota-plug" / "module.yml", tasmota / "module.yml")
    (devices / "module.yml").write_text("module:\n  name: robohub-devices\n  type: collection\n  version: 0.0.0\n"
                                        "devices:\n  - devices/plug/shelly-plug-m-gen3\n  - devices/plug/tasmota-plug-fixture\n")
    (robohub / "registry" / "modules" / "robohub-devices.yml").write_text(
        f"name: robohub-devices\nrole: collection\npath: {devices}\n")
    return robohub


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(base: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/healthz", timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.2)
    raise SystemExit(f"mesh did not start at {base}")


def mint_token(home: str) -> str:
    keys = os.path.join(home, ".rho", "auth")
    os.makedirs(keys, exist_ok=True)
    subprocess.run([RHO_CMD, "auth", "keygen", "--dir", keys, "--force"], check=True, capture_output=True, text=True)
    return subprocess.run(
        [RHO_CMD, "auth", "ticket", "--private", os.path.join(keys, "private.pem"), "--issuer", "keeper",
         "--audience", "mesh", "--subject", "smoke", "--scopes", "*", "--ttl", "1h"],
        check=True, capture_output=True, text=True).stdout.strip()


def ticket(home: str, scopes: str, subject: str = "smoke") -> str:
    """Another ticket from the key mint_token made, with the scopes given."""
    keys = os.path.join(home, ".rho", "auth")
    return subprocess.run(
        [RHO_CMD, "auth", "ticket", "--private", os.path.join(keys, "private.pem"), "--issuer", "keeper",
         "--audience", "mesh", "--subject", subject, "--scopes", scopes, "--ttl", "1h"],
        check=True, capture_output=True, text=True).stdout.strip()


def send(base: str, method: str, path: str, fields: dict = None, bearer: str = "", cookie: str = ""):
    """A request with exactly the credential given: a bearer ticket, a session cookie, or none."""
    data = urllib.parse.urlencode(fields).encode() if fields is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    if bearer:
        request.add_header("Authorization", f"Bearer {bearer}")
    if cookie:
        request.add_header("Cookie", f"rho_session_mesh={cookie}")
    if data is not None:
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as answer:
        return answer.code, answer.read().decode("utf-8", "replace")


def call(base: str, method: str, path: str, fields: dict = None, hx: bool = False):
    data = urllib.parse.urlencode(fields).encode() if fields is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    request.add_header("Authorization", f"Bearer {TOKEN}")
    if data is not None:
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
    if hx:
        request.add_header("HX-Request", "true")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as answer:
        return answer.code, answer.read().decode("utf-8", "replace")


def as_json(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return {}


def device(base: str, index: int) -> dict:
    _, body = call(base, "GET", "/mesh/api/devices")
    for entry in as_json(body) or []:
        if entry.get("id") == index:
            return entry
    return {}


class Gateway:
    """A gateway's listing and outbox, as much of them as Mesh's poll reads,
    and an update's resource: taken, then sending, then how it ended."""
    nodes = []
    updates = []         # (offer bytes, image bytes) as Mesh posted them
    update_end = "confirmed"
    update_polls = 0


class GatewayHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if url.path.endswith("/update"):
            offer = bytes.fromhex(urllib.parse.parse_qs(url.query).get("offer", [""])[0])
            image = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            Gateway.updates.append((offer, image))
            Gateway.update_polls = 0
            data = json.dumps({"id": 1, "state": "offered", "next": 0, "chunks": 2}).encode()
            self.send_response(202)
        else:
            data = b"{}"
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/api/v1/telemetry/outbox":
            body = []
        elif url.path == "/api/v1/nodes":
            body = Gateway.nodes
        elif url.path.endswith("/update"):
            Gateway.update_polls += 1
            state = "sending" if Gateway.update_polls < 2 else Gateway.update_end
            body = {"id": 1, "state": state, "reason": "", "next": 1, "chunks": 2}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def main() -> int:
    global TOKEN
    result = subprocess.run([RHO_CMD, "build", "."], cwd=MESH, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit("mesh build failed:\n" + result.stdout + result.stderr)
    binary = MESH / "build" / "mesh"

    plug = http.server.ThreadingHTTPServer(("127.0.0.1", free_port()), PlugHandler)
    threading.Thread(target=plug.serve_forever, daemon=True).start()
    plug_url = f"http://127.0.0.1:{plug.server_address[1]}"
    dead_gateway = f"http://127.0.0.1:{free_port()}"
    gateway = http.server.ThreadingHTTPServer(("127.0.0.1", free_port()), GatewayHandler)
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    watched_gateway = f"http://127.0.0.1:{gateway.server_address[1]}"
    # Board 1 of area 8 restarted on its watchdog, with a record; board 2 was
    # simply powered on.
    Gateway.nodes = [
        {"area_id": 8, "node_id": 1, "temperature_c": 0.0, "health": {
            "reset_reason": "watchdog", "reset_code": 2, "boot_count": 5, "firmware_version": 7,
            "received_at_ms": 1795192172963,
            "fault": {"code": 2, "task": 3, "pc": 268440116, "uptime_ms": 61000}}},
        {"area_id": 8, "node_id": 2, "health": {
            "reset_reason": "power-on", "reset_code": 1, "boot_count": 1, "firmware_version": 7,
            "received_at_ms": 1795192172964, "fault": None}},
    ]

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    root = Path(tempfile.mkdtemp(prefix="mesh-devices-"))
    broker = None
    tasmota = None
    try:
        broker, broker_port = start_broker(root)
        orchestrator = device_catalog(root)
        # The state folder's path has a space; a folder named like its first
        # word sits beside it, which a path split at the space would reach.
        state = root / "mesh state"
        projects = state / "projects"
        decoy = root / "mesh"
        decoy.mkdir()
        (decoy / "keep.txt").write_text("not Mesh's\n")
        # A mesh whose gateway is a board on another host that does not answer,
        # with a board that can be revoked (the plan written as an older Mesh did).
        silent = projects / "Silent"
        silent.mkdir(parents=True)
        (silent / "project.txt").write_text(
            f"project.name=Silent\narea_id=6\ngateway=not-a-known-board\ngateway_url={dead_gateway}\n"
            "node.count=1\nnode.1.name=Board\nnode.1.parent=gateway\nnode.1.role=node\nnode.1.id=0123456789abcdef\n")
        # A mesh whose gateway is on another host and answers, with two boards
        # that reported their health.
        watched = projects / "Watched"
        watched.mkdir(parents=True)
        (watched / "project.txt").write_text(
            f"project.name=Watched\narea_id=8\ngateway=not-a-known-board\ngateway_url={watched_gateway}\n"
            "node.count=2\nnode.1.name=Sensor\nnode.1.parent=gateway\nnode.1.role=node\nnode.1.id=fedcba9876543210\n"
            "node.2.name=Calm\nnode.2.parent=gateway\nnode.2.role=node\nnode.2.id=00112233aabbccdd\n")
        home = root / "home"
        TOKEN = mint_token(str(home))
        alerts_log = home / ".rho" / "mesh" / "alerts.log"
        env = dict(os.environ)
        env.pop("RHO_CLIENT_ID", None)
        env["ROBOHUB_WORKSPACE"] = str(PROJECTS)
        env["RHO_MESH_STATE_DIR"] = str(state)
        env["ROBOHUB_MESH_PROJECTS"] = str(projects)
        env.setdefault("RHO_HOME", os.path.join(os.path.expanduser("~"), ".rho"))
        env["HOME"] = str(home)
        env["RHO_HTTP_ADDR"] = f"127.0.0.1:{port}"
        env["ROBOHUB_ROOT"] = str(orchestrator)
        env["ROBOHUB_BUS_URL"] = f"127.0.0.1:{broker_port}"
        # Mesh connects as the keeper's broker user for it, held to the grants its
        # manifest's `bus:` block asks for (bus_acl).
        env["ROBOHUB_BUS_USER"] = "mesh"
        env["ROBOHUB_BUS_PASSWORD_FILE"] = str(root / "mesh-bus.secret")
        log_path = root / "mesh.log"
        log = open(log_path, "w")
        process = subprocess.Popen([str(binary)], cwd=MESH, env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
        try:
            wait_for(base)

            # --- Paths with a space --------------------------------------
            # @verifies MESH-0024
            call(base, "POST", "/mesh/project/new", {"name": "Bench"})
            bench = projects / "Bench"
            check((bench / "plan.yml").exists(), "a mesh is created in a state folder whose path has a space")
            status, _ = call(base, "POST", "/mesh/p/Bench/node",
                             {"tab": "new", "kind": "device", "name": "Lamp", "device": "robohub-device-shelly-plug-m-gen3",
                              "transport": "http", "address": plug_url, "device_id": "", "parent": "gateway"}, hx=True)
            plan = (bench / "plan.yml").read_text() if (bench / "plan.yml").exists() else ""
            check(status == 200 and plug_url in plan, f"its plan is saved there ({status})")
            check(any((bench / "revisions").glob("*.yml")), "and kept as a revision there")

            # --- A switch shows the state the device reports ---------------
            # @verifies MESH-0022, MESH-0014
            Plug.output, Plug.stuck = False, False
            status, body = call(base, "POST", "/mesh/api/devices/1/switch", {"state": "on"})
            answer = as_json(body)
            check(status == 200 and answer.get("confirmed") is True and answer.get("state") == "on" and Plug.output,
                  f"a plug that switches is confirmed from its status read back ({status} {body})")
            check(device(base, 1).get("state") == "on", "and listed as on")

            Plug.stuck = True
            status, body = call(base, "POST", "/mesh/api/devices/1/switch", {"state": "off"})
            answer = as_json(body)
            check(status == 202 and answer.get("confirmed") is False and answer.get("state") == "pending"
                  and answer.get("reported") == "on",
                  f"a plug whose relay sticks is pending, not off ({status} {body})")
            listed = device(base, 1)
            check(listed.get("state") == "pending" and listed.get("reported") == "on" and listed.get("requested") == "off",
                  f"the list says pending, what was asked and what the plug reports ({listed})")
            # The wait runs out (the request is made to look 60 s old).
            waiting = bench / "switch-1.txt"
            line = waiting.read_text().strip().split("|") if waiting.exists() else []
            if len(line) == 3:
                waiting.write_text(f"{line[0]}|{int(line[1]) - 60}|{line[2]}\n")
            listed = device(base, 1)
            check(listed.get("state") == "unconfirmed" and listed.get("reported") == "on",
                  f"once the wait is over it is unconfirmed, and still not shown off ({listed})")
            ids = re.findall(r"[0-9a-f]{16}", (bench / "plan.yml").read_text())
            _, page = call(base, "GET", f"/mesh/p/Bench/node/{ids[0] if ids else 'none'}?tab=data", hx=True)
            check("off requested, unconfirmed" in page, "the device's page says the switch is unconfirmed")

            Plug.stuck = False
            status, body = call(base, "POST", "/mesh/api/devices/1/switch", {"state": "off"})
            check(status == 200 and as_json(body).get("state") == "off" and device(base, 1).get("state") == "off",
                  f"a new request the plug carries out is confirmed ({status} {body})")

            # --- A source's action: a POST with a control grant -------------
            # @verifies MESH-API-0010, MESH-0022
            _, listing = call(base, "GET", "/mesh/api/projects/Bench/sources")
            outputs = [e for e in (as_json(listing) or []) if e.get("direction") == "output"]
            check(bool(outputs), f"the plug is listed as an output source ({listing[:300]})")
            action = outputs[0].get("action_path", "") if outputs else "/mesh/api/projects/Bench/sources/none/action"
            Plug.output, Plug.stuck = False, False
            sets = Plug.sets
            status, body = send(base, "GET", action + "?state=on", bearer=TOKEN)
            check(status == 405 and Plug.sets == sets and not Plug.output,
                  f"a GET of the action is refused with 405 and switches nothing ({status} {body[:120]})")
            status, _ = send(base, "GET", action, bearer=TOKEN)
            check(status == 405 and Plug.sets == sets and not Plug.output,
                  f"a GET with no state, which used to switch on, switches nothing ({status})")
            for scopes in ("mesh:Bench:read", "mesh:read"):
                status, _ = send(base, "POST", action + "?state=on", bearer=ticket(str(home), scopes))
                check(status == 403 and Plug.sets == sets,
                      f"a read-only token ({scopes}) is refused and switches nothing ({status})")
            status, _ = send(base, "POST", action, {"state": "on"}, cookie=TOKEN)
            check(status == 403 and Plug.sets == sets,
                  f"a cookie session without the form token is refused ({status})")
            status, _ = send(base, "POST", action, {"state": "on", "csrf": "0" * 64}, cookie=TOKEN)
            check(status == 403 and Plug.sets == sets, f"and with a wrong form token ({status})")
            status, _ = send(base, "POST", action, None, bearer=TOKEN)
            check(status == 400 and Plug.sets == sets, f"no state is 400, not on ({status})")
            status, _ = send(base, "POST", action + "?state=banana", bearer=TOKEN)
            check(status == 400 and Plug.sets == sets, f"a state neither on nor off is 400 ({status})")
            # The provider's own request: POST ?state=, no body, a bearer
            # ticket for audience mesh and scope mesh:<project>.
            control = ticket(str(home), "mesh:Bench", "robohub-aquarium")
            status, body = send(base, "POST", action + "?state=on", bearer=control)
            answer = as_json(body)
            check(status == 200 and answer.get("confirmed") is True and answer.get("outcome") == "confirmed" and Plug.output,
                  f"a control token switches it and the answer is confirmed ({status} {body})")
            Plug.stuck = True
            status, body = send(base, "POST", action + "?state=off", bearer=control)
            answer = as_json(body)
            check(status == 202 and answer.get("confirmed") is False and answer.get("outcome") == "pending" and Plug.output,
                  f"a relay that sticks is 202 pending, not off ({status} {body})")
            Plug.stuck = False
            settings = call(base, "GET", "/settings?tab=alerts")[1]
            csrf = re.search(r'name="csrf" value="([^"]*)"', settings)
            status, body = send(base, "POST", action, {"state": "off", "csrf": csrf.group(1) if csrf else ""}, cookie=TOKEN)
            check(status == 200 and not Plug.output,
                  f"a cookie session with the form token switches it ({status} {body})")
            call(base, "POST", "/mesh/api/devices/1/switch", {"state": "off"})

            sets = Plug.sets
            status, _ = call(base, "POST", "/mesh/api/devices/1/switch", {"state": "banana"})
            check(status == 400 and Plug.sets == sets, f"a state that is neither on nor off is refused, not sent ({status})")

            plug.shutdown()
            plug.server_close()
            status, _ = call(base, "POST", "/mesh/api/devices/1/switch", {"state": "on"})
            listed = device(base, 1)
            check(status == 502 and listed.get("state") == "off" and listed.get("requested") == "",
                  f"a plug that does not answer is 502 and nothing is recorded ({status} {listed})")

            # --- A device that reports over several MQTT topics ------------
            # @verifies MESH-0029, MESH-0030, MESH-0031, MESH-0032, MESH-0022, MESH-0014
            tasmota = Tasmota(broker_port, "s60")
            tasmota.start()
            tasmota.publish("tele/LWT", "Online", retain=True)
            known = set(re.findall(r"[0-9a-f]{16}", (bench / "plan.yml").read_text()))
            status, _ = call(base, "POST", "/mesh/p/Bench/node",
                             {"tab": "new", "kind": "device", "name": "Heater", "device": "robohub-device-tasmota-plug-fixture",
                              "transport": "mqtt", "address": "s60", "device_id": "", "parent": "gateway"}, hx=True)
            plan = (bench / "plan.yml").read_text()
            check(status == 200 and "robohub-device-tasmota-plug-fixture" in plan, f"a Tasmota plug is planned over MQTT ({status})")
            fresh = [i for i in re.findall(r"[0-9a-f]{16}", plan) if i not in known]
            heater_id = fresh[0] if fresh else "none"
            heater = next((e.get("id") for e in as_json(call(base, "GET", "/mesh/api/devices")[1]) or []
                           if e.get("device") == "robohub-device-tasmota-plug-fixture"), 2)

            def heater_entry() -> dict:
                return device(base, heater)

            def wait_entry(test, timeout: float = 15.0) -> dict:
                deadline = time.time() + timeout
                entry = heater_entry()
                while time.time() < deadline and not test(entry):
                    time.sleep(0.5)
                    entry = heater_entry()
                return entry

            # Mesh follows the plug from its first read; the plug's retained LWT says it is there.
            call(base, "POST", "/mesh/device/poll", {"id": str(heater)})
            listed = wait_entry(lambda e: e.get("reachability") == "online")
            check(listed.get("reachability") == "online", f"tele/LWT Online is online ({listed})")
            check(device(base, 1).get("reachability") == "", "a plug reached over HTTP has no online topic to read")

            # One reading from four topics: the relay from tele/STATE, the meter from tele/SENSOR.
            tasmota.relay = True
            tasmota.publish("tele/STATE", json.dumps({"Time": "2026-11-19T20:00:00", "POWER": "ON", "Wifi": {"RSSI": 76}}))
            time.sleep(1.5)
            _, listing = call(base, "GET", "/mesh/api/projects/Bench/sources")
            heater_sources = {e.get("field"): e.get("value_path") for e in (as_json(listing) or [])
                              if e.get("module_id") == "robohub.device.tasmota-plug-fixture"}

            def value(field: str) -> str:
                path = heater_sources.get(field)
                return call(base, "GET", path)[1].strip() if path else ""

            deadline = time.time() + 15
            while time.time() < deadline and (value("power_w") != "12.5" or value("state") != "on"):
                call(base, "POST", "/mesh/device/poll", {"id": str(heater)})
                time.sleep(0.5)
            check(value("state") == "on" and value("power_w") == "12.5" and value("voltage_v") == "230.1"
                  and value("energy_today_kwh") == "0.42",
                  f"stat/tele topics merge into one reading: state {value('state')}, power {value('power_w')}, "
                  f"voltage {value('voltage_v')}, today {value('energy_today_kwh')} ({sorted(heater_sources)})")

            # A switch confirmed from stat/RESULT, the command's answer.
            tasmota.answer = "result"
            status, body = call(base, "POST", f"/mesh/api/devices/{heater}/switch", {"state": "off"})
            listed = wait_entry(lambda e: e.get("state") == "off")
            check(status in (200, 202) and tasmota.commands[-1:] == ["false"],
                  f"the switch is published to cmnd/POWER as false ({status} {tasmota.commands[-3:]})")
            check(listed.get("state") == "off" and listed.get("requested") == "" and not tasmota.relay,
                  f"it is confirmed once stat/RESULT reports OFF ({listed})")

            # A plug that switches but has not said so yet: newer meter readings do
            # not confirm it; its bare stat/POWER word does.
            tasmota.answer = "silent"
            status, body = call(base, "POST", f"/mesh/api/devices/{heater}/switch", {"state": "on"})
            time.sleep(2.5)
            listed = heater_entry()
            check(status == 202 and tasmota.relay and listed.get("state") == "pending" and listed.get("requested") == "on",
                  f"a switch without a stat/ answer stays pending, whatever tele/SENSOR says ({status} {listed})")
            tasmota.publish("stat/POWER", "ON")
            listed = wait_entry(lambda e: e.get("state") == "on")
            check(listed.get("state") == "on" and listed.get("requested") == "",
                  f"it is confirmed once stat/POWER says ON ($ path, onoff word) ({listed})")

            # A relay that sticks: the plug answers with the state it is still in.
            tasmota.answer, tasmota.stuck = "result", True
            status, body = call(base, "POST", f"/mesh/api/devices/{heater}/switch", {"state": "off"})
            time.sleep(2)
            listed = heater_entry()
            check(listed.get("state") == "pending" and listed.get("reported") == "on" and listed.get("requested") == "off",
                  f"a stuck relay is pending, still reported on ({status} {listed})")
            waiting = bench / f"switch-{heater}.txt"
            line = waiting.read_text().strip().split("|") if waiting.exists() else []
            if len(line) == 3:
                waiting.write_text(f"{line[0]}|{int(line[1]) - 60}|{line[2]}\n")
            listed = heater_entry()
            check(listed.get("state") == "unconfirmed" and listed.get("reported") == "on",
                  f"and unconfirmed once the wait is over, never shown off ({listed})")
            tasmota.stuck = False

            # A word that is neither on nor off is a protocol error: the state keeps its value.
            before = value("state")
            tasmota.publish("stat/POWER", "BLINK")
            time.sleep(1)
            call(base, "POST", "/mesh/device/poll", {"id": str(heater)})
            check(before == "on" and value("state") == "on",
                  f"stat/POWER BLINK leaves the state as it was ({before} -> {value('state')})")
            log.flush()
            check("BLINK" in log_path.read_text(), "and the log says the value was refused")

            # tele/LWT Offline: the plug is unreachable, on the list and on its page.
            tasmota.publish("tele/LWT", "Offline", retain=True)
            listed = wait_entry(lambda e: e.get("reachability") == "unreachable")
            check(listed.get("reachability") == "unreachable", f"tele/LWT Offline is unreachable ({listed})")
            _, page = call(base, "GET", f"/mesh/p/Bench/node/{heater_id}?tab=data", hx=True)
            check("Unreachable" in page, "the device's page says it is unreachable" + ("" if "Unreachable" in page else " " + page[-1500:]))
            tasmota.stop()
            tasmota = None

            # --- Alerts --------------------------------------------------
            # @verifies MESH-0023
            def alerts(needle: str) -> list:
                text = alerts_log.read_text() if alerts_log.exists() else ""
                return [l for l in text.splitlines() if needle in l]
            deadline = time.time() + 40
            while time.time() < deadline and not alerts("the gateway does not answer"):
                time.sleep(0.5)
            # The poll backs off 2, 4, 8 s: several more failures in this time.
            time.sleep(12)
            down = alerts("the gateway does not answer")
            check(len(down) == 1 and "[ERROR]" in down[0] and "Silent" in down[0],
                  f"a gateway that stops answering is one alert, however many polls fail ({down})")
            status, _ = call(base, "POST", "/mesh/p/Silent/node/0123456789abcdef/revoke", {"action": "revoke"}, hx=True)
            unsent = alerts("board list could not be sent")
            check(status == 200 and len(unsent) == 1 and "[ERROR]" in unsent[0],
                  f"a revocation that cannot reach the gateway is an alert ({status} {unsent})")
            call(base, "POST", "/mesh/p/Silent/node/0123456789abcdef/revoke", {"action": "restore"}, hx=True)
            check(len(alerts("board list could not be sent")) == 1, "and the next one within the cooldown is not another")

            # --- A board's health ------------------------------------------
            # @verifies MESH-0038
            deadline = time.time() + 40
            while time.time() < deadline and not alerts("board Sensor restarted"):
                time.sleep(0.5)
            time.sleep(3)
            restarted = alerts("restarted")
            check(len(restarted) == 1 and "[ERROR]" in restarted[0] and "Watched" in restarted[0] and
                  "board Sensor restarted: watchdog (boot 5); watchdog warning (code 2) in task 3 at 0x10001234" in restarted[0],
                  f"a watchdog reset is one alert naming the board, the reason, the task and the code ({restarted})")
            status, page = call(base, "GET", "/mesh/p/Watched/node/fedcba9876543210?tab=data", hx=True)
            check(status == 200 and "Health" in page and "watchdog" in page and "0x10001234" in page and
                  "Boots since power-on" in page,
                  f"the board's Data tab shows its last report ({status})")
            status, page = call(base, "GET", "/mesh/p/Watched/node/00112233aabbccdd?tab=data", hx=True)
            check(status == 200 and "power-on" in page and "Fault before" not in page,
                  f"a power-on report shows no fault ({status})")

            # --- An update over the link ----------------------------------
            # @verifies MESH-0039, MESH-0040
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
            jobs = watched / "jobs"
            jobs.mkdir(exist_ok=True)
            image_path = root / "sensor-image"
            image_bytes = bytes(range(256)) * 3
            image_path.write_bytes(image_bytes)

            def update_job(seq, label):
                # An update job whose build is done, as Mesh queues one: its
                # script waits for Mesh's tick, as mesh_update_script writes it.
                result, progress = jobs / f"{seq}.result", jobs / f"{seq}.progress"
                (jobs / f"{seq}.sh").write_text(
                    f"last=''\nwhile [ ! -f '{result}' ]; do\n"
                    f"  if [ -f '{progress}' ]; then now=$(cat '{progress}'); if [ \"$now\" != \"$last\" ]; then echo \"$now\"; last=\"$now\"; fi; fi\n"
                    f"  sleep 1\ndone\n[ -f '{progress}' ] && cat '{progress}'\nexit \"$(cat '{result}')\"\n")
                (jobs / f"{seq}.image").write_text(f"{image_path}\n1\n")
                (jobs / f"{seq}.job").write_text(
                    f"kind=update\ntarget=node.fedcba9876543210\nlabel={label}\nsignature=sig-{seq}\ncreated=1\n")

            def wait_exit(seq, timeout=60.0):
                end = time.time() + timeout
                while time.time() < end and not (jobs / f"{seq}.exit").exists():
                    time.sleep(0.5)
                return (jobs / f"{seq}.exit").read_text().strip() if (jobs / f"{seq}.exit").exists() else ""

            update_job("000901", "Update Sensor over the link")
            code = wait_exit("000901")
            job_log = (jobs / "000901.log").read_text() if (jobs / "000901.log").exists() else ""
            key_file = watched / "update-key.txt"
            check(code == "0" and "update: confirmed" in job_log, f"the update job ends done when the board confirms ({code}): {job_log[-300:]}")
            check(key_file.exists() and (key_file.stat().st_mode & 0o077) == 0,
                  "the mesh's update key was made, owner-only")
            ok_signed = False
            if Gateway.updates and key_file.exists():
                offer, posted = Gateway.updates[0]
                seed = bytes.fromhex(key_file.read_text().strip())
                public = Ed25519PrivateKey.from_private_bytes(seed).public_key()
                manifest = offer[1:58]
                try:
                    public.verify(offer[58:122], b"rh-update1" + manifest)
                    ok_signed = (len(offer) == 122 and posted == image_bytes and
                                 manifest[5:13] == bytes.fromhex("fedcba9876543210") and
                                 int.from_bytes(manifest[19:23], "little") == len(image_bytes) and
                                 manifest[23:55] == __import__("hashlib").sha256(image_bytes).digest())
                except Exception:  # noqa: BLE001
                    ok_signed = False
            check(ok_signed, "the gateway got the image and a manifest for this board signed with the mesh's key")
            deployed = (watched / "deployed.txt").read_text() if (watched / "deployed.txt").exists() else ""
            end = time.time() + 10
            while time.time() < end and "sig-000901" not in deployed:
                time.sleep(0.5)
                deployed = (watched / "deployed.txt").read_text() if (watched / "deployed.txt").exists() else ""
            check("sig-000901" in deployed, "the board's deployed signature is the updated image's")
            Gateway.update_end = "rolled_back"
            update_job("000902", "Update Sensor over the link")
            code = wait_exit("000902")
            end = time.time() + 10
            while time.time() < end and not alerts("update of board Sensor"):
                time.sleep(0.5)
            rolled = alerts("update of board Sensor")
            check(code == "1" and len(rolled) == 1 and "was rolled back" in rolled[0],
                  f"an update the board rolled back fails, and says so ({code} {rolled})")

            # --- Deleting a mesh whose path has a space -------------------
            # @verifies MESH-0024
            call(base, "POST", "/mesh/project/delete", {"project": "Bench"})
            check(not bench.exists(), "deleting a mesh removes its folder")
            check((decoy / "keep.txt").exists() and silent.exists() and state.exists(),
                  "and nothing else: not the folder named like the path's first word, nor another mesh")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()
            if failures:
                print(log_path.read_text()[-3000:])
    finally:
        gateway.shutdown()
        if tasmota:
            tasmota.stop()
        if broker:
            broker.terminate()
            try:
                broker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                broker.kill()
        shutil.rmtree(root, ignore_errors=True)
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("mesh devices smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
