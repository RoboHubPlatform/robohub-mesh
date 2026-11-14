#!/usr/bin/env python3
"""Smoke test for Mesh's device-side security.

Builds Mesh and starts it against a temporary state directory on the loopback,
with a stand-in keeper, and checks:

- Mesh's folders are 0700 and what an older Mesh left readable (a gateway key, a
  built image) is closed when it starts;
- the node manifest is written beside the gateway key, 0600, from the plan, and
  rewritten when a board is revoked and admitted again, and when its key is
  rotated — which waits for the gateway to hear the board confirm the last one;
- a host node gets its board id and key for its keeper credential, and nothing
  else: no credential, a credential the keeper refuses, a revoked board and a
  placement with no board are refused, the key is never logged, and a client
  has a budget of calls.

    python3 tests/security_smoke.py
"""

import hashlib
import hmac
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

GATEWAY_KEY = "5a" * 32
CREDENTIAL = "c0ffee" * 8
TOKEN = ""

# The stand-in keeper's records: device -> (project, area, node).
RECORDS = {
    "host-01": ("Sec", 5, 3),
    "board-01": ("Sec", 5, 1),
    "lost-01": ("Sec", 5, 9),
    "other-01": ("not-a-mesh", 5, 3),
}


class Keeper(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        device = form.get("device_id", [""])[0]
        credential = form.get("credential", [""])[0]
        record = RECORDS.get(device)
        if self.path != "/api/nodes/verify" or record is None or credential != CREDENTIAL:
            body, status = b'{"ok":false,"error":"unknown node or credential"}', 403
        else:
            body = json.dumps({"ok": True, "device_id": device, "project": record[0],
                               "area": record[1], "node": record[2]}).encode()
            status = 200
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


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
    result = subprocess.run(
        [RHO_CMD, "auth", "ticket", "--private", os.path.join(keys, "private.pem"), "--issuer", "keeper",
         "--audience", "mesh", "--subject", "smoke", "--scopes", "*", "--ttl", "1h"],
        check=True, capture_output=True, text=True)
    return result.stdout.strip()


def hx(base: str, path: str, fields: dict) -> str:
    request = urllib.request.Request(base + path, data=urllib.parse.urlencode(fields).encode(), method="POST")
    request.add_header("Authorization", f"Bearer {TOKEN}")
    request.add_header("HX-Request", "true")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def ask_key(base: str, authorization: str, client: str) -> tuple:
    request = urllib.request.Request(base + "/mesh/api/nodes/key", data=b"", method="POST")
    if authorization:
        request.add_header("Authorization", authorization)
    request.add_header("X-Forwarded-For", client)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as answer:
        body = answer.read().decode()
        try:
            return answer.code, json.loads(body)
        except ValueError:
            return answer.code, {"raw": body}


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def expect(condition: bool, what: str) -> None:
    if not condition:
        raise AssertionError(what)
    print(f"[PASS] {what}")


def manifest_line(text: str, uid: str) -> str:
    for line in text.splitlines():
        if line.startswith(uid + "|"):
            return line
    return ""


def main() -> int:
    global TOKEN
    result = subprocess.run([RHO_CMD, "build", "."], cwd=MESH, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit("mesh build failed:\n" + result.stdout + result.stderr)
    binary = MESH / "build" / "mesh"
    keeper_port = free_port()
    keeper = http.server.ThreadingHTTPServer(("127.0.0.1", keeper_port), Keeper)
    threading.Thread(target=keeper.serve_forever, daemon=True).start()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="mesh-security-") as root:
        state = Path(root) / "state"
        projects = state / "projects"
        project = projects / "Sec"
        project.mkdir(parents=True)
        os.chmod(state, 0o755)
        os.chmod(projects, 0o755)
        os.chmod(project, 0o755)
        (project / "project.txt").write_text(
            "project.name=Sec\narea_id=5\nmesh_children=Child\nnode.count=3\n"
            "node.1.name=Board\nnode.1.parent=gateway\nnode.1.role=node\nnode.1.id=0123456789abcdef\n"
            "node.2.name=Relay\nnode.2.parent=gateway\nnode.2.role=relay\nnode.2.id=1111222233334444\n"
            "node.3.name=Host\nnode.3.parent=gateway\nnode.3.role=node\nnode.3.id=aaaabbbbccccdddd\n")
        # A mesh that reports through Sec: its gateway is a relay of Sec's.
        child = projects / "Child"
        child.mkdir()
        (child / "project.txt").write_text(
            "project.name=Child\narea_id=7\nmesh_parent=Sec\nrelay_uid=9999aaaabbbbcccc\nnode.count=0\n")
        # What an older Mesh left readable: the gateway's key and a built image.
        key_file = project / "gateway.key"
        key_file.write_text(GATEWAY_KEY + "\n")
        key_file.chmod(0o644)
        image = project / "boards" / "node_1" / "build" / "output" / "node_1.elf"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"\x7fELF")
        image.chmod(0o644)
        os.chmod(project / "boards", 0o755)
        home = os.path.join(root, "home")
        TOKEN = mint_token(home)
        env = dict(os.environ)
        env.pop("RHO_CLIENT_ID", None)
        env["ROBOHUB_WORKSPACE"] = str(PROJECTS)
        env["RHO_MESH_STATE_DIR"] = str(state)
        env["ROBOHUB_MESH_PROJECTS"] = str(projects)
        env["ROBOHUB_KEEPER_URL"] = f"http://127.0.0.1:{keeper_port}"
        env.setdefault("RHO_HOME", os.path.join(os.path.expanduser("~"), ".rho"))
        env["HOME"] = home
        env["RHO_HTTP_ADDR"] = f"127.0.0.1:{port}"
        log_path = Path(root) / "mesh.log"
        log = open(log_path, "w")
        process = subprocess.Popen([str(binary)], cwd=MESH, env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
        try:
            wait_for(base)

            # Folders and files.
            urllib.request.urlopen(urllib.request.Request(base + "/mesh/fleet",
                                                          headers={"Authorization": f"Bearer {TOKEN}"}), timeout=20).read()
            for folder in (state, projects, project, project / "boards"):
                expect(mode(folder) == 0o700, f"{folder.name} is 0700")
            expect(mode(key_file) == 0o600, "a gateway key left readable is closed at start")
            expect(mode(image) == 0o600, "a built image left readable is closed at start")

            # The manifest, from the plan.
            manifest = project / "nodes.txt"
            deadline = time.time() + 10
            while time.time() < deadline and not manifest.exists():
                time.sleep(0.2)
            expect(manifest.exists(), "the node manifest is written beside the gateway key")
            expect(mode(manifest) == 0o600, "the node manifest is 0600")
            text = manifest.read_text()
            expect(manifest_line(text, "0123456789abcdef") == "0123456789abcdef|5|1|board|0|0|", "a board's line")
            expect(manifest_line(text, "1111222233334444") == "1111222233334444|5|2|relay|0|0|", "a relay's line")
            expect(manifest_line(text, "aaaabbbbccccdddd") == "aaaabbbbccccdddd|5|3|board|0|0|", "a host node's line")
            expect(manifest_line(text, "9999aaaabbbbcccc") == "9999aaaabbbbcccc|7|0|relay|0|0|7",
                   "a mesh linked below is a relay for its own area")

            # Revoking a board, and admitting it again.
            fragment = hx(base, "/mesh/p/Sec/node/0123456789abcdef/revoke", {"action": "revoke"})
            expect("revoked" in fragment, "the inspector says the board is revoked")
            expect(manifest_line(manifest.read_text(), "0123456789abcdef").split("|")[5] == "1",
                   "a revoked board is revoked in the manifest")
            expect(mode(manifest) == 0o600, "the rewritten manifest is 0600")
            page = urllib.request.urlopen(urllib.request.Request(
                base + "/mesh/p/Sec/node/0123456789abcdef?tab=revoke",
                headers={"Authorization": f"Bearer {TOKEN}", "HX-Request": "true"}), timeout=20).read().decode()
            expect("Admit it again" in page, "the revoke tab offers to admit it again")

            # The board key for a host node.
            status, answer = ask_key(base, "", "10.0.0.1")
            expect(status == 401, "a request without a node credential is refused")
            status, answer = ask_key(base, "Node host-01:" + "00" * 32, "10.0.0.2")
            expect(status == 403, "a credential the keeper refuses gets nothing")
            status, answer = ask_key(base, f"Node host-01:{CREDENTIAL}", "10.0.0.3")
            uid = "aaaabbbbccccdddd"
            want = hmac.new(bytes.fromhex(GATEWAY_KEY), b"rh-dev1" + bytes.fromhex(uid), hashlib.sha256).hexdigest()[:32]
            expect(status == 200 and answer.get("uid") == uid and answer.get("key") == want,
                   "a host node gets the id and key of the board planned at its placement")
            status, answer = ask_key(base, f"Node other-01:{CREDENTIAL}", "10.0.0.4")
            expect(status == 200 and answer.get("uid") == uid, "a placement in an unnamed mesh is found by its area")
            status, answer = ask_key(base, f"Node board-01:{CREDENTIAL}", "10.0.0.5")
            expect(status == 403 and "revoked" in answer.get("error", ""), "a revoked board gets no key")
            status, answer = ask_key(base, f"Node lost-01:{CREDENTIAL}", "10.0.0.6")
            expect(status == 404, "a placement with no board planned gets nothing")

            hx(base, "/mesh/p/Sec/node/0123456789abcdef/revoke", {"action": "restore"})
            expect(manifest_line(manifest.read_text(), "0123456789abcdef").split("|")[5] == "0",
                   "a board admitted again is admitted in the manifest")
            status, answer = ask_key(base, f"Node board-01:{CREDENTIAL}", "10.0.0.7")
            expect(status == 200 and answer.get("uid") == "0123456789abcdef", "and gets its key again")

            # Rotating a board's key: the manifest names its next epoch, and it is
            # not rotated again until its gateway has heard it confirm.
            board_uid = "0123456789abcdef"
            report = project / "rekey.txt"
            fragment = hx(base, f"/mesh/p/Sec/node/{board_uid}/rotate", {"action": "rotate"})
            expect(manifest_line(manifest.read_text(), board_uid).split("|")[4] == "1",
                   "rotating a board names its next key epoch in the manifest")
            expect("Waiting for the board to confirm" in fragment, "the inspector says the board has not confirmed yet")
            fragment = hx(base, f"/mesh/p/Sec/node/{board_uid}/rotate", {"action": "rotate"})
            expect("has not confirmed its last key yet" in fragment and
                   manifest_line(manifest.read_text(), board_uid).split("|")[4] == "1",
                   "a board whose last rotation is unconfirmed is not rotated again")
            report.write_text(f"# uid|epoch|status|time\n{board_uid}|1|confirmed|1794600000\n")
            page = urllib.request.urlopen(urllib.request.Request(
                base + f"/mesh/p/Sec/node/{board_uid}?tab=revoke",
                headers={"Authorization": f"Bearer {TOKEN}", "HX-Request": "true"}), timeout=20).read().decode()
            expect("heard the board confirm" in page and "Rotate key" in page,
                   "once the gateway says the board confirmed, the inspector says so and offers to rotate again")
            hx(base, f"/mesh/p/Sec/node/{board_uid}/rotate", {"action": "rotate"})
            expect(manifest_line(manifest.read_text(), board_uid).split("|")[4] == "2", "and it rotates to epoch 2")
            report.write_text(f"{board_uid}|2|cannot|1794600100\n")
            page = urllib.request.urlopen(urllib.request.Request(
                base + f"/mesh/p/Sec/node/{board_uid}?tab=revoke",
                headers={"Authorization": f"Bearer {TOKEN}", "HX-Request": "true"}), timeout=20).read().decode()
            expect("cannot rotate its key" in page and "Rotate key" not in page,
                   "a board that cannot store a key is shown so, and is not offered a rotation")
            fragment = hx(base, f"/mesh/p/Sec/node/{board_uid}/rotate", {"action": "rotate"})
            expect(manifest_line(manifest.read_text(), board_uid).split("|")[4] == "2",
                   "and is not rotated")

            # A host node asking for its key after a rotation gets the current
            # epoch's key, and the epoch.
            hx(base, "/mesh/p/Sec/node/aaaabbbbccccdddd/rotate", {"action": "rotate"})
            status, answer = ask_key(base, f"Node host-01:{CREDENTIAL}", "10.0.0.10")
            rotated_key = hmac.new(bytes.fromhex(GATEWAY_KEY), b"rh-dev2" + bytes.fromhex("aaaabbbbccccdddd") +
                                   (1).to_bytes(4, "little"), hashlib.sha256).hexdigest()[:32]
            expect(status == 200 and answer.get("key") == rotated_key and answer.get("epoch") == 1,
                   "a host node rotated to epoch 1 gets the epoch-1 key")

            codes = [ask_key(base, f"Node host-01:{CREDENTIAL}", "10.0.0.8")[0] for _ in range(11)]
            expect(codes[:10] == [200] * 10 and codes[10] == 429, "a client has a budget of calls")
            expect(ask_key(base, f"Node host-01:{CREDENTIAL}", "10.0.0.9")[0] == 200,
                   "another client has its own")

            log.flush()
            logged = log_path.read_text()
            expect(want not in logged and rotated_key not in logged and CREDENTIAL not in logged and GATEWAY_KEY not in logged,
                   "no key or credential is logged")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()
            keeper.shutdown()
    print("mesh security smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
