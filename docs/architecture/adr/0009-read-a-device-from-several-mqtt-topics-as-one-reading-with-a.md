---
id: ADR-0009
title: Read a device from several MQTT topics as one reading, with an onoff field type
status: accepted
date: 2026-11-19
requirements: [MESH-0029, MESH-0030, MESH-0031, MESH-0032, MESH-0022]
supersedes: []
superseded_by: []
---

# Read a device from several MQTT topics as one reading, with an onoff field type

## Context

Mesh reads an MQTT device from one status topic whose payload is JSON (the
Shelly Plug's `{prefix}/status/switch:0`), takes each field from a JSON path in
it, and reads a switch state only as a JSON boolean (`bool`). A plug reflashed
with Tasmota — the Sonoff S60TPF, robohub-devices ADR-0002 — reports otherwise:

- the relay on `stat/RESULT` (`{"POWER":"ON"}`), on `stat/POWER` as a bare word
  (`ON`) and every telemetry period on `tele/STATE`;
- the power meter on `tele/SENSOR` (`{"ENERGY":{"Power":12.5,...}}`);
- whether it is there on `tele/LWT`, as `Online` or `Offline`.

No single topic carries a whole reading, the relay is a word and not a boolean,
and the online payload is not `true`.

Options considered in review:

1. A topic per field line (a seventh column in the field grammar).
2. A list of status topics in the manifest, merged into one reading.
3. Widening `bool` to accept `ON`/`OFF`.
4. A new field type for ON/OFF words.

## Decision

Decided by the maintainer in review on 2026-11-19, and the contract
robohub-devices implements on its side:

- **Several topics as a list** (option 2): `telemetry_mqtt.status_topics` lists
  the topics; a manifest has it or the single `status_topic`, which keeps its
  meaning. Every listed topic is read; a message changes only the fields whose
  path it carries; each field takes the value of the newest message carrying it,
  and a field no message carries keeps its last value (MESH-0029). The field
  grammar does not change.
- **`$` is the whole payload** for a message that is not a JSON object or array;
  a path a message does not carry, or a payload that is not JSON for any other
  path, leaves the field unchanged; two lines may name one field with different
  paths, as alternatives (MESH-0030).
- **A new type, `onoff`** (option 4): ON/OFF, true/false, 1/0 in any case, from
  a JSON string, boolean or number or a bare payload, stored as 1 or 0; anything
  else is a protocol error for that field, which keeps its last value. `bool`
  does not change, so no manifest that works today reads differently (MESH-0031).
- **`online_up`**, optional, is the payload of `online_topic` that means online,
  compared case-sensitively; `true` when not given, the Shelly behaviour
  (MESH-0032).
- **A switch is confirmed from whichever topic carries the state**: the newest
  status message that carries the state field, if it is newer than the request
  (MESH-0022). For Tasmota that is the `stat/RESULT` or `stat/POWER` its command
  answers with; a `tele/SENSOR` without the relay does not count.
- The merge is done in Mesh's telemetry port (`telemetry_device_fields`), which
  already reads JSON for the device field map (ADR-0003, ADR-0007); HTTP devices
  are read through the same function, so `onoff` and `$` mean the same on both
  transports.

## Consequences

- A device manifest describes a multi-topic device with no code in Mesh; the
  Shelly manifest is read exactly as before.
- Merging by newest message relies on the broker provider's message times
  (`bus.topics`, whole seconds of age), the same clock the switch confirmation
  already used.
- Mesh's broker grants (`bus:` in its manifest) gain the Tasmota topics:
  `+/cmnd/POWER` to publish, `+/stat/#` and `+/tele/#` to follow.
- A protocol error is logged once per distinct message, not on every poll, and
  recorded nowhere else: recording it as a reading with a status would replace
  the field's last value.
