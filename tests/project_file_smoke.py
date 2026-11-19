#!/usr/bin/env python3
"""Smoke test for exporting a mesh as a project file and importing one.

Builds Mesh and runs it as several instances — each its own state folder, home
and port on the loopback, as several machines would be — and checks, against
the running application:

- an export carries the mesh's definition and no secret in clear: the gateway
  key, the relay key and every Wi-Fi password are left out and named under
  `omitted:`; two exports of an unchanged mesh differ only in `exported.at`; the
  JSON form is the same tree; both validate against schemas/mesh-project.v1.json;
  every key of the mesh's plan is one the rules account for;
- an export imported as a new mesh on another instance gives back the same
  definition, from YAML and from JSON, with its id, node numbers and ids, area and
  ports, every board never programmed, no job queued and no gateway started, a
  Wi-Fi board's build refused until its password is entered, and the gateway's
  serial device and TLS files named as warnings;
- a sealed export opens with its passphrase and keeps the gateway key, the Wi-Fi
  passwords and the record of what runs; a wrong passphrase, or the sealed block
  moved into another file, imports nothing; the passphrase is in no log or file;
- conflicts: a mesh id taken, a node id or gateway key another mesh has (refused
  with identity, accepted as a copy), an area and ports another mesh has
  (reassigned and listed), a link to a mesh that is not there (left out and
  listed), a board the catalog lacks (kept, its build refused);
- an overwrite keeps the mesh's id, area, ports, key, deployed record and tokens,
  keeps the plan it replaced as a revision, needs the mesh's name typed, never
  replaces the gateway key, names what the manifest admits and refuses, and a
  second import of the same file writes nothing;
- the preview is bound to its file (412 for another), a newer schema is refused
  naming the versions, a file over 1 MiB is 413, every problem of an invalid file
  is named with nothing written, a value of the file is never run;
- a read-only token can export but neither import nor export sealed, a cookie
  session without the form token is refused, and the pages offer it all;
- an import interrupted between staging and rename leaves nothing once Mesh
  starts again.

    python3 tests/project_file_smoke.py
"""

import hashlib
import json
import os
import re
import secrets
import shutil
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
PROJECTS = MESH.parent
RHO = PROJECTS / "rho" / "bin" / "rho"
RHO_CMD = str(RHO) if RHO.exists() else "rho"
sys.path.insert(0, str(HERE.parent))
import project_schema_test as schema_rules  # noqa: E402

failures = []
GATEWAY_KEY = secrets.token_hex(32)
OTHER_KEY = secrets.token_hex(32)
RELAY_KEY = secrets.token_hex(16)
WIFI_NODE = "Wifi-Node-Secret-" + secrets.token_hex(4)
WIFI_MESH = "Wifi-Mesh-Secret-" + secrets.token_hex(4)
PASSPHRASE = "correct horse battery " + secrets.token_hex(3)
SECRETS = (GATEWAY_KEY, RELAY_KEY, WIFI_NODE, WIFI_MESH)


def check(ok: bool, what: str) -> None:
    print(("[PASS] " if ok else "[FAIL] ") + what)
    if not ok:
        failures.append(what)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def send(base: str, method: str, path: str, fields: dict = None, bearer: str = "", cookie: str = "",
         hx: bool = False, raw: bytes = None, headers: dict = None):
    data = raw if raw is not None else (urllib.parse.urlencode(fields).encode() if fields is not None else None)
    request = urllib.request.Request(base + path, data=data, method=method)
    if bearer:
        request.add_header("Authorization", f"Bearer {bearer}")
    if cookie:
        request.add_header("Cookie", f"rho_session_mesh={cookie}")
    if data is not None:
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
    if hx:
        request.add_header("HX-Request", "true")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with OPENER.open(request, timeout=60) as response:
            return response.status, response.read().decode("utf-8", "replace"), dict(response.headers)
    except urllib.error.HTTPError as answer:
        return answer.code, answer.read().decode("utf-8", "replace"), dict(answer.headers)


def as_json(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return {}


def yaml_flat(text: str) -> dict:
    """The writer's YAML (nested maps of quoted scalars) as flat keys."""
    out, stack = {}, []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        key, _, rest = raw.strip().partition(":")
        key = key.strip().strip('"')
        while stack and stack[-1][0] >= indent:
            stack.pop()
        rest = rest.strip()
        if rest == "":
            stack.append((indent, key))
            continue
        path = ".".join([k for _, k in stack] + ([] if key == "_" else [key]))
        out[path] = json.loads(rest) if rest.startswith('"') else rest
    return out


def json_flat(tree, prefix: str = "") -> dict:
    out = {}
    for key, value in tree.items():
        path = prefix if key == "_" else (f"{prefix}.{key}" if prefix else key)
        if isinstance(value, dict):
            out.update(json_flat(value, path))
        else:
            out[path] = value
    return out


def definition(flat: dict) -> dict:
    """What an import must give back: the mesh and its plan."""
    return {k: v for k, v in flat.items() if k.startswith("plan.") or k in ("mesh.id", "mesh.name", "mesh.description")}


def plan_flat(path: Path) -> dict:
    return yaml_flat(path.read_text()) if path.exists() else {}


def key_kind(key: str, rules: dict) -> str:
    """project_key_kind, from the same lists, for the keys of a real plan."""
    def table(name):
        return dict(word.split(":", 1) for word in rules[name].split())
    node = re.match(r"^node\.([1-9][0-9]*)\.(.+)$", key)
    if node:
        field = node.group(2)
        if field in table("PROJECT_NODE_FIELDS"):
            return "definition"
        if field in rules["PROJECT_SECRET_NODE_FIELDS"].split():
            return "secret"
        if field in rules["PROJECT_RUNTIME_NODE_FIELDS"].split():
            return "runtime"
        return ""
    if key in table("PROJECT_PLAN_KEYS"):
        return "definition"
    for name, kind in (("PROJECT_SECRET_KEYS", "secret"), ("PROJECT_PLACEMENT_KEYS", "placement"),
                       ("PROJECT_IDENTITY_KEYS", "identity"), ("PROJECT_RUNTIME_KEYS", "runtime")):
        if key in rules[name].split():
            return kind
    if any(key.startswith(prefix) for prefix in rules["PROJECT_RUNTIME_PREFIXES"].split()):
        return "runtime"
    return ""


def mint_keys(home: Path) -> Path:
    keys = home / ".rho" / "auth"
    keys.mkdir(parents=True, exist_ok=True)
    subprocess.run([RHO_CMD, "auth", "keygen", "--dir", str(keys), "--force"], check=True, capture_output=True, text=True)
    return keys


def ticket(keys: Path, scopes: str, subject: str = "smoke") -> str:
    return subprocess.run(
        [RHO_CMD, "auth", "ticket", "--private", str(keys / "private.pem"), "--issuer", "keeper",
         "--audience", "mesh", "--subject", subject, "--scopes", scopes, "--ttl", "1h"],
        check=True, capture_output=True, text=True).stdout.strip()


class Mesh:
    """One Mesh, as if on its own machine: its own state, projects, home and port."""

    def __init__(self, root: Path, name: str, keys: Path, extra: dict = None):
        self.root = root / name
        self.state = self.root / "state"
        self.projects = self.state / "projects"
        self.home = self.root / "home"
        self.projects.mkdir(parents=True, exist_ok=True)
        (self.home / ".rho").mkdir(parents=True, exist_ok=True)
        if not (self.home / ".rho" / "auth").exists():
            shutil.copytree(keys, self.home / ".rho" / "auth")
        self.name = name
        self.extra = extra or {}
        self.process = None
        self.log_path = root / f"mesh-{name}.log"

    def start(self, extra: dict = None) -> "Mesh":
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        env = dict(os.environ)
        for drop in ("RHO_CLIENT_ID", "RHO_AUTH_URL", "RHO_KEEPER_PORT", "RHO_AUTH_PUBLIC_KEY", "RHO_AUTH_STANDALONE",
                     "RHO_APP_WORKSPACE", "ROBOHUB_MESH_TEST_IMPORT_HALT"):
            env.pop(drop, None)
        env["ROBOHUB_WORKSPACE"] = str(PROJECTS)
        env["RHO_MESH_STATE_DIR"] = str(self.state)
        env["ROBOHUB_MESH_PROJECTS"] = str(self.projects)
        env.setdefault("RHO_HOME", os.path.join(os.path.expanduser("~"), ".rho"))
        env["HOME"] = str(self.home)
        env["RHO_HTTP_ADDR"] = f"127.0.0.1:{self.port}"
        env.update(self.extra)
        env.update(extra or {})
        self.log = open(self.log_path, "a")
        self.process = subprocess.Popen([str(MESH / "build" / "mesh")], cwd=MESH, env=env,
                                        stdout=self.log, stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(self.base + "/healthz", timeout=1) as response:
                    if response.status == 200:
                        return self
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.2)
        raise SystemExit(f"mesh {self.name} did not start:\n{self.log_path.read_text()}")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.process:
            self.log.close()
        self.process = None

    def mesh_ids(self) -> set:
        return {p.name for p in self.projects.iterdir() if (p / "plan.yml").exists()}


def write_garden(projects: Path, port_base: int) -> None:
    """A mesh as an older Mesh wrote it (project.txt), with every kind of secret."""
    garden = projects / "garden"
    garden.mkdir(parents=True)
    (garden / "project.txt").write_text("\n".join([
        "project.name=Garden", "project.description=The back garden", "area_id=5",
        f"rest_port={port_base}", f"uplink_port={port_base + 1}",
        "gateway=native", "gateway_host=127.0.0.1", "gateway_serial=/dev/ttyUSB7",
        "gateway_tls_cert=/etc/robohub/garden-cert.pem", "gateway_tls_key=/etc/robohub/garden-key.pem",
        "telemetry_retention_days=30", "ssid=HomeNet", f"password={WIFI_MESH}", "relay_uid=0011223344556677",
        "mesh_parent=north", "gateway_token=legacy-gateway-token",
        "status.gateway=running", "message.build=Built", "current_step=build",
        "node.count=3",
        "node.1.name=Pump board", "node.1.parent=gateway", "node.1.role=node", "node.1.id=0123456789abcdef",
        "node.1.link=wifi", "node.1.ssid=GardenNet", f"node.1.password={WIFI_NODE}", "node.1.epoch=2",
        "node.1.production=off",
        "node.2.name=Plug $(touch /tmp/robohub-mesh-import-ran)", "node.2.kind=device", "node.2.parent=node.1",
        "node.2.id=fedcba9876543210", "node.2.transport=http", "node.2.address=http://127.0.0.1:9/`id`",
        "node.2.state=on", "node.2.last_value=12.5",
        "node.3.name=Shed board", "node.3.parent=gateway", "node.3.role=node", "node.3.id=1111222233334444",
        "node.3.link=uart", "node.3.baud=115200", "status.node.3=programmed",
    ]) + "\n")
    (garden / "gateway.key").write_text(GATEWAY_KEY + "\n")
    os.chmod(garden / "gateway.key", 0o600)
    (garden / "relay.key").write_text(RELAY_KEY + "\n")
    os.chmod(garden / "relay.key", 0o600)
    (garden / "tokens.jsonl").write_text('{"name":"aquarium","token":"kept-token","created":1,"expires":2}\n')
    north = projects / "north"
    north.mkdir(parents=True)
    (north / "project.txt").write_text("project.name=North\narea_id=9\nnode.count=0\n")


def export(mesh: Mesh, token: str, mesh_id: str = "garden", form: str = "") -> tuple:
    suffix = "?format=json" if form == "json" else ""
    return send(mesh.base, "GET", f"/mesh/api/projects/{mesh_id}/export{suffix}", bearer=token)


def imported(mesh: Mesh, token: str, fields: dict) -> tuple:
    status, body, _ = send(mesh.base, "POST", "/mesh/api/projects/import", fields, bearer=token)
    return status, as_json(body), body


def import_confirmed(mesh: Mesh, token: str, fields: dict) -> tuple:
    status, preview, body = imported(mesh, token, fields)
    if status != 200:
        return status, preview, body
    return imported(mesh, token, dict(fields, confirm=preview.get("digest", "")))


def problems_text(answer: dict) -> str:
    return " ".join(f"{p.get('where')}: {p.get('problem')}" for p in answer.get("problems", []))


def main() -> int:
    result = subprocess.run([RHO_CMD, "build", "."], cwd=MESH, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit("mesh build failed:\n" + result.stdout + result.stderr)
    root = Path(tempfile.mkdtemp(prefix="mesh-projectfile-"))
    meshes = []
    marker = Path("/tmp/robohub-mesh-import-ran")
    if marker.exists():
        marker.unlink()
    try:
        keys = mint_keys(root / "keys")
        admin = ticket(keys, "*")
        mesh_admin = ticket(keys, "mesh:*")
        reader = ticket(keys, "mesh:garden:read")
        reads_all = ticket(keys, "mesh:read")
        writer = ticket(keys, "mesh:garden")
        rules = schema_rules.rho_constants()
        schema = json.loads((MESH / "schemas" / "mesh-project.v1.json").read_text())

        alpha = Mesh(root, "alpha", keys)
        write_garden(alpha.projects, 28580)
        (alpha.projects / "garden" / "deployed.txt").write_text(
            f"gateway\truns=native;host=127.0.0.1\nnode.0123456789abcdef\tboard=;link=wifi,ssid:GardenNet,password:{WIFI_NODE}\n")
        meshes.append(alpha.start())
        a = alpha.base
        # The shed board was programmed and has no record yet: the first look at
        # the mesh records what it runs, in this Mesh's shape, beside the two
        # records an older Mesh wrote.
        send(a, "GET", "/mesh/p/garden", cookie=admin)
        check("node.1111222233334444\tsig=2;area=5;key=" in (alpha.projects / "garden" / "deployed.txt").read_text(),
              "a programmed board is recorded with the area and the key's scope")

        # ---------------------------------------------------------- export
        # @verifies MESH-XFER-0001, MESH-XFER-0002, MESH-XFER-0003, MESH-XFER-0009, MESH-0026, MESH-XFER-0024
        status, yaml_text, headers = export(alpha, reader)
        check(status == 200, f"a read-only token exports the mesh ({status} {yaml_text[:200]})")
        check('filename="garden.mesh.yml"' in headers.get("Content-Disposition", ""),
              f"the download is named from the mesh id ({headers.get('Content-Disposition')})")
        flat = yaml_flat(yaml_text)
        check(flat.get("format") == "robohub-mesh-project" and flat.get("schema") == "1" and
              flat.get("exported.by", "").startswith("robohub-mesh 0.") and
              re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", flat.get("exported.at", "")),
              "the file says what it is, its schema, the Mesh that wrote it and when, in UTC")
        check(flat.get("mesh.id") == "garden" and flat.get("mesh.name") == "Garden" and flat.get("mesh.area") == "5" and
              flat.get("mesh.rest_port") == "28580" and flat.get("mesh.uplink_port") == "28581",
              "it carries the mesh's id, name, area and ports")
        check(flat.get("plan.node.1.id") == "0123456789abcdef" and flat.get("plan.node.3.baud") == "115200" and
              flat.get("plan.node.1.epoch") == "2" and flat.get("plan.gateway_serial") == "/dev/ttyUSB7" and
              flat.get("plan.relay_uid") == "0011223344556677" and flat.get("plan.node.1.ssid") == "GardenNet",
              "it carries the gateway, every node with its id, link settings and key epoch, and the relay id")
        check(not any(k.startswith(("plan.status", "plan.message", "plan.revision", "plan.updated", "plan.current_step"))
                      or k.endswith((".state", ".last_value", ".rev")) for k in flat),
              "it carries no runtime state")
        check(not any(s in yaml_text for s in SECRETS) and "legacy-gateway-token" not in yaml_text,
              "no gateway key, relay key, Wi-Fi password or token is in it in clear")
        check(all(k in flat for k in ("omitted.gateway_key", "omitted.relay_key", "omitted.password",
                                       "omitted.node.1.password", "omitted.gateway_token")),
              f"each secret left out is named under omitted ({sorted(k for k in flat if k.startswith('omitted'))})")

        # @verifies MESH-XFER-0004, MESH-XFER-0005, MESH-XFER-0006
        time.sleep(1.1)
        _, again, _ = export(alpha, reader)
        diff = [line for line in set(yaml_text.splitlines()) ^ set(again.splitlines())]
        check(all(line.strip().startswith("at:") for line in diff) and len(diff) == 2,
              f"two exports of an unchanged mesh differ only in exported.at ({diff})")
        keys_in_order = [line for line in yaml_text.splitlines() if line.startswith("    ") and line.strip().endswith(":")]
        check(yaml_text.index("    1:") < yaml_text.index("    2:") < yaml_text.index("    3:") and
              all('"' in line for line in yaml_text.splitlines() if ": " in line and not line.lstrip().startswith("#")),
              f"keys are written in order, a node row by its number, every value quoted ({len(keys_in_order)})")
        status, json_text, headers = export(alpha, reader, form="json")
        jflat = json_flat(as_json(json_text))
        check(status == 200 and 'garden.mesh.json' in headers.get("Content-Disposition", ""), "the JSON export downloads")
        check({k: v for k, v in jflat.items() if k != "exported.at"} == {k: v for k, v in flat.items() if k != "exported.at"},
              "the JSON export is the same tree with the same values")
        check(schema_rules.validate(as_json(json_text), schema) == [],
              f"the export validates against schemas/mesh-project.v1.json {schema_rules.validate(as_json(json_text), schema)}")
        stored = plan_flat(alpha.projects / "garden" / "plan.yml")
        unknown = sorted(k for k in stored if key_kind(k, rules) == "")
        check(not unknown, f"every key of the mesh's plan.yml is one the project file's rules account for {unknown}")

        # ---------------------------------------------- import on another Mesh
        beta = Mesh(root, "beta", keys)
        meshes.append(beta.start())
        b = beta.base

        # @verifies MESH-XFER-0004, MESH-0025, MESH-XFER-0014, MESH-XFER-0028
        status, preview, body = imported(beta, admin, {"file": json_text})
        check(status == 200 and preview.get("preview") is True and preview.get("mode") == "new" and
              preview.get("digest") == hashlib.sha256(json_text.encode()).hexdigest(),
              f"a JSON file is previewed, named by its SHA-256 ({status} {body[:300]})")
        check(beta.mesh_ids() == set(), "a preview writes nothing")
        status, answer, body = imported(beta, admin, {"file": json_text, "confirm": preview.get("digest", "")})
        check(status == 200 and answer.get("imported") is True and answer.get("mesh") == "garden",
              f"and imported as a new mesh under its own id ({status} {body[:300]})")
        _, back, _ = export(beta, admin)
        check(definition(yaml_flat(back)) == {k: v for k, v in definition(flat).items() if k != "plan.mesh_parent"},
              "exported again from the other Mesh, the definition is the same (the link to a mesh not there aside)")
        send(b, "POST", "/mesh/project/delete", {"project": "garden"}, bearer=admin)

        # @verifies MESH-XFER-0004, MESH-0025, MESH-XFER-0014, MESH-XFER-0020, MESH-XFER-0013, MESH-XFER-0022, MESH-XFER-0030, MESH-XFER-0018, MESH-XFER-0029, MESH-0027
        status, preview, body = imported(beta, admin, {"file": yaml_text})
        warnings = " ".join(preview.get("warnings", []))
        conflicts = " ".join(preview.get("conflicts", []))
        check(status == 200 and preview.get("mesh", {}).get("id") == "garden" and
              preview.get("mesh", {}).get("area") == "5" and preview.get("mesh", {}).get("uplink_port") == "28581",
              f"the YAML file is previewed as mesh garden with its own area and ports ({status} {body[:300]})")
        check({n.get("id") for n in preview.get("nodes", []) if n.get("change") == "added"} ==
              {"0123456789abcdef", "fedcba9876543210", "1111222233334444"},
              "the preview names each node added by id and name")
        check("/dev/ttyUSB7" in warnings and "garden-cert.pem" in warnings and "garden-key.pem" in warnings,
              f"the serial device and the TLS files are named as warnings ({warnings[:400]})")
        check("GardenNet" in warnings and "password" in warnings,
              "a Wi-Fi board without its password is named")
        check("north" in conflicts and "left out" in conflicts, f"a link to a mesh not here is left out and listed ({conflicts})")
        check(any("gateway key" in s for s in preview.get("secrets", {}).get("omitted", [])) and
              any(o.get("reason") == "never programmed" for o in preview.get("out_of_date", [])),
              "the secrets left out and the boards never programmed are listed")
        status, answer, body = imported(beta, admin, {"file": yaml_text, "confirm": preview.get("digest", "")})
        check(status == 200 and answer.get("mesh") == "garden", f"imported as new ({status} {body[:200]})")
        _, back, _ = export(beta, admin)
        check(definition(yaml_flat(back)) == {k: v for k, v in definition(flat).items() if k != "plan.mesh_parent"},
              "from YAML too, the other Mesh gives back the same definition")
        bplan = plan_flat(beta.projects / "garden" / "plan.yml")
        check(bplan.get("area_id") == "5" and bplan.get("uplink_port") == "28581" and "mesh_parent" not in bplan,
              "with its area and ports, and without the link to a mesh that is not there")
        bgarden = beta.projects / "garden"
        key_file = bgarden / "gateway.key"
        check(not key_file.exists() or key_file.read_text().strip() != GATEWAY_KEY,
              "a mesh imported without its key does not have the file's key")
        check(not (bgarden / "deployed.txt").exists(), "nothing is recorded as deployed")
        check(not (bgarden / "jobs").exists() or not any((bgarden / "jobs").iterdir()), "no job is queued")
        check(not (bgarden / "gateway.pid").exists(), "no gateway is started")
        check(not any(s in (bgarden / "plan.yml").read_text() for s in SECRETS), "no secret is in the imported plan")
        check(not marker.exists(), "a value that looks like a command is never run")
        status, page, _ = send(b, "POST", "/mesh/p/garden/apply", {"only": "node:0123456789abcdef"}, bearer=admin, hx=True)
        check("GardenNet" in page and "password" in page and (not (bgarden / "jobs").exists() or not any((bgarden / "jobs").iterdir())),
              f"the Wi-Fi board's build is refused, naming its network, and nothing is queued ({status} {re.sub('<[^>]+>', ' ', page)[:300]})")

        # ---------------------------------------------------------- access
        # @verifies MESH-XFER-0024, MESH-XFER-0025, MESH-0028
        status, _, _ = send(b, "POST", "/mesh/api/projects/import", {"file": yaml_text}, bearer=reader)
        check(status == 403, f"a read-only token cannot import ({status})")
        status, _, _ = send(b, "POST", "/mesh/api/projects/import", {"file": yaml_text}, bearer=writer)
        check(status == 403, f"a grant to change one mesh cannot import ({status})")
        status, _, _ = send(b, "POST", "/mesh/api/projects/import", {"file": yaml_text}, bearer=reads_all)
        check(status == 403, f"a grant to read every mesh cannot import ({status})")
        status, _, _ = send(a, "POST", "/mesh/api/projects/garden/export", {"passphrase": PASSPHRASE}, bearer=reader)
        check(status == 403, f"a read-only token cannot export the sealed secrets ({status})")
        status, _, _ = send(a, "POST", "/mesh/api/projects/garden/export", {"passphrase": PASSPHRASE}, bearer=writer)
        check(status == 403, f"nor can a grant to change the mesh ({status})")
        status, _, _ = send(a, "GET", "/mesh/api/projects/garden/export")
        check(status in (302, 401), f"no ticket, no export ({status})")
        status, _, _ = send(b, "POST", "/mesh/api/projects/import", {"file": yaml_text}, cookie=admin)
        check(status == 403, f"a cookie session posting to the API without the form token is refused ({status})")
        status, _, _ = send(b, "POST", "/mesh/project/import", {"file": yaml_text, "mode": "copy"}, cookie=admin)
        check(status == 403, f"a page's import without the form token is refused ({status})")
        status, _, _ = send(b, "POST", "/mesh/project/import", {"file": yaml_text, "mode": "copy"}, bearer=admin)
        check(status == 403, f"a page's import needs the form token even with a bearer ticket ({status})")
        status, _, _ = send(a, "POST", "/mesh/project/garden/export",
                            {"passphrase": PASSPHRASE, "passphrase2": PASSPHRASE}, cookie=admin)
        check(status == 403, f"a page's sealed export without the form token is refused ({status})")
        check(len(beta.mesh_ids()) == 1, "none of the refused requests wrote anything")

        # ---------------------------------------------------------- pages
        # @verifies MESH-XFER-0027
        _, settings, _ = send(a, "GET", "/mesh/project/garden/settings", cookie=admin)
        csrf = re.search(r'name="csrf" value="([^"]*)"', settings)
        check('href="/mesh/project/garden/export"' in settings and 'href="/mesh/project/garden/export?format=json"' in settings,
              "the settings page offers to export as YAML and JSON")
        check('name="passphrase2"' in settings and 'action="/mesh/project/garden/export"' in settings,
              "and with its secrets sealed, the passphrase typed twice")
        check('name="mode" value="overwrite"' in settings and 'type="file"' in settings and "FileReader" in settings,
              "and to import a file over the mesh, read by the page into the form")
        _, fleet, _ = send(a, "GET", "/mesh/fleet", cookie=admin)
        check('id="import-mesh"' in fleet and 'value="copy"' in fleet and 'value="new"' in fleet,
              "the fleet page offers to import a file as a new mesh or a copy")
        _, reader_settings, _ = send(a, "GET", "/mesh/project/garden/settings", bearer=reader)
        check('href="/mesh/project/garden/export"' in reader_settings and 'name="passphrase2"' not in reader_settings,
              "someone who may only read the mesh is offered the plain export alone")
        _, beta_fleet, _ = send(b, "GET", "/mesh/fleet", cookie=admin)
        beta_csrf = re.search(r'name="csrf" value="([^"]*)"', beta_fleet)
        status, page, _ = send(b, "POST", "/mesh/project/import",
                               {"file": yaml_text, "mode": "copy", "csrf": beta_csrf.group(1) if beta_csrf else ""}, cookie=admin)
        digest = hashlib.sha256(yaml_text.encode()).hexdigest()
        check(status == 200 and digest in page and 'name="confirm"' in page and "Garden copy" in page,
              f"a page's import shows its preview, bound to the file's digest ({status})")
        check(len(beta.mesh_ids()) == 1, "and writes nothing until it is confirmed")

        # ---------------------------------------------------------- sealed
        # @verifies MESH-XFER-0010, MESH-XFER-0017, MESH-0026, MESH-XFER-0011
        status, sealed_text, headers = send(a, "POST", "/mesh/api/projects/garden/export", {"passphrase": PASSPHRASE}, bearer=admin)
        sflat = yaml_flat(sealed_text)
        check(status == 200 and sflat.get("sealed.cipher") == "aes-256-gcm" and sflat.get("sealed.kdf") == "pbkdf2-hmac-sha256" and
              sflat.get("sealed.iterations") == "600000" and len(sflat.get("sealed.salt", "")) == 32 and
              len(sflat.get("sealed.nonce", "")) == 24 and sflat.get("sealed.data"),
              f"a sealed export carries the secrets in sealed: AES-256-GCM, PBKDF2-HMAC-SHA256, 600000 iterations ({status} {sealed_text[:200]})")
        check(not any(s in sealed_text for s in SECRETS), "and none of them in clear, nor in the record of what runs")
        check("omitted.relay_key" in sflat and "omitted.gateway_key" not in sflat and "omitted.node.1.password" not in sflat,
              "the relay key is never exported; the key and the passwords are sealed, not omitted")
        check("no-store" in headers.get("Cache-Control", ""), "a sealed export is not cached")
        status, _, _ = send(a, "POST", "/mesh/api/projects/garden/export?passphrase=url-passphrase-123", {},
                            bearer=admin)
        check(status == 422, f"a passphrase in the address is not taken ({status})")
        status, _, _ = send(a, "POST", "/mesh/api/projects/garden/export", {"passphrase": "short"}, bearer=admin)
        check(status == 422, f"a passphrase shorter than 12 characters is refused ({status})")
        settings_csrf = csrf.group(1) if csrf else ""
        status, page, _ = send(a, "POST", "/mesh/project/garden/export",
                               {"passphrase": PASSPHRASE, "passphrase2": PASSPHRASE + "x", "csrf": settings_csrf}, cookie=admin)
        check(status == 400, f"the page refuses two passphrases that differ ({status})")
        status, page_sealed, _ = send(a, "POST", "/mesh/project/garden/export",
                                      {"passphrase": PASSPHRASE, "passphrase2": PASSPHRASE, "format": "json", "csrf": settings_csrf},
                                      cookie=admin)
        check(status == 200 and as_json(page_sealed).get("sealed", {}).get("cipher") == "aes-256-gcm",
              f"and exports sealed JSON with the form token ({status})")

        # @verifies MESH-XFER-0012, MESH-0027
        gamma = Mesh(root, "gamma", keys)
        meshes.append(gamma.start())
        status, answer, body = imported(gamma, admin, {"file": sealed_text, "passphrase": "wrong horse battery"})
        check(status == 422 and "cannot be opened" in problems_text(answer) and "without its secrets" in problems_text(answer),
              f"a wrong passphrase imports nothing and offers to import without the secrets ({status} {body[:300]})")
        moved = sealed_text.replace('id: "garden"', 'id: "orchard"')
        status, answer, body = imported(gamma, admin, {"file": moved, "passphrase": PASSPHRASE})
        check(status == 422 and "cannot be opened" in problems_text(answer),
              f"a sealed block moved into another file does not open ({status})")
        check(gamma.mesh_ids() == set(), "neither wrote anything")

        # @verifies MESH-XFER-0017, MESH-BUILD-0001, MESH-BUILD-0012
        status, preview, body = imported(gamma, admin, {"file": sealed_text, "passphrase": PASSPHRASE})
        outdated = {o.get("name"): o.get("reason") for o in preview.get("out_of_date", [])}
        check(status == 200 and "Shed board" not in outdated,
              f"a board recorded with the same area and key is current after the import ({status} {outdated})")
        check(outdated.get("Pump board") == "recorded without its area and key",
              f"one recorded by an older Mesh is out of date and says why ({outdated})")
        # @verifies MESH-XFER-0010, MESH-XFER-0014, MESH-XFER-0017, MESH-0025
        status, answer, body = import_confirmed(gamma, admin, {"file": sealed_text, "passphrase": PASSPHRASE})
        ggarden = gamma.projects / "garden"
        check(status == 200 and answer.get("mesh") == "garden", f"the right passphrase imports it ({status} {body[:300]})")
        check((ggarden / "gateway.key").exists() and (ggarden / "gateway.key").read_text().strip() == GATEWAY_KEY and
              oct((ggarden / "gateway.key").stat().st_mode & 0o777) == "0o600",
              "with the mesh's gateway key, owner-only")
        gplan = (ggarden / "plan.yml").read_text() if (ggarden / "plan.yml").exists() else ""
        check(WIFI_NODE in gplan and WIFI_MESH in gplan, "with the Wi-Fi passwords")
        check((ggarden / "deployed.txt").exists() and
              (ggarden / "deployed.txt").read_text() == (alpha.projects / "garden" / "deployed.txt").read_text(),
              "and, with the key, area, uplink port, numbers and ids all kept, the record of what runs")
        check((ggarden / "nodes.txt").exists() and "0123456789abcdef|5|1|board|2|0|" in (ggarden / "nodes.txt").read_text(),
              "its node manifest admits its boards at the same area, number and epoch")
        check("import.wifi_needed" not in gplan, "and a Wi-Fi board imported with its password is not held back")
        gamma.stop()

        # @verifies MESH-XFER-0011
        logs = " ".join(m.log_path.read_text() for m in meshes if m.log_path.exists())
        found = [str(p) for p in root.rglob("*") if p.is_file() and PASSPHRASE.encode() in p.read_bytes()]
        check(PASSPHRASE not in logs and not found, f"the passphrase is in no log and no file ({found})")

        # ---------------------------------------------------------- conflicts
        # @verifies MESH-XFER-0018, MESH-XFER-0015
        status, answer, body = imported(beta, admin, {"file": sealed_text, "passphrase": PASSPHRASE, "name": "Garden twin"})
        text = problems_text(answer)
        check(status == 409 and "0123456789abcdef" in text and "copy" in text,
              f"node ids another mesh here has refuse an import with identity and offer a copy ({status} {text[:300]})")
        (beta.projects / "garden" / "gateway.key").write_text(GATEWAY_KEY + "\n")
        status, answer, body = imported(beta, admin, {"file": sealed_text, "passphrase": PASSPHRASE})
        check(status == 409 and "gateway key" in problems_text(answer),
              f"so does a sealed gateway key another mesh here uses ({status})")
        status, preview, body = imported(beta, admin, {"file": yaml_text, "mode": "copy"})
        check(status == 200 and preview.get("mesh", {}).get("id") == "Garden-copy", f"the copy is offered ({status} {body[:200]})")
        status, answer, body = imported(beta, admin, {"file": yaml_text, "mode": "copy", "confirm": preview.get("digest", "")})
        copy = plan_flat(beta.projects / "Garden-copy" / "plan.yml")
        check(status == 200 and copy.get("project.name") == "Garden copy" and copy.get("node.count") == "3",
              f"a copy is imported with the file's boards ({status})")
        check(not any(copy.get(f"node.{n}.id") in ("0123456789abcdef", "fedcba9876543210", "1111222233334444") for n in (1, 2, 3)),
              "with none of the file's node ids")
        check("mesh_parent" not in copy and "relay_uid" not in copy and
              not (beta.projects / "Garden-copy" / "deployed.txt").exists() and
              not (beta.projects / "Garden-copy" / "gateway.key").exists(),
              "and no link, relay id, deployed record or key of its own")
        check(copy.get("area_id") is None and plan_flat(beta.state / "fleet.yml").get("mesh.Garden-copy.area") not in (None, "5"),
              "placed by the fleet away from the other meshes")

        # @verifies MESH-XFER-0018, MESH-XFER-0014, MESH-XFER-0017
        delta = Mesh(root, "delta", keys)
        occupant = delta.projects / "occupant"
        occupant.mkdir(parents=True)
        (occupant / "project.txt").write_text("project.name=Occupant\narea_id=5\nrest_port=28580\nuplink_port=28581\nnode.count=0\n")
        (delta.projects / "garden").mkdir()
        (delta.projects / "garden" / "notes.txt").write_text("a folder named like the mesh, not a mesh\n")
        meshes.append(delta.start())
        send(delta.base, "GET", "/mesh/fleet", bearer=admin)
        status, preview, body = imported(delta, admin, {"file": sealed_text, "passphrase": PASSPHRASE, "name": "Garden West"})
        conflicts = " ".join(preview.get("conflicts", []))
        check(status == 200 and preview.get("mesh", {}).get("id") == "Garden-West" and "taken" in conflicts,
              f"a mesh id taken here gives the mesh an id from its name, and says so ({status} {conflicts[:300]})")
        check(preview.get("mesh", {}).get("area") != "5" and preview.get("mesh", {}).get("uplink_port") != "28581" and
              "area 5" in conflicts and "uplink port 28581" in conflicts,
              "an area and ports another mesh has are replaced from the fleet and listed")
        status, answer, body = imported(delta, admin, {"file": sealed_text, "passphrase": PASSPHRASE, "name": "Garden West",
                                                       "confirm": preview.get("digest", "")})
        west = delta.projects / "Garden-West"
        check(status == 200 and (west / "gateway.key").read_text().strip() == GATEWAY_KEY and not (west / "deployed.txt").exists(),
              "with the key kept but another area, no board is recorded as deployed")
        missing = yaml_text.replace('name: "Shed board"', 'board: "no-such-board"\n      name: "Shed board"')
        status, preview, body = imported(delta, admin, {"file": missing, "mode": "copy", "name": "Catalog"})
        check(status == 200 and any("no-such-board" in w for w in preview.get("warnings", [])),
              f"a board this Mesh's catalog lacks is listed and the node kept ({status} {body[:200]})")
        imported(delta, admin, {"file": missing, "mode": "copy", "name": "Catalog", "confirm": preview.get("digest", "")})
        cplan = plan_flat(delta.projects / "Catalog" / "plan.yml")
        shed = [n for n in (1, 2, 3) if cplan.get(f"node.{n}.board") == "no-such-board"]
        if shed:
            nid = cplan.get(f"node.{shed[0]}.id", "")
            if not nid:
                send(delta.base, "GET", "/mesh/p/Catalog", bearer=admin)
                nid = plan_flat(delta.projects / "Catalog" / "plan.yml").get(f"node.{shed[0]}.id", "")
            status, page, _ = send(delta.base, "POST", "/mesh/p/Catalog/apply", {"only": f"node:{nid}"}, bearer=admin, hx=True)
            jobs = delta.projects / "Catalog" / "jobs"
            check("no-such-board" in page and (not jobs.exists() or not any(jobs.iterdir())),
                  f"and its build is refused until the catalog has it ({re.sub('<[^>]+>', ' ', page)[:300]})")
        else:
            check(False, "the node with the missing board was imported")

        # ---------------------------------------------------------- overwrite
        # @verifies MESH-XFER-0016, MESH-XFER-0019, MESH-XFER-0022, MESH-XFER-0020, MESH-0027
        garden = alpha.projects / "garden"
        before_key = (garden / "gateway.key").read_text()
        before_tokens = (garden / "tokens.jsonl").read_text()
        send(a, "POST", "/mesh/p/garden/node/1111222233334444/delete", {}, bearer=admin, hx=True)
        send(a, "POST", "/mesh/project/garden/settings", {"name": "Garden renamed", "description": "changed",
                                                          "gateway_url": "", "telemetry_retention_days": "30"}, bearer=admin)
        aplan = plan_flat(garden / "plan.yml")
        revision = aplan.get("revision", "")
        check(aplan.get("node.count") == "2" and aplan.get("project.name") == "Garden renamed", "the mesh is edited after its export")
        # Deleting the shed board took its record of what runs with it; what the
        # overwrite must keep is the record as it is now.
        before_deployed = (garden / "deployed.txt").read_text()
        status, preview, body = imported(alpha, admin, {"file": yaml_text, "mode": "overwrite", "target": "garden"})
        check(status == 200 and {n.get("id") for n in preview.get("nodes", []) if n.get("change") == "added"} == {"1111222233334444"},
              f"an overwrite's preview names the node it brings back ({status} {body[:300]})")
        check(any(m.get("change") == "admits" and "1111222233334444" in m.get("board", "") for m in preview.get("manifest", [])),
              f"and that the new manifest admits it ({preview.get('manifest')})")
        check(any("Garden renamed" in c for c in preview.get("confirm", [])), "and asks for the mesh's name to be typed")
        status, answer, body = imported(alpha, admin, {"file": yaml_text, "mode": "overwrite", "target": "garden",
                                                       "name": "Garden", "confirm": preview.get("digest", "")})
        check(status == 409 and plan_flat(garden / "plan.yml").get("revision") == revision,
              f"a confirmation without the mesh's name as it is now is refused, and writes nothing ({status})")
        status, answer, body = imported(alpha, admin, {"file": yaml_text, "mode": "overwrite", "target": "garden",
                                                       "name": "Garden renamed", "confirm": "0" * 64})
        check(status == 412 and plan_flat(garden / "plan.yml").get("revision") == revision,
              f"a confirmation naming another file is refused with 412 ({status} {body[:200]})")
        send(a, "POST", "/mesh/project/garden/settings", {"name": "Garden renamed", "description": "changed again",
                                                          "gateway_url": "", "telemetry_retention_days": "30"}, bearer=admin)
        revision = plan_flat(garden / "plan.yml").get("revision", "")
        status, preview, body = imported(alpha, admin, {"file": yaml_text, "mode": "overwrite", "target": "garden"})
        status, answer, body = imported(alpha, admin, {"file": yaml_text, "mode": "overwrite", "target": "garden",
                                                       "name": "Garden renamed", "confirm": preview.get("digest", "")})
        after = plan_flat(garden / "plan.yml")
        check(status == 200 and answer.get("imported") is True, f"the overwrite is confirmed ({status} {body[:200]})")
        check({k: v for k, v in after.items() if key_kind(k, rules) == "definition"} ==
              {k[5:]: v for k, v in flat.items() if k.startswith("plan.")},
              "the plan's definition is the file's")
        check(after.get("area_id") == "5" and after.get("rest_port") == "28580" and after.get("uplink_port") == "28581" and
              (garden / "gateway.key").read_text() == before_key and (garden / "deployed.txt").read_text() == before_deployed and
              (garden / "tokens.jsonl").read_text() == before_tokens,
              "and the mesh keeps its area, ports, gateway key, deployed record and tokens")
        check(after.get(f"node.1.password") == WIFI_NODE and after.get("password") == WIFI_MESH,
              "the Wi-Fi passwords the file leaves out stay with the nodes it keeps")
        check((garden / "revisions" / f"{revision}.yml").exists(),
              f"the plan it replaced is kept as revision {revision}, though one was kept moments before")
        check(not (garden / "jobs").exists() or not any((garden / "jobs").iterdir()), "nothing is queued by the overwrite")

        # @verifies MESH-XFER-0023
        revision = after.get("revision", "")
        status, preview, body = imported(alpha, admin, {"file": yaml_text, "mode": "overwrite", "target": "garden"})
        check(status == 200 and preview.get("unchanged") is True, f"importing the same file again previews no change ({body[:200]})")
        status, answer, body = imported(alpha, admin, {"file": yaml_text, "mode": "overwrite", "target": "garden",
                                                       "name": "Garden", "confirm": preview.get("digest", "")})
        check(status == 200 and answer.get("unchanged") is True and answer.get("imported") is False and
              plan_flat(garden / "plan.yml").get("revision") == revision,
              f"and writes nothing, raising no revision ({status} {body[:200]})")

        # @verifies MESH-XFER-0019
        (beta.projects / "Garden-copy" / "gateway.key").write_text(OTHER_KEY + "\n")
        _, copy_sealed, _ = send(b, "POST", "/mesh/api/projects/Garden-copy/export", {"passphrase": PASSPHRASE}, bearer=admin)
        status, preview, body = imported(alpha, admin, {"file": copy_sealed, "passphrase": PASSPHRASE, "mode": "overwrite",
                                                        "target": "garden"})
        check(status == 200 and any("must be built and flashed again" in w for w in preview.get("warnings", [])),
              f"a file whose sealed key differs says boards flashed with it must be built again ({status} {body[:300]})")
        status, answer, body = imported(alpha, admin, {"file": copy_sealed, "passphrase": PASSPHRASE, "mode": "overwrite",
                                                       "target": "garden", "name": "Garden",
                                                       "confirm": preview.get("digest", "")})
        check(status == 200 and (garden / "gateway.key").read_text() == before_key,
              f"and the overwrite keeps the mesh's own gateway key ({status})")
        imported(alpha, admin, {"file": yaml_text, "mode": "overwrite", "target": "garden", "name": "Garden copy",
                                "confirm": hashlib.sha256(yaml_text.encode()).hexdigest()})

        # ---------------------------------------------------------- invalid files
        # @verifies MESH-XFER-0007
        newer = yaml_text.replace('schema: "1"', 'schema: "2"')
        status, answer, _ = imported(beta, admin, {"file": newer, "mode": "copy"})
        text = problems_text(answer)
        check(status == 422 and "schema 2" in text and "schema 1" in text and "robohub-mesh 0." in text,
              f"a newer schema is refused naming its schema, Mesh's own and the Mesh that wrote it ({text})")
        older = yaml_text.replace('schema: "1"', 'schema: "0"')
        status, answer, _ = imported(beta, admin, {"file": older, "mode": "copy"})
        check(status == 422 and "never written" in problems_text(answer), "a schema no Mesh wrote is refused")

        # @verifies MESH-XFER-0001, MESH-XFER-0004, MESH-XFER-0008, MESH-XFER-0026, MESH-XFER-0028, MESH-0027
        count = len(beta.mesh_ids())
        status, answer, _ = imported(beta, admin, {"file": yaml_text.replace('format: "robohub-mesh-project"', 'format: "robohub-energy"')})
        check(status == 422 and "format" in problems_text(answer), f"a file of another format is refused, naming format")
        listed = yaml_text.replace('  gateway: "native"', '  gateway:\n    - native')
        status, answer, _ = imported(beta, admin, {"file": listed, "mode": "copy"})
        check(status == 422 and re.search(r"line \d+: a list is not allowed", problems_text(answer)),
              f"a YAML line Mesh cannot read is refused with its line number ({problems_text(answer)})")
        bad = yaml_text.replace('id: "garden"', 'id: "../evil"').replace('baud: "115200"', 'baud: "fast"\n      colour: "red"')
        status, answer, _ = imported(beta, admin, {"file": bad})
        text = problems_text(answer)
        check(status == 422 and "mesh.id" in text and "colour" in text and re.search(r"line \d+, mesh\.id", text),
              f"every problem is named, with its key and line ({text[:400]})")
        status, answer, _ = imported(beta, admin, {"file": json.dumps({"format": "robohub-mesh-project", "schema": "1",
                                                                       "mesh": {"id": "x"}, "plan": {"node": [1, 2]}})})
        check(status == 422 and "plan.node" in problems_text(answer), "a JSON list is refused, naming its path")
        check(len(beta.mesh_ids()) == count, "an invalid file writes nothing")

        # @verifies MESH-XFER-0026, MESH-XFER-0008
        big = yaml_text + "#" + "x" * (1024 * 1024) + "\n"
        status, answer, _ = imported(beta, admin, {"file": big})
        check(status == 413, f"a file over 1 MiB is 413 ({status})")
        status, _, _ = send(b, "POST", "/mesh/api/projects/import", raw=b"file=" + b"%41" * (1200 * 1024), bearer=admin)
        check(status == 413, f"a body too large for a 1 MiB file is 413 before it is read ({status})")

        # ---------------------------------------------------------- interrupted
        # @verifies MESH-XFER-0021, MESH-0027
        epsilon = Mesh(root, "epsilon", keys)
        meshes.append(epsilon.start({"ROBOHUB_MESH_TEST_IMPORT_HALT": "before-rename"}))
        _, preview, _ = imported(epsilon, admin, {"file": yaml_text})
        try:
            send(epsilon.base, "POST", "/mesh/api/projects/import", {"file": yaml_text, "confirm": preview.get("digest", "")},
                 bearer=admin)
        except Exception:
            pass
        epsilon.process.wait(timeout=20)
        staged = [p.name for p in epsilon.projects.iterdir() if p.name.startswith(".import-")]
        check(bool(staged) and "garden" not in epsilon.mesh_ids() and
              "mesh.garden.area" in plan_flat(epsilon.state / "fleet.yml"),
              f"an import stopped between staging and rename leaves a hidden folder and a fleet place ({staged})")
        epsilon.stop()
        epsilon.start()
        check(not any(p.name.startswith(".import-") for p in epsilon.projects.iterdir()) and
              "mesh.garden.area" not in plan_flat(epsilon.state / "fleet.yml") and epsilon.mesh_ids() == set(),
              "and Mesh removes both at start: no mesh, no folder, no fleet place")
        status, answer, _ = import_confirmed(epsilon, admin, {"file": yaml_text})
        check(status == 200 and epsilon.mesh_ids() == {"garden"}, "the same import then completes")
    finally:
        for mesh in meshes:
            mesh.stop()
        if marker.exists():
            marker.unlink()
        if not failures:
            shutil.rmtree(root, ignore_errors=True)
        else:
            print(f"logs and state kept in {root}")
    if failures:
        print(f"{len(failures)} checks failed")
        return 1
    print("mesh project file smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
