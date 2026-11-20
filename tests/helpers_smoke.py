#!/usr/bin/env python3
"""Smoke test for Mesh's helpers under the keeper (robohub ADR-0018, MESH-0037).

Builds Mesh and runs it as the keeper runs it — with a client secret in
RHO_CLIENT_SECRET_FILE and ROBOHUB_KEEPER_URL naming a keeper on this machine —
beside a stand-in keeper that offers the helpers API (keeper KEEP-APPS-0020) as
well as the bus users API, a broker and a Zigbee2MQTT of the smoke's own (from
tests/zigbee_smoke.py), and a stand-in robohub-gateway built by a stand-in `rho`.
The stand-in keeper runs what it is asked to as processes of its own, with only
the environment it was given, and keeps their log where the keeper would. No
serial port is opened and no systemd is touched. It checks:

- a native coordinator's Zigbee2MQTT is asked for as helper
  `z2m-<mesh>-<coordinator>`: the pinned install's `node index.js`, its folder,
  ZIGBEE2MQTT_DATA, Z2M_ONBOARD_NO_SERVER and PATH, Mesh's App credential in the
  header, no proxy header and no secret anywhere in the definition; Mesh starts
  no process of its own, and its bridge comes online;
- the tab says it runs as a keeper helper, names its unit and shows the log
  path the keeper answered;
- a restart of Mesh stops nothing and asks the keeper only for its list; a
  helper stopped behind Mesh's back is started again when Mesh starts;
- Stop is `POST …/stop`, and removing the coordinator is `DELETE` (with its
  broker user removed);
- a mesh's native gateway is started by its job as helper `gateway-<mesh>`,
  its key file passed by path through `env` and nothing named like a secret in
  its environment; it is found online, a restart of Mesh leaves it running, Stop
  goes through the keeper, and deleting the mesh removes the helper.

    python3 tests/helpers_smoke.py
"""

import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zigbee_smoke as z  # noqa: E402

CLIENT_SECRET = "smoke-client-secret-0123456789"
HELPER_PATTERNS = ("gateway-", "z2m-")
REFUSED_WORDS = ("SECRET", "PASSWORD", "TOKEN", "KEY")


class Helpers:
    """The keeper's helpers API (KEEP-APPS-0020), with processes for units."""
    definitions = {}
    processes = {}
    calls = []          # (method, path, authorization, forwarded)
    log_dir = None
    home = None
    lock = threading.Lock()

    @classmethod
    def state(cls, name: str) -> str:
        process = cls.processes.get(name)
        return "active" if process and process.poll() is None else "inactive"

    @classmethod
    def log(cls, name: str) -> str:
        return str(cls.log_dir / f"{name}.log")

    @classmethod
    def entry(cls, name: str) -> dict:
        return {"name": name, "unit": f"rh-mesh-{name}.service", "state": cls.state(name), "log": cls.log(name)}

    @classmethod
    def stop(cls, name: str) -> None:
        process = cls.processes.get(name)
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    @classmethod
    def start(cls, name: str) -> None:
        cls.stop(name)
        definition = cls.definitions[name]
        env = {"HOME": str(cls.home), "PATH": "/usr/bin:/bin"}
        env.update(definition.get("env", {}))
        log = open(cls.log(name), "a")
        cls.processes[name] = subprocess.Popen([definition["program"]] + definition.get("args", []), cwd=definition["dir"],
                                               env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                               start_new_session=True)
        log.close()

    @classmethod
    def calls_of(cls, method: str = "", path: str = "") -> list:
        return [c for c in cls.calls if (not method or c[0] == method) and (not path or c[1] == path)]


class HelperKeeper(z.KeeperHandler):
    def helpers_route(self) -> bool:
        return self.path == "/api/v1/helpers" or self.path.startswith("/api/v1/helpers/")

    def app_authorized(self) -> bool:
        return self.headers.get("Authorization", "") == f"App mesh:{CLIENT_SECRET}"

    def note(self, method: str) -> None:
        Helpers.calls.append((method, self.path, self.headers.get("Authorization", ""),
                              self.headers.get("X-Forwarded-For", "") + self.headers.get("Forwarded", "")))

    def do_GET(self):
        if not self.helpers_route():
            return self.answer(404, {"ok": False, "error": "no such route"})
        self.note("GET")
        if not self.app_authorized():
            return self.answer(401, {"error": "an App credential is needed"})
        with Helpers.lock:
            self.answer(200, {"helpers": [Helpers.entry(n) for n in sorted(Helpers.definitions)]})

    def do_POST(self):
        if not self.helpers_route():
            return super().do_POST()
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) or b"{}"
        self.note("POST")
        Helpers.calls[-1] = Helpers.calls[-1] + (raw.decode(),)
        if not self.app_authorized():
            return self.answer(401, {"error": "an App credential is needed"})
        parts = self.path.split("/")
        with Helpers.lock:
            if self.path == "/api/v1/helpers":
                body = json.loads(raw)
                name = body.get("name", "")
                if not name.startswith(HELPER_PATTERNS) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
                    return self.answer(403, {"error": "not a helper mesh may run"})
                program, folder = body.get("program", ""), body.get("dir", "")
                if not os.path.isabs(program) or not os.access(program, os.X_OK) or not os.path.isdir(folder):
                    return self.answer(400, {"error": "program or dir"})
                for key, value in body.get("env", {}).items():
                    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) or any(w in key for w in REFUSED_WORDS) or "\n" in value:
                        return self.answer(400, {"error": f"{key} is refused"})
                Helpers.definitions[name] = body
                Helpers.start(name)
                return self.answer(200, {"unit": f"rh-mesh-{name}.service", "state": Helpers.state(name), "log": Helpers.log(name)})
            if len(parts) == 6 and parts[4] in Helpers.definitions and parts[5] in ("start", "stop", "restart"):
                if parts[5] == "stop":
                    Helpers.stop(parts[4])
                else:
                    Helpers.start(parts[4])
                return self.answer(200, {"state": Helpers.state(parts[4])})
            return self.answer(404, {"error": "no such helper"})

    def do_DELETE(self):
        if not self.helpers_route():
            return super().do_DELETE()
        self.note("DELETE")
        if not self.app_authorized():
            return self.answer(401, {"error": "an App credential is needed"})
        name = self.path.rsplit("/", 1)[-1]
        with Helpers.lock:
            if name not in Helpers.definitions:
                return self.answer(404, {"error": "no such helper"})
            Helpers.stop(name)
            del Helpers.definitions[name]
            self.answer(200, {"removed": f"rh-mesh-{name}.service"})


FAKE_GATEWAY = r'''#!/usr/bin/env python3
# A stand-in robohub-gateway: it answers /healthz where RHO_HTTP_ADDR says and
# writes what it was started with beside itself.
import http.server, json, os, sys
with open(os.path.join(os.getcwd(), "started-with.json"), "w") as out:
    json.dump({"argv": sys.argv, "env": dict(os.environ), "pid": os.getpid()}, out)
port = int(os.environ.get("RHO_HTTP_ADDR", ":0").rsplit(":", 1)[1])
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path.startswith("/healthz")
        self.send_response(200 if ok else 404)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok" if ok else b"")
    def log_message(self, *args):
        pass
print("stand-in gateway listening on", port, flush=True)
http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
'''


def stand_ins(root: Path, orchestrator: Path) -> Path:
    """A robohub-gateway checkout whose `rho build .` a stand-in rho answers, and
    that rho on PATH first; every other rho command goes to the real one."""
    gateway = root / "robohub-gateway"
    (gateway / "build").mkdir(parents=True)
    (gateway / "module.yml").write_text("module:\n  name: robohub-gateway\n  type: rho\n  version: 0.0.0\n")
    (orchestrator / "registry" / "modules" / "robohub-gateway.yml").write_text(f"name: robohub-gateway\nrole: service\npath: {gateway}\n")
    bin_dir = root / "bin"
    bin_dir.mkdir()
    real = subprocess.run(["sh", "-c", f"command -v {z.RHO_CMD}"], capture_output=True, text=True).stdout.strip() or z.RHO_CMD
    rho = bin_dir / "rho"
    source = root / "fake-gateway.py"
    source.write_text(FAKE_GATEWAY)
    rho.write_text("#!/bin/sh\n"
                   f"if [ \"$1\" = build ] && [ \"$2\" = . ] && [ \"$(pwd -P)\" = {str(gateway.resolve())!r} ]; then\n"
                   f"  cp {str(source)!r} build/robohub-gateway && chmod 755 build/robohub-gateway && echo 'stand-in rho: built'; exit 0\n"
                   f"fi\nexec {real!r} \"$@\"\n")
    rho.chmod(0o755)
    return bin_dir


def alive(pid) -> bool:
    return bool(pid) and Path(f"/proc/{pid}").exists()


def main() -> int:
    result = subprocess.run([z.RHO_CMD, "build", "."], cwd=z.MESH, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit("mesh build failed:\n" + result.stdout + result.stderr)
    binary = z.MESH / "build" / "mesh"
    root = Path(tempfile.mkdtemp(prefix="mesh-helpers-"))
    broker = z.Broker(root)
    keeper = None
    process = None
    port = z.free_port()
    base = f"http://127.0.0.1:{port}"
    check = z.check
    try:
        broker.start()
        z.Keeper.broker = broker
        keeper = http.server.ThreadingHTTPServer(("127.0.0.1", z.free_port()), HelperKeeper)
        threading.Thread(target=keeper.serve_forever, daemon=True).start()
        orchestrator = z.catalog(root)
        bin_dir = stand_ins(root, orchestrator)
        state = root / "state"
        projects = state / "projects"
        projects.mkdir(parents=True)
        home = root / "home"
        install = z.toolchains(home)
        Helpers.home = home
        Helpers.log_dir = root / "keeper-workspace" / "helpers"
        Helpers.log_dir.mkdir(parents=True)
        z.TOKEN = z.mint_token(str(home))
        (root / "mesh-bus.secret").write_text(z.BUS_PASSWORD + "\n")
        secret_file = root / "client.secret"
        secret_file.write_text(CLIENT_SECRET + "\n")
        secret_file.chmod(0o600)
        env = dict(os.environ)
        env.pop("RHO_CLIENT_ID", None)
        env.pop("RHO_AUTH_URL", None)
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '/usr/bin:/bin')}"
        env["ROBOHUB_WORKSPACE"] = str(z.PROJECTS)
        env["RHO_MESH_STATE_DIR"] = str(state)
        env["ROBOHUB_MESH_PROJECTS"] = str(projects)
        env.setdefault("RHO_HOME", os.path.join(os.path.expanduser("~"), ".rho"))
        env["HOME"] = str(home)
        env["RHO_HTTP_ADDR"] = f"127.0.0.1:{port}"
        env["ROBOHUB_ROOT"] = str(orchestrator)
        env["ROBOHUB_KEEPER_URL"] = f"http://127.0.0.1:{keeper.server_address[1]}"
        env["RHO_KEEPER_PORT"] = str(keeper.server_address[1])
        env["RHO_CLIENT_SECRET_FILE"] = str(secret_file)
        env["ROBOHUB_BUS_URL"] = f"127.0.0.1:{broker.port}"
        env["ROBOHUB_BUS_USER"] = "mesh"
        env["ROBOHUB_BUS_PASSWORD_FILE"] = str(root / "mesh-bus.secret")
        log_path = root / "mesh.log"
        log = open(log_path, "w")

        def start_mesh():
            started = subprocess.Popen([str(binary)], cwd=z.MESH, env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
            z.wait_for(base)
            return started

        def restart_mesh(running):
            running.terminate()
            running.wait(timeout=20)
            mark = len(Helpers.calls)
            started = start_mesh()
            # Mesh asks the keeper about its coordinators as soon as it starts.
            z.wait(lambda: len(Helpers.calls) > mark, 20)
            time.sleep(2)
            return started, mark

        def csrf_of():
            found = re.search(r'name="csrf" value="([^"]*)"', z.call(base, "GET", "/settings?tab=alerts", hx=False)[1])
            return found.group(1) if found else ""

        process = start_mesh()
        csrf = csrf_of()
        z.call(base, "POST", "/mesh/project/new", {"name": "Home"}, hx=False)
        home_dir = projects / "Home"
        plan_path = home_dir / "plan.yml"

        # --- Zigbee2MQTT as a helper --------------------------------------
        # @verifies MESH-0037, MESH-ZB-0006
        z.call(base, "POST", "/mesh/p/Home/node", {"tab": "new", "kind": "coordinator", "parent": "gateway",
                                                    "device": "robohub-device-sonoff-zbdongle-e", "name": "zb1",
                                                    "serial_port_typed": "/dev/null", "channel": "15"})
        ids = z.node_ids(plan_path.read_text())
        coordinator_id = ids[0] if ids else "none"
        action = f"/mesh/p/Home/node/{coordinator_id}/zigbee"
        tab = f"/mesh/p/Home/node/{coordinator_id}?tab=zigbee"
        status, answer = z.call(base, "POST", action, {"action": "start", "csrf": csrf})
        check("Zigbee2MQTT started" in answer, "the coordinator starts" + ("" if "started" in answer else " " + answer[-600:]))
        posts = Helpers.calls_of("POST", "/api/v1/helpers")
        definition = json.loads(posts[0][4]) if posts else {}
        data = home_dir / "zigbee" / "zb1"
        node = home / ".rho" / "toolchains" / "node-22.0" / "bin" / "node"
        check(len(posts) == 1 and definition.get("name") == "z2m-Home-zb1" and definition.get("program") == str(node) and
              definition.get("args") == [str(install / "index.js")] and definition.get("dir") == str(install),
              f"Mesh asks the keeper to run its Zigbee2MQTT as helper z2m-Home-zb1, node index.js from the pinned install ({definition})")
        check(sorted(definition.get("env", {})) == ["PATH", "Z2M_ONBOARD_NO_SERVER", "ZIGBEE2MQTT_DATA"] and
              definition["env"]["ZIGBEE2MQTT_DATA"] == str(data) and definition["env"]["Z2M_ONBOARD_NO_SERVER"] == "1" and
              definition["env"]["PATH"].startswith(str(node.parent) + ":"),
              "with its data folder, the onboarding page off and its Node.js first on PATH, and nothing else")
        check(posts and posts[0][2] == f"App mesh:{CLIENT_SECRET}" and posts[0][3] == "",
              "as Mesh, with its client secret in an App credential and no proxy header")
        issued = [p for p, _ in broker.issued.values()]
        raw = posts[0][4] if posts else ""
        check(raw and CLIENT_SECRET not in raw and z.BUS_PASSWORD not in raw and not any(p in raw for p in issued) and
              not any(w in k for k in definition.get("env", {}) for w in REFUSED_WORDS),
              "and no secret is in the definition: the broker password stays in secret.yaml")
        check(not (data / "mesh-z2m.pid").exists() and (data / "mesh-helper").exists(), "Mesh starts no process of its own")
        helper_pid = Helpers.processes["z2m-Home-zb1"].pid if "z2m-Home-zb1" in Helpers.processes else 0
        check(alive(helper_pid), "the keeper runs it")
        check(z.wait(lambda: "Bridge online" in z.call(base, "GET", tab)[1], 20), "and its bridge comes online")
        _, page = z.call(base, "GET", tab)
        check("keeper helper" in page and "rh-mesh-z2m-Home-zb1.service" in page and Helpers.log("z2m-Home-zb1") in page and
              "Running" in page, "the tab says it runs as a keeper helper, names its unit and shows the keeper's log")

        # A restart of Mesh stops nothing and asks the keeper only for its list.
        process, mark = restart_mesh(process)
        since = Helpers.calls[mark:]
        check(alive(helper_pid) and Helpers.processes["z2m-Home-zb1"].pid == helper_pid,
              "a restart of Mesh leaves the Zigbee2MQTT helper running")
        check(since and all(c[0] == "GET" for c in since), f"and asks the keeper only for its helpers ({[c[:2] for c in since]})")

        # One stopped behind Mesh's back is started again when Mesh starts.
        Helpers.stop("z2m-Home-zb1")
        process, mark = restart_mesh(process)
        check(z.wait(lambda: Helpers.state("z2m-Home-zb1") == "active", 20) and
              any(c[0] == "POST" and c[1] == "/api/v1/helpers" for c in Helpers.calls[mark:]),
              "a helper that was stopped and is still wanted is started again when Mesh starts")
        csrf = csrf_of()

        # Stop and remove, through the keeper.
        z.call(base, "POST", action, {"action": "stop", "csrf": csrf})
        check(Helpers.calls_of("POST", "/api/v1/helpers/z2m-Home-zb1/stop") and Helpers.state("z2m-Home-zb1") == "inactive"
              and not (data / "mesh-wanted").exists(), "Stop asks the keeper to stop the helper, and Mesh no longer wants it")
        _, page = z.call(base, "GET", tab)
        check(">Start<" in page and ">Stop<" not in page, "the tab offers Start once the keeper says it is stopped")
        z.call(base, "POST", f"/mesh/p/Home/node/{coordinator_id}/delete", {"action": "delete"})
        check(Helpers.calls_of("DELETE", "/api/v1/helpers/z2m-Home-zb1") and "z2m-Home-zb1" not in Helpers.definitions
              and not broker.issued, "removing the coordinator removes its helper and its broker user")

        # --- The gateway as a helper --------------------------------------
        # @verifies MESH-0037, MESH-BUILD-0007, MESH-BUILD-0008
        rest = z.free_port()
        z.call(base, "POST", "/mesh/p/Home/gateway", {"tab": "gateway", "gateway": "native", "rest_port": str(rest),
                                                       "uplink_port": str(z.free_port())})
        z.call(base, "POST", "/mesh/p/Home/apply", {"only": "gateway"})
        jobs = home_dir / "jobs"
        z.wait(lambda: any(jobs.glob("*.done")), 120)
        exits = [p.read_text().strip() for p in jobs.glob("*.exit")]
        job_logs = "".join(p.read_text() for p in jobs.glob("*.log"))
        check(exits == ["0"], f"the gateway's job builds it and starts it ({exits}): {job_logs[-1500:]}")
        posts = [c for c in Helpers.calls_of("POST", "/api/v1/helpers") if '"gateway-Home"' in c[4]]
        definition = json.loads(posts[-1][4]) if posts else {}
        program = str((root / "robohub-gateway" / "build" / "robohub-gateway"))
        args = definition.get("args", [])
        check(definition.get("program") == "/usr/bin/env" and args[-1:] == [program] and
              any(a.startswith("GATEWAY_KEY_FILE=/") for a in args) and definition.get("dir") == str(root / "robohub-gateway"),
              f"the gateway is asked for as helper gateway-Home, its key file passed by path through env ({definition})")
        gateway_env = definition.get("env", {})
        check(gateway_env.get("REST_PORT") == str(rest) and gateway_env.get("RHO_HTTP_ADDR") == f":{rest}" and
              gateway_env.get("GATEWAY_ID") == "Home" and gateway_env.get("RHO_KEEPER_PORT") == str(keeper.server_address[1]) and
              not any(w in k for k in gateway_env for w in REFUSED_WORDS),
              "with the environment it always had, where the keeper is, and nothing named like a secret")
        check(posts and posts[-1][2] == f"App mesh:{CLIENT_SECRET}" and CLIENT_SECRET not in posts[-1][4] and
              CLIENT_SECRET not in job_logs and not any(CLIENT_SECRET in p.read_text() for p in jobs.glob("*.sh")),
              "as Mesh, the secret in no job script, log or definition")
        check((home_dir / "gateway.helper").exists() and not (home_dir / "gateway.pid").exists(),
              "no process of Mesh's own: the mark says the keeper runs it")
        gateway_pid = Helpers.processes["gateway-Home"].pid if "gateway-Home" in Helpers.processes else 0
        health = home_dir / "health.txt"
        check(z.wait(lambda: health.exists() and health.read_text().startswith("online|"), 30), "the gateway is found online")

        process, mark = restart_mesh(process)
        since = Helpers.calls[mark:]
        check(alive(gateway_pid) and Helpers.processes["gateway-Home"].pid == gateway_pid and all(c[0] == "GET" for c in since),
              f"a restart of Mesh leaves the gateway running ({[c[:2] for c in since]})")

        z.call(base, "POST", "/mesh/gateway/stop", {"return": "agora"}, hx=False)
        check(Helpers.calls_of("POST", "/api/v1/helpers/gateway-Home/stop") and Helpers.state("gateway-Home") == "inactive" and
              not (home_dir / "gateway.helper").exists(), "Stop asks the keeper to stop the gateway's helper")
        check(z.wait(lambda: health.exists() and health.read_text().startswith("stopped|"), 30),
              "and the gateway is then not started, not down")
        z.call(base, "POST", "/mesh/project/delete", {"project": "Home"}, hx=False)
        check(Helpers.calls_of("DELETE", "/api/v1/helpers/gateway-Home") and "gateway-Home" not in Helpers.definitions and
              not home_dir.exists(), "deleting the mesh removes its gateway's helper")
    finally:
        if process:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        for name in list(Helpers.processes):
            Helpers.stop(name)
        if keeper:
            keeper.shutdown()
        broker.stop()

    if z.failures:
        print(f"\n{len(z.failures)} check(s) failed")
        print((root / "mesh.log").read_text()[-4000:] if (root / "mesh.log").exists() else "")
        return 1
    print("\nhelpers smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
