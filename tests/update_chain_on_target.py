#!/usr/bin/env python3
"""On-target: Mesh builds and flashes a Pico 2 W as a node, then updates it over
its link through the gateway (MESH-0043, MESH-0040, MESH-0041; robohub ADR-0025).

Run with a Pico 2 W on USB (under WSL kept attached across its resets, e.g.
`usbipd attach --wsl --busid <id> --auto-attach`):

    python3 tests/update_chain_on_target.py

A Mesh of its own, built from this checkout, on a temporary state folder:

1. a mesh whose gateway runs on this machine, with one Pico 2 W board on the
   USB link (the gateway reads the board's serial port);
2. "build and flash": Mesh builds robohub-node's template for the board, with
   the board's key and the mesh's update key, and flashes it with the A/B
   partition table (the flash job is told to go, as a person would once the
   board is in its programming mode); Mesh records it as A/B-flashed with the
   managed layer;
3. the gateway is started, and the board reports its health through it;
4. "build and update over the link": Mesh builds the update image, signs it,
   hands it to the gateway, and the job ends when the board confirms it.

Over Wi-Fi the same chain needs the gateway's uplink reachable from the board;
under WSL2 in NAT mode that is a Windows port forward (MESH-0044), so this runs
over USB. The board keeps the image Mesh last gave it.
"""

import json
import os
import re
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
RHO_CMD = "rho"
TOKEN = ""
NODE_ID = "0a1b2c3d4e5f6071"
failures = []


def check(ok: bool, what: str) -> None:
    print(("[PASS] " if ok else "[FAIL] ") + what, flush=True)
    if not ok:
        failures.append(what)


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
         "--audience", "mesh", "--subject", "chain", "--scopes", "*", "--ttl", "2h"],
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


def pico_serial() -> str:
    by_id = Path("/dev/serial/by-id")
    if by_id.is_dir():
        for entry in sorted(by_id.iterdir()):
            if "Pico" in entry.name or "2e8a" in entry.name.lower():
                return str(entry)
    return ""


def jobs(project: Path) -> list:
    """Each job as (seq, kind, state), oldest first."""
    out = []
    folder = project / "jobs"
    if not folder.is_dir():
        return out
    for job in sorted(folder.glob("*.job")):
        seq = job.stem
        meta = dict(line.split("=", 1) for line in job.read_text().splitlines() if "=" in line)
        state = "queued"
        if (folder / f"{seq}.exit").exists():
            code = (folder / f"{seq}.exit").read_text().strip()
            state = {"0": "done", "125": "skipped", "130": "cancelled"}.get(code, "failed")
        elif (folder / f"{seq}.started").exists():
            state = "running"
        elif (folder / f"{seq}.wait").exists() and not (folder / f"{seq}.go").exists():
            state = "waiting"
        out.append((seq, meta.get("kind", ""), state))
    return out


def job_log(project: Path, seq: str) -> str:
    log = project / "jobs" / f"{seq}.log"
    return log.read_text(errors="replace") if log.exists() else ""


def follow(base: str, project: Path, kind: str, timeout: float, go: bool = False) -> tuple:
    """The newest job of `kind`, followed until it ends; told to go when it waits."""
    end = time.time() + timeout
    told = False
    last = ("", kind, "missing")
    while time.time() < end:
        # The job runner advances on Mesh's answers, so the page is asked for.
        call(base, "GET", "/mesh/p/Chain/apply")
        found = [j for j in jobs(project) if j[1] == kind]
        if found:
            last = found[-1]
            seq, _, state = last
            if state in ("done", "failed", "skipped", "cancelled"):
                return last
            if go and not told and state == "waiting":
                call(base, "POST", f"/mesh/p/Chain/job/{seq}/go", {})
                told = True
        time.sleep(2)
    return last


def main() -> int:
    global TOKEN
    serial = pico_serial()
    if not serial:
        print("skipped: no Pico 2 W on USB (/dev/serial/by-id)")
        return 77
    result = subprocess.run([RHO_CMD, "build", "."], cwd=MESH, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit("mesh build failed:\n" + result.stdout[-3000:] + result.stderr[-3000:])
    binary = MESH / "build" / "mesh"

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    root = Path(tempfile.mkdtemp(prefix="mesh-chain-"))
    state = root / "state"
    projects = state / "projects"
    project = projects / "Chain"
    project.mkdir(parents=True)
    rest = free_port()
    (project / "project.txt").write_text(
        "project.name=Chain\narea_id=9\ngateway=native\n"
        f"rest_port={rest}\ngateway_serial={serial}\n"
        "node.count=1\nnode.1.name=Pico\nnode.1.parent=gateway\nnode.1.role=node\n"
        f"node.1.id={NODE_ID}\nnode.1.kind=compute\nnode.1.board=pico2w\nnode.1.link=usb\n")
    home = root / "home"
    TOKEN = mint_token(str(home))
    env = dict(os.environ)
    env.pop("RHO_CLIENT_ID", None)
    env["ROBOHUB_WORKSPACE"] = str(PROJECTS)
    env["RHO_MESH_STATE_DIR"] = str(state)
    env["ROBOHUB_MESH_PROJECTS"] = str(projects)
    env.setdefault("RHO_HOME", os.path.join(os.path.expanduser("~"), ".rho"))
    # Mesh reads the token's key under HOME; the tools a build runs that live
    # in the person's own folders (a pip-installed cmake) are found where they are.
    env["PYTHONUSERBASE"] = env.get("PYTHONUSERBASE", os.path.expanduser("~/.local"))
    env["HOME"] = str(home)
    env["RHO_HTTP_ADDR"] = f"127.0.0.1:{port}"
    env["ROBOHUB_ROOT"] = str(PROJECTS / "robohub")
    # No keeper here to issue service tokens: Mesh and the gateway it starts (which
    # inherits this environment) answer requests from this machine only.
    env["RHO_AUTH_STANDALONE"] = "1"
    log = open(root / "mesh.log", "w")
    process = subprocess.Popen([str(binary)], cwd=MESH, env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
    try:
        wait_for(base)
        call(base, "GET", "/mesh/p/Chain")

        # --- Build and flash ---------------------------------------------
        status, body = call(base, "POST", "/mesh/p/Chain/apply", {"only": f"node:{NODE_ID}"})
        check(status == 200, f"build and flash is queued ({status})")
        built = follow(base, project, "build", 1200)
        check(built[2] == "done", f"Mesh builds robohub-node's template for the Pico 2 W ({built}): "
              + job_log(project, built[0])[-600:].replace("\n", " | "))
        flashed = follow(base, project, "flash", 300, go=True)
        flash_log = job_log(project, flashed[0])
        check(flashed[2] == "done" and "partition" in flash_log,
              f"and flashes it with the A/B partition table ({flashed}): " + flash_log[-600:].replace("\n", " | "))
        recorded = time.time() + 30
        plan = ""
        while time.time() < recorded:
            call(base, "GET", "/mesh/p/Chain/apply")
            plan = (project / "plan.yml").read_text() if (project / "plan.yml").exists() else ""
            if re.search(r'\bmanaged: *"?1"?', plan):
                break
            time.sleep(2)
        check(re.search(r'\bab: *"?1"?', plan) is not None and re.search(r'\bmanaged: *"?1"?', plan) is not None,
              "Mesh records the board as A/B-flashed with the managed layer")

        # --- The gateway, and the board's health through it --------------
        status, _ = call(base, "POST", "/mesh/p/Chain/apply", {"only": "gateway"})
        gateway = follow(base, project, "gateway", 600)
        check(gateway[2] == "done", f"the gateway is built and started ({gateway})")
        seen = False
        end = time.time() + 90
        while time.time() < end and not seen:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{rest}/api/v1/nodes", timeout=3) as answer:
                    listing = answer.read().decode()
                seen = '"health"' in listing
            except (urllib.error.URLError, ConnectionError, OSError):
                pass
            time.sleep(2)
        check(seen, "the board reports its health through the gateway")

        # --- Build and update over the link ------------------------------
        status, body = call(base, "POST", "/mesh/p/Chain/apply", {"only": f"update:{NODE_ID}"})
        check(status == 200 and "cannot take an update" not in body,
              f"build and update over the link is offered and queued ({status})")
        updated = follow(base, project, "update", 1500)
        update_log = job_log(project, updated[0])
        check(updated[2] == "done" and "update: confirmed" in update_log,
              f"the board takes the image over its link and confirms it ({updated}): "
              + update_log[-800:].replace("\n", " | "))
    finally:
        call(base, "POST", "/mesh/gateway/stop", {"project": "Chain"})
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()
        if failures:
            print(f"logs kept in {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)

    if failures:
        print("FAILED:", *failures, sep="\n  ")
        return 1
    print("OK: Mesh built and flashed a Pico 2 W as a node and updated it over its link through the gateway")
    return 0


if __name__ == "__main__":
    sys.exit(main())
