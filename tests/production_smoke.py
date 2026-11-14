#!/usr/bin/env python3
"""Smoke test for a board's production build in Mesh.

Builds Mesh and starts it against a temporary state directory on the loopback,
with a stand-in `rho` first on its PATH, so the jobs it queues build and flash
nothing: the stand-in writes an image and an OTP file where a build would, prints
a production plan for `rho flash --production --dry-run`, and records what an
actual flash was given (its arguments, its environment and its standard input).
No board is reached and nothing is burned. It checks:

- the toggle is offered on a board whose platform has a production plan (an
  RP2350 Pico 2W) and not on a Linux host, and turning it on for a board that
  has none is refused; turning it on says what cannot be undone;
- a production build is refused without the mesh's signing key, and with a key
  file others can read; the key's path is kept beside the plan, never in it;
- once queued, the build is given the key by path and writes
  CONFIG_RH_PRODUCTION=y; the flash waits with the dry run's steps, refuses a
  wrong name, and runs `rho flash --production` with the typed name on its input
  and RH_PICO_OTP_JSON pointing at the build's OTP file.

    python3 tests/production_smoke.py
"""

import os
import shutil
import socket
import stat
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
# RHO_CMD names another rho to build and mint with, for a tree whose rho is mid-change.
RHO_CMD = os.environ.get("RHO_CMD") or (str(RHO) if RHO.exists() else (shutil.which("rho") or "rho"))

TOKEN = ""
KEY_TEXT = "-----BEGIN EC PRIVATE KEY-----\nnot-a-real-key-smoke-only\n-----END EC PRIVATE KEY-----\n"

# The stand-in rho: `build` and `flash` are recorded and do nothing to any
# board; anything else is the real rho.
STUB = r"""#!/bin/sh
log="$RH_SMOKE_LOG"
case "$1" in
build)
  {
    echo "build $*"
    echo "cwd=$(pwd)"
    echo "key=${RH_SECURE_BOOT_SIGNING_KEY:-}"
  } >> "$log"
  program="$(basename "$(pwd)" | tr - _)"
  mkdir -p build/output
  printf 'ELF' > "build/output/$program.elf"
  printf '{"rows": "smoke"}\n' > "build/output/$program.otp.json"
  exit 0
  ;;
flash)
  for arg in "$@"; do
    if [ "$arg" = "--dry-run" ]; then
      echo "PRODUCTION FLASH (smoke stand-in)"
      echo "  1. [IRREVERSIBLE] write the boot key hash into OTP"
      echo "  2. [reversible] write the image to the board"
      echo "otp=${RH_PICO_OTP_JSON:-}"
      echo "dry run: nothing was run"
      exit 0
    fi
  done
  typed="$(cat)"
  {
    echo "flash $*"
    echo "otp=${RH_PICO_OTP_JSON:-}"
    echo "typed=$typed"
  } >> "$log"
  exit 0
  ;;
esac
exec "$RH_SMOKE_REAL_RHO" "$@"
"""


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
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", "replace")


def get(base: str, path: str) -> str:
    request = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {TOKEN}", "HX-Request": "true"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", "replace")


def expect(condition: bool, what: str) -> None:
    if not condition:
        raise AssertionError(what)
    print(f"[PASS] {what}")


def jobs_of(project: Path) -> list:
    jobs = project / "jobs"
    return sorted(p.stem for p in jobs.glob("*.job")) if jobs.exists() else []


def wait_file(path: Path, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return True
        time.sleep(0.5)
    return path.exists()


def main() -> int:
    global TOKEN
    result = subprocess.run([RHO_CMD, "build", "."], cwd=MESH, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit("mesh build failed:\n" + result.stdout + result.stderr)
    binary = MESH / "build" / "mesh"
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="mesh-production-") as root:
        state = Path(root) / "state"
        projects = state / "projects"
        project = projects / "Prod"
        project.mkdir(parents=True)
        (project / "project.txt").write_text(
            "project.name=Prod\narea_id=6\ngateway=native\nnode.count=2\n"
            "node.1.name=Tank\nnode.1.parent=gateway\nnode.1.role=node\nnode.1.board=pico2w\nnode.1.link=uart\n"
            "node.1.id=0123456789abcdef\n"
            "node.2.name=Host\nnode.2.parent=gateway\nnode.2.role=node\nnode.2.board=arduino-uno-q\n"
            "node.2.id=aaaabbbbccccdddd\n")
        stub_dir = Path(root) / "bin"
        stub_dir.mkdir()
        stub = stub_dir / "rho"
        stub.write_text(STUB)
        stub.chmod(0o755)
        smoke_log = Path(root) / "rho-calls.log"
        smoke_log.write_text("")
        home = os.path.join(root, "home")
        TOKEN = mint_token(home)
        env = dict(os.environ)
        env.pop("RHO_CLIENT_ID", None)
        env["PATH"] = str(stub_dir) + os.pathsep + env.get("PATH", "")
        env["RH_SMOKE_LOG"] = str(smoke_log)
        env["RH_SMOKE_REAL_RHO"] = RHO_CMD
        env["ROBOHUB_WORKSPACE"] = str(PROJECTS)
        env["RHO_MESH_STATE_DIR"] = str(state)
        env["ROBOHUB_MESH_PROJECTS"] = str(projects)
        env.setdefault("RHO_HOME", os.path.join(os.path.expanduser("~"), ".rho"))
        env["HOME"] = home
        env["RHO_HTTP_ADDR"] = f"127.0.0.1:{port}"
        log_path = Path(root) / "mesh.log"
        log = open(log_path, "w")
        process = subprocess.Popen([str(binary)], cwd=MESH, env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
        try:
            wait_for(base)
            board = "0123456789abcdef"
            host = "aaaabbbbccccdddd"

            # The toggle: offered where the platform has a production plan.
            page = get(base, f"/mesh/p/Prod/node/{board}?tab=revoke")
            expect("Production build" in page and "Turn on production build" in page,
                   "a Pico 2W (RP2350) board is offered a production build")
            expect("cannot be undone" in page, "the toggle shows what cannot be undone")
            page = get(base, f"/mesh/p/Prod/node/{host}?tab=revoke")
            expect("Production build" not in page, "a Linux host is not offered one")
            fragment = hx(base, f"/mesh/p/Prod/node/{host}/production", {"action": "on"})
            expect("has no production mode" in fragment, "turning it on for a board without one is refused")
            plan = (project / "plan.yml").read_text()
            expect("production: on" not in plan.replace('"', ""), "and the plan does not say it")

            fragment = hx(base, f"/mesh/p/Prod/node/{board}/production", {"action": "on"})
            expect("Production build on for Tank" in fragment and "cannot be undone" in fragment,
                   "turning it on says so, with the warning")
            expect("Turn off production build" in fragment, "and offers to turn it off")

            # No key: refused before anything is prepared.
            fragment = hx(base, "/mesh/p/Prod/apply", {"only": f"node:{board}"})
            expect("secure-boot signing key, and none is in place" in fragment and jobs_of(project) == [],
                   "a production build is not queued without the mesh's signing key")

            key = Path(root) / "keys" / "rp2350.pem"
            key.parent.mkdir()
            key.write_text(KEY_TEXT)
            key.chmod(0o644)
            fragment = hx(base, f"/mesh/p/Prod/node/{board}/signing-key", {"signing_key": "keys/rp2350.pem"})
            expect("absolute path" in fragment, "a relative key path is refused")
            fragment = hx(base, f"/mesh/p/Prod/node/{board}/signing-key", {"signing_key": str(key)})
            expect("Signing key path saved" in fragment, "the key path is saved")
            key_setting = project / "signing-key.txt"
            expect(stat.S_IMODE(key_setting.stat().st_mode) == 0o600, "beside the plan, 0600")
            plan = (project / "plan.yml").read_text()
            expect(str(key) not in plan and "not-a-real-key" not in plan, "never in the plan")
            fragment = hx(base, "/mesh/p/Prod/apply", {"only": f"node:{board}"})
            expect("can be read by others" in fragment and jobs_of(project) == [],
                   "a key file others can read is refused")

            key.chmod(0o600)
            fragment = hx(base, "/mesh/p/Prod/apply", {"only": f"node:{board}"})
            expect("Queued." in fragment, "with an owner-only key the production build is queued")
            build_job, flash_job = jobs_of(project)[-2:]
            jobs = project / "jobs"
            build_script = (jobs / f"{build_job}.sh").read_text()
            flash_script = (jobs / f"{flash_job}.sh").read_text()
            expect(f"export RH_SECURE_BOOT_SIGNING_KEY='{key}'" in build_script and "not-a-real-key" not in build_script,
                   "the build is given the key by its path, not its contents")
            expect("--production --dry-run >" in build_script, "and writes what the flash will do")
            expect(" --production < \"$confirm\"" in flash_script and "--dry-run" not in flash_script,
                   "the flash runs rho flash --production with the typed name on its input")
            expect("node_1.otp.json" in flash_script and "RH_PICO_OTP_JSON" in flash_script,
                   "an RP2350 flash names the build's OTP file")

            expect(wait_file(jobs / f"{build_job}.exit", 120), "the build job finishes")
            code = (jobs / f"{build_job}.exit").read_text().strip()
            expect(code == "0", f"the build job succeeds (exit {code}): {(jobs / f'{build_job}.log').read_text()[-2000:] if code != '0' else ''}")
            conf = (project / "boards" / "node_1" / "project.conf").read_text()
            expect("CONFIG_RH_PRODUCTION=y" in conf, "the board's configuration asks for a production build")
            calls = smoke_log.read_text()
            expect(f"key={key}" in calls, "the build ran with the key's path in its environment")

            # The flash waits with the steps, and only the exact name runs it.
            page = get(base, f"/mesh/p/Prod/job/{flash_job}")
            expect("[IRREVERSIBLE] write the boot key hash into OTP" in page and "Type the board name (pico2w)" in page,
                   "the waiting flash shows every step and asks for the board's name")
            expect("every 2s" not in page, "and is not refreshed under the person typing")
            fragment = hx(base, f"/mesh/p/Prod/job/{flash_job}/go", {"board_name": "pico2"})
            expect("That is not the board" in fragment and not (jobs / f"{flash_job}.go").exists(),
                   "a wrong name runs nothing")
            fragment = hx(base, f"/mesh/p/Prod/job/{flash_job}/go", {})
            expect(not (jobs / f"{flash_job}.go").exists(), "nor does no name")
            fragment = hx(base, f"/mesh/p/Prod/job/{flash_job}/go", {"board_name": "pico2w"})
            expect((jobs / f"{flash_job}.go").exists(), "the exact name lets the flash run")
            expect(wait_file(jobs / f"{flash_job}.exit", 60), "the flash job finishes")
            calls = smoke_log.read_text()
            flash_line = [line for line in calls.splitlines() if line.startswith("flash ")]
            expect(flash_line and "--production" in flash_line[-1] and "--dry-run" not in flash_line[-1],
                   "rho flash was run with --production")
            expect("typed=pico2w" in calls, "with the typed name on its standard input")
            expect(f"otp={project}/boards/node_1/build/output/node_1.otp.json" in calls,
                   "and RH_PICO_OTP_JSON naming the build's OTP file")

            fragment = hx(base, f"/mesh/p/Prod/node/{board}/production", {"action": "off"})
            expect("Production build off for Tank" in fragment and "Turn on production build" in fragment,
                   "the production build can be turned off again")
            log.flush()
            expect("not-a-real-key" not in log_path.read_text(), "the key is never logged")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            log.close()
    print("mesh production smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
