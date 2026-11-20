#!/usr/bin/env python3
"""Smoke test for Zigbee coordinators (ADR-0010, MESH-ZB-0001..0016, MESH-0034, MESH-0035).

Builds Mesh and starts it against a temporary state folder, beside a broker of
the smoke's own (mosquitto, holding Mesh to its manifest's `bus:` grants and
each coordinator to the user the keeper issued it), a stand-in keeper answering
POST/DELETE /api/v1/bus/users (robohub-keeper ADR-0006), and a stand-in
Zigbee2MQTT installed where Mesh looks for the pinned one
(~/.rho/toolchains/zigbee2mqtt/<version>, started by a fake `node` of a major
the plugin names). No serial port is opened: the coordinator's port is
/dev/null, which exists and can be read and written. It checks:

- a coordinator is offered where the gateway can host one, placed under it with
  a typed port, and refused a second time on the same board, with a port that is
  not a path, or a name that is not a topic level (MESH-ZB-0001..0003, 0015);
- a port that does not exist is said before the start (MESH-ZB-0003);
- the start asks the keeper for a broker user confined to the base topic, with
  Mesh's own broker credential in the header, writes the configuration and an
  owner-only secret file from the plugin's template and starts Zigbee2MQTT with
  its data folder and Z2M_ONBOARD_NO_SERVER, its pid beside it (0004..0006);
- a restart sets Mesh's keys again and keeps the network Zigbee2MQTT made
  (0005); the bridge's state, version, firmware and channel are shown (0009);
- joining is refused past 254 s, opened for 60 s with the time shown, and a form
  without Mesh's token is refused (0010);
- the joined plug is listed and matched to the S60ZBTPF manifest, the unknown
  device is listed but not adoptable (0011); adopting it adds a node and renames
  it, the node unrenamed until the bridge says ok (0012);
- the plug is read and switched as an MQTT device under the base topic: its
  availability read from inside JSON (MESH-0034), its switch published as
  {"state":"ON"} (MESH-0035) and confirmed from the state it reports (0014,
  MESH-0022); the coordinator itself is no device in the list (0001);
- an unsealed export names each coordinator's network as left out, a sealed one
  carries it, and an import writes it beside the new mesh's coordinator folder
  before Zigbee2MQTT ever starts there (0016);
- removing the plug asks Zigbee2MQTT to remove it from the network (0013);
  removing the coordinator stops its Zigbee2MQTT, removes its broker user and
  keeps its data folder (0004, 0006).

    python3 tests/zigbee_smoke.py
"""

import http.server
import json
import os
import re
import shutil
import signal
import socket
import stat
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
FIXTURE = HERE.parent / "fixtures" / "zigbee2mqtt"
BUS_PASSWORD = "smoke-bus-password"
PASSPHRASE = "correct horse battery staple"
TOKEN = ""
failures = []


def check(ok: bool, what: str) -> None:
    print(("[PASS] " if ok else "[FAIL] ") + what)
    if not ok:
        failures.append(what)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Broker:
    """A private mosquitto whose users and grants the stand-in keeper changes."""

    def __init__(self, root: Path):
        self.root, self.port = root, free_port()
        self.passwd, self.acl = root / "mosquitto.passwd", root / "mosquitto.acl"
        self.issued = {}   # user -> (password, prefix)
        self.process = None

    def mesh_acl(self) -> str:
        text = (MESH / "module.yml").read_text()
        block = re.search(r"^bus:\n((?:[ \t]+.*\n)+)", text, re.M)
        lists = dict(re.findall(r"^\s+(publish|subscribe):\s*\[(.*)\]", block.group(1), re.M)) if block else {}
        out = "user mesh\n"
        for kind, verb in (("publish", "write"), ("subscribe", "read")):
            for topic in lists.get(kind, "").split(","):
                if topic.strip():
                    out += f"topic {verb} {topic.strip()}\n"
        return out + "\n"

    def write(self) -> None:
        self.passwd.write_text(f"mesh:{BUS_PASSWORD}\nwatcher:{BUS_PASSWORD}\n" +
                               "".join(f"{u}:{p}\n" for u, (p, _) in self.issued.items()))
        os.chmod(self.passwd, 0o600)
        subprocess.run(["mosquitto_passwd", "-U", str(self.passwd)], check=True, capture_output=True)
        self.acl.write_text(self.mesh_acl() + "user watcher\ntopic read #\n\n" +
                            "".join(f"user {u}\ntopic readwrite {prefix}/#\n\n" for u, (_, prefix) in self.issued.items()))
        os.chmod(self.acl, 0o600)

    def start(self) -> None:
        self.write()
        config = self.root / "mosquitto.conf"
        config.write_text(f"listener {self.port} 127.0.0.1\nallow_anonymous false\npassword_file {self.passwd}\n"
                          f"acl_file {self.acl}\npersistence false\n")
        self.process = subprocess.Popen(["mosquitto", "-c", str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 10
        while time.time() < deadline:
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", self.port)) == 0:
                    return
            time.sleep(0.1)
        raise SystemExit("the smoke's broker did not start")

    def reload(self) -> None:
        self.write()
        self.process.send_signal(signal.SIGHUP)
        time.sleep(0.5)

    def stop(self) -> None:
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


class Keeper:
    """The keeper's bus users API (KEEP-PROV-0006..0008), as much as Mesh uses."""
    broker = None
    requests = []
    network = ""


class KeeperHandler(http.server.BaseHTTPRequestHandler):
    def answer(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self) -> bool:
        return self.headers.get("Authorization", "") == f"Bus mesh:{BUS_PASSWORD}"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        Keeper.requests.append(("POST", self.path, body, self.headers.get("Authorization", ""),
                                self.headers.get("X-Forwarded-For", "")))
        if self.path != "/api/v1/bus/users":
            return self.answer(404, {"ok": False, "error": "no such route"})
        if not self.authorized():
            return self.answer(401, {"ok": False, "error": "a bus credential is needed"})
        name, prefix = body.get("name", ""), body.get("prefix", "")
        if not prefix.startswith("zigbee/") or "+" in prefix or "#" in prefix or "." in name:
            return self.answer(400, {"ok": False, "error": "bad name or prefix"})
        user = f"mesh-{name}"
        password = Keeper.broker.issued.get(user, ("issued-" + name, ""))[0]
        Keeper.broker.issued[user] = (password, prefix)
        Keeper.broker.reload()
        self.answer(200, {"user": user, "password": password, "prefix": prefix, "port": str(Keeper.broker.port),
                          "loopback": "127.0.0.1", "network": Keeper.network})

    def do_DELETE(self):
        Keeper.requests.append(("DELETE", self.path, {}, self.headers.get("Authorization", ""), ""))
        if not self.authorized():
            return self.answer(401, {"ok": False, "error": "a bus credential is needed"})
        name = self.path.rsplit("/", 1)[-1]
        user = f"mesh-{name}"
        if user not in Keeper.broker.issued:
            return self.answer(404, {"ok": False, "error": "no such user"})
        del Keeper.broker.issued[user]
        Keeper.broker.reload()
        self.answer(200, {"removed": user})

    def log_message(self, *args):
        pass


class Watcher:
    """Everything on the broker under zigbee/, as the smoke sees it."""

    def __init__(self, port: int):
        self.lines = []
        self.process = subprocess.Popen(["mosquitto_sub", "-h", "127.0.0.1", "-p", str(port), "-u", "watcher", "-P", BUS_PASSWORD,
                                         "-v", "-t", "zigbee/#"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        threading.Thread(target=self.read, daemon=True).start()
        time.sleep(0.3)

    def read(self) -> None:
        for line in self.process.stdout:
            self.lines.append(line.rstrip("\n"))

    def seen(self, topic: str, needle: str = "") -> bool:
        return any(l.startswith(topic + " ") and needle in l for l in self.lines)

    def stop(self) -> None:
        self.process.terminate()


def catalog(root: Path) -> Path:
    """An orchestrator root whose registry carries robohub-devices with the
    ZBDongle-E and the S60ZBTPF from the devices checkout, and the stand-in
    robohub-zigbee2mqtt."""
    robohub = root / "robohub"
    devices = root / "devices"
    (robohub / "registry" / "modules").mkdir(parents=True)
    for kind, name in (("coordinator", "sonoff-zbdongle-e"), ("plug", "sonoff-s60zbtpf")):
        target = devices / "devices" / kind / name
        target.mkdir(parents=True)
        shutil.copy(PROJECTS / "robohub-devices" / "devices" / kind / name / "module.yml", target / "module.yml")
    (devices / "module.yml").write_text("module:\n  name: robohub-devices\n  type: collection\n  version: 0.0.0\n"
                                        "devices:\n  - devices/coordinator/sonoff-zbdongle-e\n  - devices/plug/sonoff-s60zbtpf\n")
    (robohub / "registry" / "modules" / "robohub-devices.yml").write_text(f"name: robohub-devices\nrole: collection\npath: {devices}\n")
    plugin = root / "zigbee2mqtt-plugin"
    shutil.copytree(FIXTURE, plugin, ignore=shutil.ignore_patterns("fake_z2m.py", "__pycache__"))
    (robohub / "registry" / "modules" / "robohub-zigbee2mqtt.yml").write_text(f"name: robohub-zigbee2mqtt\nrole: plugin\npath: {plugin}\n")
    return robohub


def toolchains(home: Path) -> Path:
    """A fake Node.js 24 where `rho keeper sync` would fetch it, and the pinned
    Zigbee2MQTT installed, whose index.js the fake node runs as the stand-in."""
    node = home / ".rho" / "toolchains" / "node-22.0" / "bin" / "node"
    node.parent.mkdir(parents=True)
    # exec, so the process Mesh starts and signals is the stand-in itself, as the
    # real Zigbee2MQTT is node itself: a wrapper killed with SIGKILL would leave
    # its child running.
    node.write_text("#!/bin/bash\nif [ \"$1\" = --version ]; then echo v24.11.1; exit 0; fi\n"
                    f"exec python3 {FIXTURE / 'fake_z2m.py'} \"$@\"\n")
    node.chmod(0o755)
    install = home / ".rho" / "toolchains" / "zigbee2mqtt" / "2.14.3"
    (install / "node_modules").mkdir(parents=True)
    (install / "index.js").write_text("// the stand-in runs instead\n")
    return install


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


def call(base: str, method: str, path: str, fields: dict = None, hx: bool = True):
    data = urllib.parse.urlencode(fields).encode() if fields is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    request.add_header("Authorization", f"Bearer {TOKEN}")
    if data is not None:
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
    if hx:
        request.add_header("HX-Request", "true")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as answer:
        return answer.code, answer.read().decode("utf-8", "replace")


def as_json(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return {}


def node_ids(plan: str) -> list:
    return re.findall(r"\bid: ['\"]?([0-9a-f]{16})", plan)


def wait(test, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if test():
            return True
        time.sleep(0.5)
    return test()


def main() -> int:
    global TOKEN
    result = subprocess.run([RHO_CMD, "build", "."], cwd=MESH, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit("mesh build failed:\n" + result.stdout + result.stderr)
    binary = MESH / "build" / "mesh"
    root = Path(tempfile.mkdtemp(prefix="mesh-zigbee-"))
    broker = Broker(root)
    keeper = None
    watcher = None
    process = None
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    try:
        broker.start()
        Keeper.broker = broker
        keeper = http.server.ThreadingHTTPServer(("127.0.0.1", free_port()), KeeperHandler)
        threading.Thread(target=keeper.serve_forever, daemon=True).start()
        watcher = Watcher(broker.port)
        orchestrator = catalog(root)
        state = root / "state"
        projects = state / "projects"
        projects.mkdir(parents=True)
        home = root / "home"
        install = toolchains(home)
        TOKEN = mint_token(str(home))
        (root / "mesh-bus.secret").write_text(BUS_PASSWORD + "\n")
        env = dict(os.environ)
        env.pop("RHO_CLIENT_ID", None)
        env.pop("RHO_AUTH_URL", None)
        # Without a client secret Mesh is not run by a keeper that offers helpers:
        # Zigbee2MQTT is a process of Mesh's own (tests/helpers_smoke.py runs the
        # other way).
        env.pop("RHO_CLIENT_SECRET_FILE", None)
        env["ROBOHUB_WORKSPACE"] = str(PROJECTS)
        env["RHO_MESH_STATE_DIR"] = str(state)
        env["ROBOHUB_MESH_PROJECTS"] = str(projects)
        env.setdefault("RHO_HOME", os.path.join(os.path.expanduser("~"), ".rho"))
        env["HOME"] = str(home)
        env["RHO_HTTP_ADDR"] = f"127.0.0.1:{port}"
        env["ROBOHUB_ROOT"] = str(orchestrator)
        env["ROBOHUB_KEEPER_URL"] = f"http://127.0.0.1:{keeper.server_address[1]}"
        env["ROBOHUB_BUS_URL"] = f"127.0.0.1:{broker.port}"
        env["ROBOHUB_BUS_USER"] = "mesh"
        env["ROBOHUB_BUS_PASSWORD_FILE"] = str(root / "mesh-bus.secret")
        log_path = root / "mesh.log"
        log = open(log_path, "w")
        process = subprocess.Popen([str(binary)], cwd=MESH, env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
        wait_for(base)
        settings = call(base, "GET", "/settings?tab=alerts", hx=False)[1]
        found = re.search(r'name="csrf" value="([^"]*)"', settings)
        csrf = found.group(1) if found else ""

        call(base, "POST", "/mesh/project/new", {"name": "Home"}, hx=False)
        home_dir = projects / "Home"
        plan_path = home_dir / "plan.yml"

        # --- Placing a coordinator ---------------------------------------
        # @verifies MESH-ZB-0001, MESH-ZB-0002, MESH-ZB-0003
        _, form = call(base, "GET", "/mesh/p/Home/node/new?kind=device&parent=gateway")
        check("kind=coordinator" in form, "adding a device under the native gateway offers a coordinator")
        _, form = call(base, "GET", "/mesh/p/Home/node/new?kind=coordinator&parent=gateway")
        check("ZBDongle-E" in form and "serial_port_typed" in form and 'name="channel"' in form and 'value="15"' in form,
              "the coordinator's form asks which coordinator, its port and its channel (15)")
        coordinator = {"tab": "new", "kind": "coordinator", "parent": "gateway", "device": "robohub-device-sonoff-zbdongle-e",
                       "name": "zb1", "serial_port_typed": "/dev/does-not-exist", "channel": "15"}
        status, answer = call(base, "POST", "/mesh/p/Home/node", dict(coordinator, serial_port_typed="/dev/tty;reboot"))
        check("zb1" not in plan_path.read_text() and "path under /dev/" in answer, "a port that is not a device path is refused")
        status, answer = call(base, "POST", "/mesh/p/Home/node", dict(coordinator, name='zb"1'))
        check('zb"1' not in plan_path.read_text() and "quotes" in answer, "a name with a quote is refused, saying why")
        status, answer = call(base, "POST", "/mesh/p/Home/node", coordinator)
        plan = plan_path.read_text()
        check(status == 200 and "robohub-device-sonoff-zbdongle-e" in plan and "/dev/does-not-exist" in plan and "channel" in plan,
              f"a coordinator is placed under the gateway with its port and channel ({status})")
        coordinator_id = node_ids(plan)[0] if node_ids(plan) else "none"
        status, answer = call(base, "POST", "/mesh/p/Home/node", dict(coordinator, name="zb2"))
        check("zb2" not in plan_path.read_text() and "has a coordinator already" in answer, "a second coordinator on one board is refused")
        _, devices = call(base, "GET", "/mesh/api/devices", hx=False)
        check(as_json(devices) == [], f"a coordinator is not a device that is read or switched ({devices[:200]})")

        # --- Starting it ------------------------------------------------
        # @verifies MESH-ZB-0003, MESH-ZB-0008
        tab = f"/mesh/p/Home/node/{coordinator_id}?tab=zigbee"
        _, page = call(base, "GET", tab)
        check("/dev/does-not-exist does not exist" in page, "the coordinator's page says its port does not exist")
        check("Bridge not heard" in page, "and that its bridge has said nothing yet")
        action = f"/mesh/p/Home/node/{coordinator_id}/zigbee"
        status, _ = call(base, "POST", action, {"action": "start"})
        check(status == 403 and not Keeper.requests, f"a form without Mesh's token is refused ({status})")
        status, answer = call(base, "POST", action, {"action": "start", "csrf": csrf})
        check("does not exist" in answer and not Keeper.requests, "a start with a port that does not exist is refused, said in words")
        status, _ = call(base, "POST", f"/mesh/p/Home/node/{coordinator_id}",
                         {"tab": "node", "name": "zb1", "device": "robohub-device-sonoff-zbdongle-e", "serial_port_typed": "/dev/null",
                          "channel": "20", "parent": "gateway"})
        check("/dev/null" in plan_path.read_text() and "channel: \"20\"" in plan_path.read_text() or "channel: '20'" in plan_path.read_text()
              or "channel: 20" in plan_path.read_text(), "its port and channel may change before the first start")

        # @verifies MESH-ZB-0004, MESH-ZB-0005, MESH-ZB-0006
        status, answer = call(base, "POST", action, {"action": "start", "csrf": csrf})
        check("Zigbee2MQTT started" in answer, "the coordinator starts" + ("" if "started" in answer else " " + answer[-600:]))
        posts = [r for r in Keeper.requests if r[0] == "POST"]
        check(len(posts) == 1 and posts[0][2].get("prefix") == "zigbee/Home/zb1" and posts[0][3] == f"Bus mesh:{BUS_PASSWORD}"
              and posts[0][2].get("name", "").startswith("z2m-") and posts[0][4] == "",
              f"Mesh asked the keeper for a broker user confined to zigbee/Home/zb1, with its own credential ({posts})")
        data = home_dir / "zigbee" / "zb1"
        config = (data / "configuration.yaml").read_text() if (data / "configuration.yaml").exists() else ""
        secret_text = (data / "secret.yaml").read_text() if (data / "secret.yaml").exists() else ""
        issued_user = f"mesh-{posts[0][2].get('name')}" if posts else ""
        issued_password = broker.issued.get(issued_user, ("?", ""))[0]
        check('base_topic: "zigbee/Home/zb1"' in config and f'user: "{issued_user}"' in config and 'port: "/dev/null"' in config
              and "adapter: ember" in config and "channel: 20" in config and "!secret password" in config
              and "!secret network_key" in config and "pan_id: 6754" in config,
              "the configuration is the plugin's template filled for the coordinator")
        check(issued_password not in config and issued_password in secret_text and BUS_PASSWORD not in config + secret_text,
              "the issued password is only in the secret file, and Mesh's own is nowhere")
        check(all(stat.S_IMODE(os.stat(p).st_mode) == 0o600 for p in (data / "configuration.yaml", data / "secret.yaml")) and
              stat.S_IMODE(os.stat(data).st_mode) == 0o700, "the data folder and its files are owner-only")
        pid_file = data / "mesh-z2m.pid"
        pid = int(pid_file.read_text().strip()) if pid_file.exists() else 0
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode() if pid and Path(f"/proc/{pid}").exists() else ""
        check(str(install / "index.js") in cmdline and issued_password not in cmdline, f"Zigbee2MQTT runs from the pinned install ({cmdline})")
        environ = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0") if cmdline else []
        check(b"Z2M_ONBOARD_NO_SERVER=1" in environ and f"ZIGBEE2MQTT_DATA={data}".encode() in environ,
              "with its data folder and the onboarding page off")

        # @verifies MESH-ZB-0009
        check(wait(lambda: "Bridge online" in call(base, "GET", tab)[1]), "the bridge says it is online")
        _, page = call(base, "GET", tab)
        check("2.14.3" in page and "EmberZNet 7.4.4.0 [GA]" in page, "with Zigbee2MQTT's version and the firmware")

        # A restart sets Mesh's keys again and keeps the network.
        # @verifies MESH-ZB-0005
        wait(lambda: "- 13" in (data / "secret.yaml").read_text())
        call(base, "POST", action, {"action": "start", "csrf": csrf})
        secret_text = (data / "secret.yaml").read_text()
        config = (data / "configuration.yaml").read_text()
        check("- 1\n  - 3" in secret_text and "pan_id: 6754" in config and "- 221" in config and issued_password in secret_text,
              "a restart keeps the network key and PAN ids Zigbee2MQTT made")
        check(len([r for r in Keeper.requests if r[0] == "POST"]) == 2 and len(broker.issued) == 1, "and asks the keeper again for the same user")
        check(wait(lambda: "Bridge online" in call(base, "GET", tab)[1]), "the bridge is online again")

        # @verifies MESH-ZB-0004, MESH-ZB-0006
        # A machine's boot: Mesh starts before the keeper answers. The coordinator
        # it had started still starts, with the broker user it was issued before.
        process.terminate()
        process.wait(timeout=20)
        pid_file = data / "mesh-z2m.pid"
        old_pid = pid_file.read_text().strip() if pid_file.exists() else ""
        if old_pid:
            subprocess.run(["kill", old_pid], check=False)
            wait(lambda: not Path(f"/proc/{old_pid}").exists(), 20)
        asked_before = len([r for r in Keeper.requests if r[0] == "POST"])
        down_env = dict(env, ROBOHUB_KEEPER_URL=f"http://127.0.0.1:{free_port()}")
        process = subprocess.Popen([str(binary)], cwd=MESH, env=down_env, stdout=log, stderr=subprocess.STDOUT, text=True)
        wait_for(base)
        new_pid = lambda: pid_file.read_text().strip() if pid_file.exists() else ""
        check(wait(lambda: new_pid() not in ("", old_pid) and Path(f"/proc/{new_pid()}").exists(), 20) and
              len([r for r in Keeper.requests if r[0] == "POST"]) == asked_before and
              "issued before" in log_path.read_text(),
              "with the keeper not up yet, Mesh starts the coordinator with the broker user it kept")
        check(wait(lambda: "Bridge online" in call(base, "GET", tab)[1]), "and its bridge comes online")
        process.terminate()
        process.wait(timeout=20)
        process = subprocess.Popen([str(binary)], cwd=MESH, env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
        wait_for(base)
        found = re.search(r'name="csrf" value="([^"]*)"', call(base, "GET", "/settings?tab=alerts", hx=False)[1])
        csrf = found.group(1) if found else csrf
        status, _ = call(base, "POST", f"/mesh/p/Home/node/{coordinator_id}",
                         {"tab": "node", "name": "zb1", "device": "robohub-device-sonoff-zbdongle-e", "serial_port_typed": "/dev/null",
                          "channel": "25", "parent": "gateway"})
        check("25" not in re.findall(r"channel: ['\"]?(\d+)", plan_path.read_text()), "the channel no longer changes once it started")

        # --- Pairing ----------------------------------------------------
        # @verifies MESH-ZB-0010
        status, answer = call(base, "POST", action, {"action": "permit", "time": "300", "csrf": csrf})
        check("never without a bound" in answer and not watcher.seen("zigbee/Home/zb1/bridge/request/permit_join"),
              "joining for more than 254 s is refused and nothing is sent")
        call(base, "POST", action, {"action": "permit", "time": "60", "csrf": csrf})
        check(wait(lambda: watcher.seen("zigbee/Home/zb1/bridge/request/permit_join", '{"time":60}')), "joining is asked for 60 s")
        check(wait(lambda: "Joining is open for" in call(base, "GET", tab)[1]), "and the time left is shown")
        call(base, "POST", action, {"action": "permit", "time": "0", "csrf": csrf})
        check(wait(lambda: watcher.seen("zigbee/Home/zb1/bridge/request/permit_join", '{"time":0}')), "and it is closed early with 0")

        # @verifies MESH-ZB-0011
        _, page = call(base, "GET", tab)
        check("0xa4c1380000000002" in page and "SONOFF S60ZBTPF" in page and "Adopt" in page, "the joined plug is listed and matched")
        check("0xa4c1380000000003" in page and "No device manifest reads this model" in page, "an unknown device is listed, not adoptable")
        check("0x00124b0000000001" not in page, "the coordinator itself is not listed")

        # @verifies MESH-ZB-0012, MESH-ZB-0015
        status, answer = call(base, "POST", action, {"action": "adopt", "ieee": "0xa4c1380000000003", "name": "mystery", "csrf": csrf})
        check("mystery" not in plan_path.read_text(), "a device no manifest reads is not adopted")
        status, answer = call(base, "POST", action, {"action": "adopt", "ieee": "0xa4c1380000000002", "name": 'He"ater', "csrf": csrf})
        check('He"ater' not in plan_path.read_text() and 'id="ins-notice"' in answer and "quotes" in answer,
              "a refused adopt says why, in the notice the form scrolls to")
        check('show:#ins-notice:top' in call(base, "GET", tab)[1], "the tab's forms scroll their answer into view")
        status, answer = call(base, "POST", action, {"action": "adopt", "ieee": "0xa4c1380000000002", "name": "Heater z", "csrf": csrf})
        plan = plan_path.read_text()
        check('name: "Heater z"' in plan and 'address: "heater-z"' in plan and "0xa4c1380000000002" in plan and "zigbee" in plan,
              "adopting adds a Zigbee device node: the name as given, its address made from it")
        check(wait(lambda: watcher.seen("zigbee/Home/zb1/bridge/request/device/rename", '"to":"heater-z"')),
              "and asks Zigbee2MQTT to rename it to its address")
        heater_id = [i for i in node_ids(plan) if i != coordinator_id][0]
        check(wait(lambda: "Not renamed yet" not in call(base, "GET", f"/mesh/p/Home/node/{heater_id}")[1] and
                   "zigbee_rename" not in re.sub(r"zigbee_rename: ['\"]{2}", "", plan_path.read_text())),
              "the node is renamed once the bridge says ok for its transaction")

        # --- Reading and switching ---------------------------------------
        # @verifies MESH-ZB-0014, MESH-0034, MESH-0035, MESH-0022
        listed = as_json(call(base, "GET", "/mesh/api/devices", hx=False)[1]) or []
        heater = next((d for d in listed if d.get("name") == "Heater z"), {})
        check(len(listed) == 1 and heater.get("transport") == "zigbee", f"the plug is a device in the list, the coordinator is not ({listed})")
        index = heater.get("id", 2)
        call(base, "POST", "/mesh/device/poll", {"id": str(index)}, hx=False)

        def entry():
            return next((d for d in as_json(call(base, "GET", "/mesh/api/devices", hx=False)[1]) or [] if d.get("id") == index), {})
        check(wait(lambda: entry().get("reachability") == "online"), f"its availability {{\"state\":\"online\"}} is online ({entry()})")
        status, body = call(base, "POST", f"/mesh/api/devices/{index}/switch", {"state": "on"}, hx=False)
        check(wait(lambda: watcher.seen("zigbee/Home/zb1/heater-z/set", '{"state":"ON"}')), "the switch is published as {\"state\":\"ON\"}")
        check(wait(lambda: entry().get("state") == "on"), f"and confirmed from the state the plug reports ({status} {body} {entry()})")
        _, sources = call(base, "GET", "/mesh/api/projects/Home/sources", hx=False)
        power = next((e.get("value_path") for e in as_json(sources) or [] if e.get("field") == "power_w"), "")
        check(power and wait(lambda: call(base, "GET", power, hx=False)[1].strip() == "12.5"), "its power is read under the base topic")

        # --- Carrying it --------------------------------------------------
        # @verifies MESH-ZB-0016
        _, unsealed = call(base, "GET", "/mesh/api/projects/Home/export", hx=False)
        check("zigbee_network" in unsealed and "the Zigbee network of coordinator zb1" in unsealed and "6754" not in unsealed,
              "an unsealed export names the network as left out, never its values")
        check('schema: "2"' in unsealed and "0xa4c1380000000002" in unsealed and "serial_port" in unsealed, "and carries the nodes as schema 2")
        status, sealed = call(base, "POST", "/mesh/api/projects/Home/export", {"passphrase": PASSPHRASE}, hx=False)
        check(status == 200 and "sealed:" in sealed and "zigbee_network" not in sealed and "6754" not in sealed,
              "a sealed export carries the network inside its sealed block")
        # @verifies MESH-0036
        # The Data tab has one switch button offering the other state, and it
        # switches in place: the answer is the inspector on the Data tab.
        data_url = f"/mesh/p/Home/node/{heater_id}?tab=data"
        _, data_page = call(base, "GET", data_url)
        buttons = data_page.count(">Switch on<") + data_page.count(">Switch off<")
        check(buttons == 1 and f"/node/{heater_id}/device" in data_page and 'hx-post' in data_page,
              f"the Data tab has one switch button, posted in place ({buttons})")
        offered = "on" if ">Switch on<" in data_page else "off"
        status, answer = call(base, "POST", f"/mesh/p/Home/node/{heater_id}/device", {"action": offered, "csrf": csrf})
        check(status == 200 and "agora-facts" in answer and "Location" not in answer,
              f"switching from the Data tab answers the Data tab, not a new page ({status})")
        expected = "Switch off" if offered == "on" else "Switch on"
        check(wait(lambda: f">{expected}<" in call(base, "GET", data_url)[1], 10),
              f"once the plug reports it, the one button offers {expected}")

        # --- Removing -----------------------------------------------------
        status, answer = call(base, "POST", f"/mesh/p/Home/node/{coordinator_id}/delete", {"action": "delete"})
        check(coordinator_id in plan_path.read_text() and "Zigbee devices first" in answer,
              "a coordinator is not removed while Zigbee devices hang under it")
        # @verifies MESH-ZB-0013
        status, answer = call(base, "POST", f"/mesh/p/Home/node/{heater_id}/delete", {"action": "delete"})
        # Mesh waits a few seconds for Zigbee2MQTT's answer and does not hold the
        # request longer: "asked, no answer yet" is as right as a confirmation.
        removed_text = re.sub(r"<[^>]+>", " ", answer)
        check(wait(lambda: watcher.seen("zigbee/Home/zb1/bridge/request/device/remove", '"id":"0xa4c1380000000002"'))
              and heater_id not in plan_path.read_text() and "offline" not in removed_text and "could not" not in removed_text,
              "removing the plug asks Zigbee2MQTT to remove it from the network and leaves the plan " + removed_text[:200])
        # @verifies MESH-ZB-0004, MESH-ZB-0006
        status, answer = call(base, "POST", f"/mesh/p/Home/node/{coordinator_id}/delete", {"action": "delete"})
        deletes = [r for r in Keeper.requests if r[0] == "DELETE"]
        check(deletes and deletes[0][1] == f"/api/v1/bus/users/{posts[0][2].get('name')}" and not broker.issued,
              f"removing the coordinator removes its broker user ({deletes})")
        check(wait(lambda: not Path(f"/proc/{pid}").exists(), 10), "and stops its Zigbee2MQTT")
        check((data / "configuration.yaml").exists() and (data / "secret.yaml").exists(), "and keeps its data folder")
        # The mesh goes; its sealed export is imported, here, as a new mesh.
        call(base, "POST", "/mesh/project/delete", {"project": "Home"}, hx=False)
        check(not home_dir.exists(), "the mesh is deleted")
        # @verifies MESH-ZB-0016
        fields = {"file": sealed, "passphrase": PASSPHRASE}
        status, preview = call(base, "POST", "/mesh/api/projects/import", fields, hx=False)
        digest = as_json(preview).get("digest", "")
        check(status == 200 and "the Zigbee network of coordinator zb1" in preview, f"the import says the network is kept ({status})")
        status, done = call(base, "POST", "/mesh/api/projects/import", dict(fields, confirm=digest), hx=False)
        twin = as_json(done).get("mesh", "")
        restore = projects / twin / "zigbee" / "zb1.restore"
        text = restore.read_text() if twin and restore.exists() else ""
        check("network_key=[1, 3, 5" in text and "pan_id=6754" in text and "backup=" in text and stat.S_IMODE(os.stat(restore).st_mode) == 0o600,
              f"and writes it, owner-only, beside the new mesh's coordinator before it first starts ({status} {done[:200]})")
        check(not (projects / twin / "zigbee" / "zb1" / "mesh-z2m.pid").exists(), "nothing is started by the import")
        twin_plan = (projects / twin / "plan.yml").read_text() if twin else ""
        twin_id = node_ids(twin_plan)[0] if node_ids(twin_plan) else "none"
        status, answer = call(base, "POST", f"/mesh/p/{twin}/node/{twin_id}/zigbee", {"action": "start", "csrf": csrf})
        restored = projects / twin / "zigbee" / "zb1"
        secret_text = (restored / "secret.yaml").read_text() if (restored / "secret.yaml").exists() else ""
        config = (restored / "configuration.yaml").read_text() if (restored / "configuration.yaml").exists() else ""
        check("network_key: [1, 3, 5, 7" in secret_text and "pan_id: 6754" in config and "ext_pan_id: [221" in config
              and (restored / "coordinator_backup.json").exists() and not restore.exists(),
              "its first start writes the network the file brought instead of GENERATE")
        call(base, "POST", f"/mesh/p/{twin}/node/{twin_id}/zigbee", {"action": "stop", "csrf": csrf})

        # @verifies MESH-ZB-0006
        # A Zigbee2MQTT that ignores SIGTERM is still stopped: killed after the
        # grace period, so it does not keep the serial port past Mesh's stop.
        (restored / "hang-on-term").write_text("")
        call(base, "POST", f"/mesh/p/{twin}/node/{twin_id}/zigbee", {"action": "start", "csrf": csrf})
        hung_pid_file = restored / "mesh-z2m.pid"
        wait(lambda: hung_pid_file.exists(), 10)
        hung_pid = hung_pid_file.read_text().strip() if hung_pid_file.exists() else ""
        check(hung_pid != "" and Path(f"/proc/{hung_pid}").exists(), "a Zigbee2MQTT that will hang on SIGTERM is running")
        call(base, "POST", f"/mesh/p/{twin}/node/{twin_id}/zigbee", {"action": "stop", "csrf": csrf})
        check(hung_pid != "" and wait(lambda: not Path(f"/proc/{hung_pid}").exists(), 30) and not hung_pid_file.exists(),
              "a Zigbee2MQTT that ignores SIGTERM is killed after the grace period and its pid file removed")
        (restored / "hang-on-term").unlink()

    finally:
        if process:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        # Whatever Mesh started is stopped with the smoke.
        for pid_file in root.glob("state/projects/*/zigbee/*/mesh-z2m.pid"):
            try:
                os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
            except (ValueError, OSError):
                pass
        if watcher:
            watcher.stop()
        if keeper:
            keeper.shutdown()
        broker.stop()
        if failures:
            print((root / "mesh.log").read_text()[-4000:] if (root / "mesh.log").exists() else "")
            for log in root.glob("state/projects/*/zigbee/*/mesh-z2m.log"):
                print(log.read_text()[-2000:])
        shutil.rmtree(root, ignore_errors=True)
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("mesh zigbee smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
