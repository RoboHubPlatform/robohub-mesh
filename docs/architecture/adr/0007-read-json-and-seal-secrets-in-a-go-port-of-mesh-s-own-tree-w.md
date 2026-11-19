---
id: ADR-0007
title: Read JSON and seal secrets in a Go port of Mesh's own tree, with no change to rho
status: accepted
date: 2026-11-19
requirements: [MESH-0026, MESH-XFER-0004, MESH-XFER-0010, MESH-XFER-0020]
supersedes: []
superseded_by: []
---

# Read JSON and seal secrets in a Go port of Mesh's own tree, with no change to rho

## Context

The project file needs things the Rho runtime does or does not have
(ADR-0004, ADR-0005, ADR-0006):

| Need | Runtime today | Gap |
|---|---|---|
| Write YAML, sorted and quoted | `rho_yaml_write_flat` | none |
| Read YAML of nested maps | `rho_yaml_flat`; skips lines it cannot place | a line check, doable in Rho in Mesh |
| Write JSON | `json_string` for literals | nesting flat keys into objects: doable in Rho, easier in Go |
| Read JSON | `json_str_field` (first match, no parse) | **no parser** |
| Seal and open with a passphrase | `rho_hmac_sha256_hex`, `rho_random_hex` | **no cipher, no KDF** |
| Name a file by its content | `rho_hmac_sha256_hex` | no plain SHA-256 (an HMAC under a fixed key does) |
| Take an upload | form bodies (`form_field`), raw `request_body`, 16 MiB cap (`RHO_HTTP_MAX_BODY`) | no multipart, not needed |
| Offer a download | `Response` with a header (`Content-Disposition`) | none |

Mesh already has a way to give itself what the runtime lacks without changing
the toolchain: a Go port in its own tree, provided as a plugin of the project's
tree (`integration.provides.ports`) and asked for in its manifest's `ports:`.
The telemetry store is one (ADR-0003). The maintainer allowed extending rho
"if really, really necessary".

## Decision

- **No change to rho.** Everything missing is provided by a new Go port in Mesh's
  repository, `plugins/mesh-projectfile`, port `projectfile`, released with Mesh
  as `mesh-telemetry` is, and added to `ports:` in Mesh's manifest. Its functions,
  declared by Mesh with `extern fn`, take and return strings:
  - `projectfile_json_flat(text) string` — a JSON document of nested objects as
    the same `a.b.c=value` lines `rho_yaml_flat` gives, numbers and booleans as
    their text; arrays, `null` and duplicate keys refused, with an error line
    naming the path.
  - `projectfile_flat_json(flat) string` — those lines as a JSON document, keys
    sorted as `rho_yaml_write_flat` sorts them, indented.
  - `projectfile_seal(passphrase, plain, aad) string` and
    `projectfile_open(passphrase, sealed_fields, aad) string` — AES-256-GCM under
    PBKDF2-HMAC-SHA256 (600 000 iterations, 16-byte salt, 12-byte nonce, from
    `crypto/rand`), from the Go standard library only; open answers "" on any
    failure.
  - `projectfile_digest(text) string` — SHA-256 as hex, the name a preview gives
    the file it was made from.
- **YAML stays with the runtime.** Export uses `rho_yaml_write_flat`; import
  checks lines in Rho (Mesh's domain module) and then reads with
  `rho_yaml_flat`, so plan.yml and the project file are read and written by the
  same code.
- The port holds no state and touches no file; Mesh's Rho code does every read,
  write and permission.

## Consequences

- No toolchain release is needed; the feature ships in a Mesh release.
- Mesh's Go port is built with Mesh's Go target only, which is the only target
  Mesh has.
- A second application that wants the same (the keeper backing up every
  application, say) would copy the port or promote it; at that point
  `rho_json_flat` and a sealing pair would be worth proposing for the runtime,
  symmetric with `rho_yaml_flat` — recorded here as the future rho change, not
  made now.
- The port has Go unit tests of its own (round trip, wrong passphrase, altered
  ciphertext, altered associated data, JSON refusals), unlike the telemetry
  store's current gap (ADR-0003).

## Alternatives considered

- **Add `rho_json_flat`, `rho_json_write_flat`, `rho_seal_hex` and
  `rho_open_hex` to the runtime.** Clean and reusable, but a toolchain release
  and a public builtin contract for one application's need. Deferred until a
  second user exists.
- **A JSON parser in Rho, in Mesh.** Possible (objects of strings), but slower,
  larger and less trustworthy than `encoding/json`, and sealing would still need
  Go. Rejected.
- **YAML only, no JSON.** Meets the request partly ("a yml or json file"); kept
  as the fallback if the maintainer prefers no port for JSON — the sealing still
  needs one.
- **Multipart upload support in rho.** Not needed: the page reads the chosen file
  into a form field and the API takes the file in a form body.
