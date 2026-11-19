#!/usr/bin/env python3
"""Smoke test for who may reach what in a running Mesh.

Builds Mesh and starts it on the loopback against a temporary state directory
and home, and checks:

- the routes that are public on purpose are exactly the auth middleware's
  skip_paths, and they answer without a ticket; the guide, which is one of them,
  lists no mesh to someone nobody signed in (Mesh's own check refuses a request
  the middleware let through unauthenticated);
- every other class of route — the overview, a mesh's pages and changes, Mesh
  as a whole, settings, faults, search and the API — refuses a request with no
  ticket, and one that forges the identity headers;
- /mesh/api serves a token naming the mesh it asks about (or mesh:* / *) and
  refuses one for another mesh, one with no mesh scope, and a gateway's token
  scope (gateway:<mesh>:*), which opens none of Mesh's routes;
- started with RHO_AUTH_STANDALONE=1, Mesh answers this machine without a
  ticket and refuses a caller that is not on it;
- without a key file, Mesh fetches the keeper's public key and keeps a copy
  (0600): with the keeper down it still accepts a ticket the kept key signed and
  refuses one another key signed, and when the keeper rotates to a new key it
  picks the new key up.

    python3 tests/access_smoke.py
"""

import http.server
import json
import os
import re
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

# The routes that are public on purpose. A path added to the middleware's
# skip_paths without being added here fails the test: it has to be a decision.
PUBLIC = {"/healthz", "/auth/login", "/assets", "/assets/", "/i18n", "/auth/callback",
          "/static", "/mesh/docs", "/mesh/api/nodes/key"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def call(base: str, path: str, method: str = "GET", token: str = "", headers: dict = None,
         data: bytes = None, address: str = "") -> tuple:
    url = (address or base) + path
    request = urllib.request.Request(url, data=data, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with OPENER.open(request, timeout=20) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as answer:
        return answer.code, answer.read().decode("utf-8", "replace")


def expect(condition: bool, what: str) -> None:
    if not condition:
        raise AssertionError(what)
    print(f"[PASS] {what}")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def keygen(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    subprocess.run([RHO_CMD, "auth", "keygen", "--dir", str(directory), "--force"],
                   check=True, capture_output=True, text=True)
    return directory


def ticket(keys: Path, scopes: str, audience: str = "mesh", subject: str = "smoke") -> str:
    result = subprocess.run(
        [RHO_CMD, "auth", "ticket", "--private", str(keys / "private.pem"), "--issuer", "keeper",
         "--audience", audience, "--subject", subject, "--scopes", scopes, "--ttl", "1h"],
        check=True, capture_output=True, text=True)
    return result.stdout.strip()


class KeyKeeper(http.server.BaseHTTPRequestHandler):
    """A stand-in keeper that publishes the public key it is given, or none."""
    key = b""

    def do_GET(self):
        if self.path == "/auth/public-key" and KeyKeeper.key:
            body, status = KeyKeeper.key, 200
        else:
            body, status = b"not here\n", 404 if self.path != "/auth/public-key" else 503
        self.send_response(status)
        self.send_header("Content-Type", "application/x-pem-file")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.do_GET()

    def log_message(self, *args):
        pass


class Mesh:
    def __init__(self, root: Path, name: str, env_extra: dict, listen: str = "127.0.0.1"):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        state = root / "state"
        projects = state / "projects"
        for mesh, area in (("Sec", 5), ("Other", 6)):
            folder = projects / mesh
            if not folder.exists():
                folder.mkdir(parents=True)
                (folder / "project.txt").write_text(
                    f"project.name={mesh}\narea_id={area}\nnode.count=1\n"
                    f"node.1.name=Board {mesh}\nnode.1.parent=gateway\nnode.1.role=node\n"
                    f"node.1.id={'0123456789abcdef' if mesh == 'Sec' else 'fedcba9876543210'}\n")
        env = dict(os.environ)
        for name_ in ("RHO_CLIENT_ID", "RHO_AUTH_URL", "RHO_KEEPER_PORT", "RHO_AUTH_PUBLIC_KEY",
                      "RHO_AUTH_STANDALONE", "RHO_APP_WORKSPACE"):
            env.pop(name_, None)
        env["ROBOHUB_WORKSPACE"] = str(PROJECTS)
        env["RHO_MESH_STATE_DIR"] = str(state)
        env["ROBOHUB_MESH_PROJECTS"] = str(projects)
        env.setdefault("RHO_HOME", os.path.join(os.path.expanduser("~"), ".rho"))
        env["HOME"] = str(root / "home")
        env["RHO_HTTP_ADDR"] = f"{listen}:{self.port}"
        env.update(env_extra)
        self.log_path = root / f"mesh-{name}.log"
        self.log = open(self.log_path, "w")
        self.process = subprocess.Popen([str(MESH / "build" / "mesh")], cwd=MESH, env=env,
                                        stdout=self.log, stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(self.base + "/healthz", timeout=1) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.2)
        self.stop()
        raise SystemExit(f"mesh did not start:\n{self.log_path.read_text()}")

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.log.close()


def skip_paths() -> set:
    text = (MESH / "project.yml").read_text()
    match = re.search(r'skip_paths:\s*"([^"]*)"', text)
    return {path.strip() for path in match.group(1).split(",") if path.strip()} if match else set()


def refused(status: int) -> bool:
    # A browser is sent to the keeper's login (302), an API caller is told 401,
    # and a ticket without the grant gets 403.
    return status in (302, 401, 403)


def check_access(root: Path) -> None:
    home = root / "home"
    keys = keygen(home / ".rho" / "auth")
    mesh = Mesh(root, "access", {})
    base = mesh.base
    try:
        expect(skip_paths() == PUBLIC, "the public routes are exactly the middleware's skip_paths")
        status, _ = call(base, "/healthz")
        expect(status == 200, "/healthz answers without a ticket")
        status, body = call(base, "/mesh/docs")
        expect(status == 200, "the guide answers without a ticket")
        expect("/mesh/project/Sec" not in body and "/mesh/p/Sec" not in body and "Board Sec" not in body,
               "the guide lists no mesh to someone nobody signed in")
        status, _ = call(base, "/mesh/api/nodes/key", method="POST", data=b"")
        expect(status == 401, "a host node's key asks for the node's own credential, not a ticket")

        protected = [
            ("GET", "/mesh"), ("GET", "/"), ("GET", "/mesh/fleet"), ("GET", "/mesh/p/Sec"),
            ("GET", "/mesh/project/Sec"), ("GET", "/mesh/project/Sec/settings"),
            ("POST", "/mesh/p/Sec/node/0123456789abcdef/delete"), ("POST", "/mesh/project/new"),
            ("POST", "/mesh/apply-all"), ("GET", "/settings"), ("POST", "/settings"),
            ("GET", "/faults"), ("GET", "/mesh/search?q=Board"), ("GET", "/mesh/agora/events?project=Sec"),
            ("GET", "/mesh/api/projects"), ("GET", "/mesh/api/projects/Sec/sources"),
            ("GET", "/mesh/api/projects/Sec/telemetry/status"), ("GET", "/mesh/api/devices"),
            ("GET", "/mesh/api/gateway"), ("GET", "/mesh/api/clients"),
            ("POST", "/mesh/api/clients/register"), ("POST", "/mesh/project/Sec/token"),
        ]
        forged = {"X-Rho-Aud": "mesh", "X-Rho-Scopes": "*", "X-Rho-Subject": "intruder"}
        for method, path in protected:
            data = b"name=x" if method == "POST" else None
            status, _ = call(base, path, method=method, data=data)
            expect(refused(status), f"{method} {path} refuses a request with no ticket ({status})")
            status, _ = call(base, path, method=method, data=data, headers=forged)
            expect(refused(status), f"{method} {path} refuses forged identity headers ({status})")

        everything = ticket(keys, "*")
        sec = ticket(keys, "mesh:Sec")
        other = ticket(keys, "mesh:Other")
        no_mesh = ticket(keys, "energy:read,aquarium:*")
        gateway = ticket(keys, "gateway:Sec:*")
        foreign = ticket(keys, "*", audience="energy")

        api = "/mesh/api/projects/Sec/sources"
        expect(call(base, api, token=everything)[0] == 200, "/mesh/api serves a * token")
        expect(call(base, api, token=sec)[0] == 200, "/mesh/api serves a token for the mesh it asks about")
        expect(call(base, api, token=other)[0] == 403, "/mesh/api refuses a token for another mesh")
        expect(call(base, api, token=no_mesh)[0] == 403, "/mesh/api refuses a token with no mesh scope")
        expect(call(base, "/mesh/api/projects/Sec/telemetry/status", token=other)[0] == 403,
               "the telemetry API refuses a token for another mesh")
        expect(call(base, api, token=foreign)[0] == 401, "a ticket issued for another application is refused")
        status, body = call(base, "/mesh/api/projects", token=sec)
        expect(status == 200 and '"Sec"' in body and '"Other"' not in body,
               "the mesh list shows a token its own mesh alone")

        for method, path in [("GET", api), ("GET", "/mesh/api/projects/Sec/telemetry/status"),
                             ("GET", "/mesh/p/Sec"), ("GET", "/mesh/project/Sec/settings"),
                             ("POST", "/mesh/p/Sec/node/0123456789abcdef/delete"),
                             ("GET", "/settings"), ("GET", "/mesh/api/gateway")]:
            data = b"" if method == "POST" else None
            status, _ = call(base, path, method=method, data=data, token=gateway,
                             headers={"HX-Request": "true"} if method == "POST" else None)
            expect(status == 403, f"a gateway token (gateway:Sec:*) is refused at {method} {path}")
        status, body = call(base, "/mesh/search?q=Board", token=sec)
        expect(status == 200 and "Board Sec" in body and "Board Other" not in body,
               "search finds only in the meshes the person may read")
        status, body = call(base, "/mesh/search?q=Board", token=gateway)
        expect("Board Sec" not in body, "search finds nothing for a gateway token")
    finally:
        mesh.stop()


def lan_address() -> str:
    """An address of this machine that is not the loopback, or ""."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))  # no packet is sent for a UDP connect
            address = sock.getsockname()[0]
            return "" if address.startswith("127.") else address
    except OSError:
        return ""


def check_standalone(root: Path) -> None:
    address = lan_address()
    # Bound to the loopback and, when the machine has one, its own LAN address,
    # so a caller from that address is a caller that is not the loopback.
    mesh = Mesh(root, "standalone", {"RHO_AUTH_STANDALONE": "1"}, listen="0.0.0.0" if address else "127.0.0.1")
    try:
        status, _ = call(mesh.base, "/mesh/api/projects/Sec/sources")
        expect(status == 200, "standalone, this machine is served without a ticket")
        status, _ = call(mesh.base, "/mesh/p/Sec")
        expect(status == 200, "standalone, this machine opens a mesh")
        status, body = call(mesh.base, "/mesh/api/projects/Sec/sources", headers={"X-Forwarded-For": "203.0.113.9"})
        expect(status == 403 and "standalone" in body, "standalone, a caller forwarded from elsewhere is refused")
        if address:
            status, body = call(mesh.base, "/mesh/api/projects/Sec/sources", address=f"http://{address}:{mesh.port}")
            expect(status == 403, f"standalone, a caller at {address} (not the loopback) is refused")
            status, _ = call(mesh.base, "/mesh/p/Sec", address=f"http://{address}:{mesh.port}")
            expect(status == 403, "standalone, a remote caller does not open a mesh")
        else:
            print("[SKIP] this machine has no address but the loopback to call from")
    finally:
        mesh.stop()


def check_key_copy(root: Path) -> None:
    first = keygen(root / "keys-first")
    second = keygen(root / "keys-second")
    keeper_port = free_port()
    keeper = http.server.ThreadingHTTPServer(("127.0.0.1", keeper_port), KeyKeeper)
    threading.Thread(target=keeper.serve_forever, daemon=True).start()
    env = {"RHO_AUTH_URL": f"http://127.0.0.1:{keeper_port}"}
    kept = root / "home" / ".rho" / "auth-app" / "mesh" / "issuer.pem"
    old_key = (first / "public.pem").read_bytes()
    new_key = (second / "public.pem").read_bytes()
    api = "/mesh/api/projects/Sec/sources"
    try:
        expect(not (root / "home" / ".rho" / "auth" / "public.pem").exists(), "the machine has no key file")
        KeyKeeper.key = old_key
        mesh = Mesh(root, "key-fetch", env)
        try:
            expect(call(mesh.base, api, token=ticket(first, "*"))[0] == 200,
                   "the keeper's published key verifies its ticket")
        finally:
            mesh.stop()
        expect(kept.exists() and kept.read_bytes() == old_key, "a copy of the keeper's key is kept")
        expect(stat.S_IMODE(kept.stat().st_mode) == 0o600, "the kept copy is 0600")

        KeyKeeper.key = b""
        mesh = Mesh(root, "key-kept", env)
        try:
            expect(call(mesh.base, api, token=ticket(first, "*"))[0] == 200,
                   "with the keeper unreachable, the kept copy verifies a ticket")
            expect(call(mesh.base, api, token=ticket(second, "*"))[0] == 401,
                   "a ticket signed by another key is refused")
        finally:
            mesh.stop()

        KeyKeeper.key = old_key
        mesh = Mesh(root, "key-rotate", env)
        try:
            expect(call(mesh.base, api, token=ticket(first, "*"))[0] == 200, "before the rotation, the old key")
            KeyKeeper.key = new_key
            expect(call(mesh.base, api, token=ticket(second, "*"))[0] == 200,
                   "a ticket naming the keeper's new key is accepted once the keeper rotates")
            expect(call(mesh.base, api, token=ticket(first, "*"))[0] == 401,
                   "and the rotated-out key no longer is")
        finally:
            mesh.stop()
        expect(kept.read_bytes() == new_key, "the kept copy is the new key")
    finally:
        keeper.shutdown()


def main() -> int:
    result = subprocess.run([RHO_CMD, "build", "."], cwd=MESH, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit("mesh build failed:\n" + result.stdout + result.stderr)
    for check in (check_access, check_standalone, check_key_copy):
        with tempfile.TemporaryDirectory(prefix="mesh-access-") as root:
            check(Path(root))
    print("mesh access smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
