---
id: ADR-0005
title: Seal a mesh's secrets with a passphrase, and leave them out by default
status: proposed
date: 2026-11-19
requirements: [MESH-0026, MESH-0028, MESH-XFER-0009, MESH-XFER-0010, MESH-XFER-0011, MESH-XFER-0012, MESH-XFER-0013, MESH-XFER-0019]
supersedes: []
superseded_by: []
---

# Seal a mesh's secrets with a passphrase, and leave them out by default

## Context

Moving a mesh to another Mesh and keeping its flashed boards working needs
what those boards were built with. A board's firmware carries its id, its key
`HMAC-SHA256(K_gw, "rh-dev1" | uid)[0:16]` (or the epoch form), the gateway's
key scope, its area and node number, and — on a network link — the gateway's
host and uplink port and the Wi-Fi SSID and password (MESH-ACCESS-0002,
`build.rho`). Mesh stores no board key: every board key derives from the
mesh's 32-byte `gateway.key` (MESH-ACCESS-0001), and a relay's `relay.key`
derives from its parent's. So the secrets a mesh has are few: the gateway key,
the Wi-Fi passwords in the plan, the relay key (derivable), the production
signing-key path (`signing-key.txt`, a file of the machine, MESH-BUILD-0010),
and a legacy `gateway_token`. Token records and registered clients belong to the
installation's keeper, not to the mesh.

A key rotation cannot replace a lost gateway key: the gateway wraps the new
board key under the current one (REKEY, robohub:ADR-0010), and only the old
gateway key derives the current one. A mesh imported with a new gateway key
therefore needs every board built and flashed again — the same as a board moved
to another mesh (MESH-FLEET-0003).

The security model asks that every secret on disk be its owner's alone and
never on a command line (SYS-0033, MESH-0008, MESH-ACCESS-0009), that Mesh hold
no signing key of the keeper (MESH-0020, robohub:ADR-0003), and that a leaked
key be replaceable (SYS-0011, SYS-0022). An exported file leaves Mesh's 0700
tree: it lands in a downloads folder, a mail or a git repository.

What cryptography a Mesh program has today:

- The Rho runtime: `rho_hmac_sha256_hex`, `rho_random_hex` (OS randomness, no
  fallback), `rho_write_secret` (0600, atomic). No cipher, no KDF.
- rho-auth (a component, not linked into applications) uses Go's
  `crypto/pbkdf2` for its passwords (PBKDF2-HMAC-SHA256, 600 000 iterations).
- Mesh's own Go port, `plugins/mesh-telemetry`, and the rho-robohub plugin have
  no encryption. A Go port has the whole Go standard library: `crypto/aes`,
  `crypto/cipher` (GCM), `crypto/pbkdf2`, `crypto/rand`.
- `openssl enc` exists on most machines but offers no authenticated mode with a
  KDF and would be one more program Mesh depends on.

Options:

1. **Leave secrets out, always.** Safe to share; the importing Mesh makes a new
   gateway key and every board is rebuilt and flashed. Fails the case the
   maintainer named: moving a mesh without touching its boards.
2. **Seal them under a passphrase.** The file is still safe to share without the
   passphrase; with it, a move keeps every board working.
3. **Include them in clear on explicit request, with a warning.** One forwarded
   file gives every board's key to whoever reads it; against SYS-0033's intent.
4. **Export by reference** — the file names the source Mesh, and the importing
   Mesh asks it for the key. Needs both online and a trust between two
   installations, whose keepers are separate issuers (SYS-0029). Not possible
   today.
5. **Re-key through epochs.** Not possible across gateway keys (above).

## Decision

- **Default: no secrets.** Every export leaves out the gateway key, relay key,
  Wi-Fi passwords, legacy token and signing-key path, and lists each one left out
  under `omitted:` by name, never by value (MESH-XFER-0009). Wi-Fi SSIDs, hosts,
  ports, ids and epochs are not secrets and are kept.
- **On request: sealed.** A person holding Mesh as a whole (ADR-0008) may ask for
  the secrets. Mesh then asks for a passphrase of at least 12 characters, typed
  twice, and writes the gateway key and the Wi-Fi passwords, as flat
  `key=value` lines, into `sealed.data`, encrypted with AES-256-GCM under a key
  from PBKDF2-HMAC-SHA256 (600 000 iterations, as rho-auth's passwords; 16-byte
  random salt; 12-byte random nonce), with `format|schema|mesh.id|exported.at` as
  associated data so a sealed block cannot be moved into another file
  (MESH-XFER-0010). `sealed.cipher`, `sealed.kdf`, `sealed.iterations`,
  `sealed.salt` and `sealed.nonce` say how; all values are hex. The relay key is
  never exported (it is derived again from the parent's key on import), nor the
  signing-key path.
- **The passphrase** comes only from a form field or a request body, is never
  written, logged, put in a URL, a command line or an environment
  (MESH-XFER-0011). Sealing and opening happen in Mesh's own process, in the Go
  port of ADR-0007.
- **Opening** fails closed: a wrong passphrase or an altered file imports no
  secret and writes nothing; the person may import without the secrets instead
  (MESH-XFER-0012).
- **Without the key**, an imported mesh gets a new gateway key as any new mesh
  does, and every board and the gateway are never programmed; no rotation is
  attempted (MESH-XFER-0013).
- **An overwrite never replaces a gateway key** (MESH-XFER-0019), so
  MESH-ACCESS-0001 holds unchanged; a file whose sealed key differs is offered as
  a new mesh instead.

## Consequences

- The ordinary export is safe to commit and share; a sealed one is as safe as its
  passphrase, and still never safe to publish.
- A move keeps flashed boards working only with the sealed export and the same
  area, uplink port and numbers (ADR-0006).
- A forgotten passphrase is a rebuild and reflash of every board, nothing worse.
- Sealing needs a cipher the runtime lacks; it comes from Mesh's own Go port
  (ADR-0007), not from rho.
- A Wi-Fi board imported without its password must have it typed again before
  its build is useful; the preview lists those boards.

## Alternatives considered

- Options 1, 3, 4 and 5 above, rejected for the reasons given: 1 is kept as the
  default but cannot be the only form; 3 leaks; 4 needs a cross-installation trust
  that does not exist; 5 is impossible without the old key.
- **Seal with a construction built in Rho from HMAC** (PBKDF2 and an HMAC
  keystream with an HMAC tag). Possible with the runtime alone, but home-made
  cryptography where the standard library has AES-GCM. Rejected.
- **`openssl enc` through `rho_exec_args`.** No AEAD with a KDF, a dependency
  on the machine, and the passphrase would have to reach another program.
  Rejected.
- **Seal with the keeper's key or an installation key.** Opens nowhere else,
  which defeats moving to another installation. Rejected.
