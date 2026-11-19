#!/usr/bin/env python3
"""A stand-in Zigbee2MQTT for tests/zigbee_smoke.py.

Started by Mesh exactly as it starts the real one — `node <install>/index.js`
with ZIGBEE2MQTT_DATA and Z2M_ONBOARD_NO_SERVER set — through a fake `node` that
runs this script. It reads the configuration and secret file Mesh wrote,
connects to the smoke's broker as the broker user the keeper issued, and speaks
the part of Zigbee2MQTT 2.x's bridge Mesh uses: bridge/state, bridge/info and
bridge/devices (retained), permit_join, device/rename and device/remove requests
answered on bridge/response/... with their transaction, and one S60ZBTPF plug
that takes {"state":"ON"|"OFF"} on <base>/<name>/set and reports its state on
<base>/<name> and its availability on <base>/<name>/availability.

Like Zigbee2MQTT, it makes its network on its first start: it writes a network
key into secret.yaml and the PAN ids into configuration.yaml, so the smoke can
check that Mesh never overwrites them. It never opens the serial port.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time

DATA = os.environ.get("ZIGBEE2MQTT_DATA", "")
PLUG = "0xa4c1380000000002"
UNKNOWN = "0xa4c1380000000003"
COORDINATOR = "0x00124b0000000001"


def yaml_value(text: str, section: str, key: str) -> str:
    inside = section == ""
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            inside = section == "" or line.split(":", 1)[0] == section
            if section == "" and line.split(":", 1)[0] == key:
                return line.split(":", 1)[1].strip().strip('"').strip("'")
            continue
        if inside and section and line.strip().split(":", 1)[0] == key:
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return ""


def main() -> int:
    if os.environ.get("Z2M_ONBOARD_NO_SERVER") != "1":
        print("fake z2m: Z2M_ONBOARD_NO_SERVER is not set", flush=True)
        return 2
    config_path = os.path.join(DATA, "configuration.yaml")
    secret_path = os.path.join(DATA, "secret.yaml")
    config = open(config_path).read()
    secret = open(secret_path).read()
    server = yaml_value(config, "mqtt", "server")
    user = yaml_value(config, "mqtt", "user")
    base = yaml_value(config, "mqtt", "base_topic")
    password = yaml_value(secret, "", "password")
    match = re.match(r"mqtt://([^:]+):(\d+)", server)
    if not match or not user or not base or not password:
        print("fake z2m: configuration incomplete", flush=True)
        return 2
    host, port = match.group(1), match.group(2)
    # The network is made once, as Zigbee2MQTT makes it, and written back the way
    # it writes it: the key into the secret file, the PAN ids into the configuration.
    if yaml_value(secret, "", "network_key") == "GENERATE":
        secret = re.sub(r"(?m)^network_key:.*$", "network_key:\n  - 1\n  - 3\n  - 5\n  - 7\n  - 9\n  - 11\n  - 13\n  - 15\n"
                        "  - 0\n  - 2\n  - 4\n  - 6\n  - 8\n  - 10\n  - 12\n  - 13", secret)
        open(secret_path, "w").write(secret if secret.endswith("\n") else secret + "\n")
    if "pan_id: GENERATE" in config:
        config = config.replace("pan_id: GENERATE", "pan_id: 6754", 1)
        config = config.replace("ext_pan_id: GENERATE", "ext_pan_id:\n    - 221\n    - 221\n    - 221\n    - 221\n"
                                "    - 221\n    - 221\n    - 221\n    - 221", 1)
        open(config_path, "w").write(config)
    open(os.path.join(DATA, "coordinator_backup.json"), "w").write('{"metadata":{"format":"zigpy/open-coordinator-backup"}}\n')

    auth = ["-h", host, "-p", port, "-u", user, "-P", password]

    def pub(topic: str, payload, retain: bool = False) -> None:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        subprocess.run(["mosquitto_pub", *auth, "-t", f"{base}/{topic}", "-m", text] + (["-r"] if retain else []),
                       check=False, capture_output=True, timeout=10)

    names = {PLUG: PLUG, UNKNOWN: UNKNOWN}
    state = {"on": False, "permit_end": 0}

    def devices() -> list:
        return [
            {"ieee_address": COORDINATOR, "type": "Coordinator", "friendly_name": "Coordinator", "definition": None},
            {"ieee_address": PLUG, "type": "Router", "friendly_name": names[PLUG], "supported": True,
             "interview_state": "SUCCESSFUL", "definition": {"model": "S60ZBTPF", "vendor": "SONOFF", "description": "Smart plug"}},
            {"ieee_address": UNKNOWN, "type": "EndDevice", "friendly_name": names[UNKNOWN], "supported": False,
             "interview_state": "SUCCESSFUL", "definition": None},
        ]

    def info() -> dict:
        body = {"version": "2.14.3", "coordinator": {"ieee_address": COORDINATOR, "type": "EmberZNet",
                                                     "meta": {"revision": "7.4.4.0 [GA]"}},
                "network": {"channel": 15, "pan_id": 6754}, "permit_join": state["permit_end"] > time.time()}
        if body["permit_join"]:
            body["permit_join_end"] = int(state["permit_end"] * 1000)
        return body

    def report() -> None:
        pub(names[PLUG], {"state": "ON" if state["on"] else "OFF", "power": 12.5 if state["on"] else 0, "voltage": 231,
                          "current": 0.06 if state["on"] else 0, "energy": 3.42, "energy_today": 0.12, "linkquality": 120})
        pub(names[PLUG] + "/availability", {"state": "online"}, retain=True)

    listener = subprocess.Popen(["mosquitto_sub", *auth, "-v", "-t", f"{base}/bridge/request/#", "-t", f"{base}/+/set"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    def stop(*_):
        pub("bridge/state", {"state": "offline"}, retain=True)
        listener.terminate()
        sys.exit(0)

    # A Zigbee2MQTT that hangs on its way down (seen when its broker went away
    # mid-shutdown): with `hang-on-term` in its data folder it ignores SIGTERM, so
    # the smoke can check that Mesh's stop still frees the coordinator.
    if DATA and os.path.exists(os.path.join(DATA, "hang-on-term")):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    else:
        signal.signal(signal.SIGTERM, stop)
    time.sleep(0.5)
    pub("bridge/state", {"state": "online"}, retain=True)
    pub("bridge/info", info(), retain=True)
    pub("bridge/devices", devices(), retain=True)
    print(f"fake z2m: online at {base} as {user}", flush=True)
    for line in listener.stdout:
        topic, _, payload = line.rstrip("\n").partition(" ")
        try:
            body = json.loads(payload)
        except ValueError:
            body = {}
        print(f"fake z2m: {topic} {payload}", flush=True)
        if topic == f"{base}/bridge/request/permit_join":
            seconds = int(body.get("time", 0))
            state["permit_end"] = time.time() + seconds if seconds > 0 else 0
            pub("bridge/response/permit_join", {"data": {"time": seconds}, "status": "ok", "transaction": body.get("transaction")})
            pub("bridge/info", info(), retain=True)
        elif topic == f"{base}/bridge/request/device/rename":
            ieee, to = body.get("from"), body.get("to")
            if ieee in names:
                names[ieee] = to
                pub("bridge/response/device/rename", {"data": {"from": ieee, "to": to}, "status": "ok",
                                                      "transaction": body.get("transaction")})
                pub("bridge/devices", devices(), retain=True)
                report()
            else:
                pub("bridge/response/device/rename", {"data": {}, "status": "error", "error": f"Device '{ieee}' does not exist",
                                                      "transaction": body.get("transaction")})
        elif topic == f"{base}/bridge/request/device/remove":
            ieee = body.get("id")
            ok = ieee in names
            pub("bridge/response/device/remove", {"data": {"id": ieee}, "status": "ok" if ok else "error",
                                                  "error": "" if ok else "no such device", "transaction": body.get("transaction")})
        elif topic.endswith("/set") and topic == f"{base}/{names[PLUG]}/set":
            if body.get("state") in ("ON", "OFF"):
                state["on"] = body["state"] == "ON"
                report()
            else:
                print("fake z2m: refused a state that is not ON or OFF", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
