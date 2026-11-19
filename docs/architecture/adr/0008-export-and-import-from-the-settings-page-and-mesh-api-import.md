---
id: ADR-0008
title: Export and import from the settings page and /mesh/api, importing with Mesh-wide grants
status: accepted
date: 2026-11-19
requirements: [MESH-0028, MESH-XFER-0024, MESH-XFER-0025, MESH-XFER-0026, MESH-XFER-0027, MESH-XFER-0028]
supersedes: []
superseded_by: []
---

# Export and import from the settings page and /mesh/api, importing with Mesh-wide grants

## Context

Who may do what in Mesh is decided by the `mesh:` grants a keeper ticket or
service token carries (MESH-0012, MESH-API-0001): reading a mesh needs `*`,
`mesh:*`, `mesh:read`, `mesh:<id>` or `mesh:<id>:read`; changing it `*`,
`mesh:*` or `mesh:<id>`; creating, copying, applying everywhere or changing
Mesh's own settings `*` or `mesh:*` (`mesh_grant_admin`). Browser posts carry
the forms module's token (`forms_csrf_ok`, FORMS-0006), API posts a bearer
token (`forms_machine_ok`, FORMS-0008), and a cross-origin form is refused
(MESH-0016). `/mesh/api/projects/{project}/...` is where applications read a
mesh. A mesh's settings page already holds rename, copy as template, tokens and
delete; the fleet page creates meshes.

Rho's HTTP server reads bodies up to 16 MiB and has no multipart parser; a
response can carry a header, so a download is a `Content-Disposition`.

A command-line surface (`rho mesh export`, or the keeper) was considered. The
plan's lock is `rho_lock`, which is per process: a second program writing
`plan.yml` while Mesh runs could lose an edit. Mesh's state belongs to Mesh's
user and process.

## Decision

- **Grants** (MESH-XFER-0024). Export without secrets: a read grant on the mesh —
  it shows nothing the mesh's pages do not. Export with sealed secrets and every
  import: `*` or `mesh:*`, since one carries the gateway key and the other
  creates or replaces a mesh. Refusals are 403, before the file is read.
- **Forms** (MESH-XFER-0025). The sealed export, the import preview and the import
  confirmation are posts: from a page with the form token, from a program with a
  bearer token. The plain export is a GET.
- **Pages** (MESH-XFER-0027). On a mesh's settings page, beside copy and
  delete: *Export* (YAML or JSON; "include secrets, sealed" with the passphrase
  twice) and *Import over this mesh*. On the fleet page, beside *New mesh*:
  *Import a mesh* (as new or as a copy, with an optional name). The file input is
  read by a few lines of script on the page into a hidden field of an ordinary
  form; the preview page lists everything ADR-0006 says, carries the file and
  its digest, and asks for the mesh's typed name for an overwrite. Strings are
  in Mesh's locales.
- **API** (MESH-XFER-0028). `GET /mesh/api/projects/{project}/export[?format=json]`;
  `POST /mesh/api/projects/{project}/export` (form body `passphrase`,
  `format`); `POST /mesh/api/projects/import` (form body `file`, `passphrase`,
  `mode`, `target`, `name`, `confirm`), answering the preview as JSON without
  `confirm`, and 422 / 409 / 412 / 413 with every problem listed. Answers to a
  sealed export carry `Cache-Control: no-store`.
- **Limits and names** (MESH-XFER-0026). A file over 1 MiB is answered 413; the
  download is named `<mesh-id>.mesh.yml` or `.mesh.json` from the checked id.
- **No CLI and no keeper surface now.** The API, with a service token and curl,
  covers backup and automation without a second writer of Mesh's files. A
  keeper-wide backup of every application is a system decision, not Mesh's.

## Consequences

- A person granted one mesh can back it up (without secrets) but not restore it;
  restoring is Mesh-wide.
- The page needs a small inline script for reading the file; Mesh's pages already
  use inline handlers (the delete confirmation).
- Every new endpoint needs Mesh's access tests (MESH-API-0006) extended.

## Alternatives considered

- **Import with `mesh:<id>` for an overwrite of that mesh.** Would let a holder of
  one mesh replace its manifest — and so admit boards — without Mesh-wide trust.
  Rejected for now; an open question for the maintainer.
- **Multipart upload.** Needs a parser in rho; reading the file in the page is
  enough.
- **`rho mesh` CLI.** Second writer of the plan outside the process lock; not
  needed while the API exists. Rejected for now.
