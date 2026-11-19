# mesh-projectfile

What a Mesh project file needs that the Rho runtime does not have, as a Go port
Mesh is built with (`ports: [projectfile]` in Mesh's manifest). A project file
carries one mesh's definition as YAML or JSON, with its secrets left out or
sealed under a passphrase; see Mesh's guide for what is in one and how it is
imported. The port lives in Mesh's repository and is versioned with Mesh, so its
changes are recorded in Mesh's CHANGELOG (ADR-0007).

## What it does

| Function | What it answers |
|---|---|
| `projectfile_json_flat(text)` | A JSON document of nested objects as the `a.b.c=value` lines `rho_yaml_flat` gives, numbers and booleans as their text, a key `_` as its object's own value. A list, a `null`, a key given twice, a key that is empty or holds `.`, `=` or a line break, a value with a line break, text after the document and a syntax error are answered as lines starting with `!` that name the path |
| `projectfile_flat_json(flat)` | Those lines as a JSON document of string values, indented, each object's keys in the order `rho_yaml_write_flat` writes them (a numeric key by its number) |
| `projectfile_seal(passphrase, plain, aad)` | `plain` encrypted with AES-256-GCM under a key PBKDF2-HMAC-SHA256 derives from the passphrase (600 000 iterations, a random 16-byte salt), a random 12-byte nonce, `aad` bound to it; as the lines `cipher`, `data`, `iterations`, `kdf`, `nonce` and `salt`. Nothing for a passphrase shorter than 12 characters |
| `projectfile_open(passphrase, fields, aad)` | The plain text, or nothing on any failure: a wrong passphrase, an altered block or associated data, or parameters other than those above |
| `projectfile_digest(text)` | The SHA-256 of a text, as hex: the name an import preview gives the file it was made from |

It uses the Go standard library alone (`crypto/aes`, `crypto/cipher`,
`crypto/pbkdf2`, `crypto/rand`, `crypto/sha256`, `encoding/json`), holds no
state, reads and writes no file and logs nothing: Mesh's own code does every
read, write and permission, and a passphrase or a secret never leaves the call
it was handed to.

## Tests

`go -C plugins/mesh-projectfile/ports test ./...`, run by Mesh's
`projectfile-port` suite: a sealed round trip, a wrong passphrase, an altered
ciphertext, other associated data and other parameters refused, the passphrase
length, JSON read as flat lines and every refusal, flat lines written as sorted
JSON and read back, and the digest.
