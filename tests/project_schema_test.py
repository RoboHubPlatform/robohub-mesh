#!/usr/bin/env python3
"""The published schema of a Mesh project file and Mesh's own rules, held together.

Mesh writes the rules for a project file out twice (ADR-0004): as JSON Schema
2020-12, published as schemas/mesh-project.v1.json for editors and other tools,
and in Rho, in src/mesh/domain/project_file.rho, which is what the exporter writes
by and the import checks by. This test holds the two to each other:

- every key the Rho lists name (the file's own keys, the plan's definition keys,
  a node's fields, the names `omitted:` may list) is a property of the schema at
  the same place, and the schema has no property they do not name;
- each key's class in Rho is the `$defs` entry the schema refers it to;
- every sample value of PROJECT_CLASS_SAMPLES — the lines the Rho unit tests hold
  project_value_ok to — is taken or refused by the schema's pattern for its class
  exactly as the sample says.

It also carries a small validator for the subset of JSON Schema the file uses,
which the project-file smoke test runs real exports through.

    python3 tests/project_schema_test.py
"""

import json
import re
import sys
from pathlib import Path

MESH = Path(__file__).resolve().parents[1]
RULES = MESH / "src" / "mesh" / "domain" / "project_file.rho"
SCHEMA = MESH / "schemas" / "mesh-project.v1.json"
ROW = "<row>"


def rho_constants(path: Path = RULES) -> dict:
    """The string constants of a Rho file, their escapes read."""
    found = {}
    for match in re.finditer(r'^const (\w+): string = "((?:[^"\\]|\\.)*)"', path.read_text(), re.M):
        found[match.group(1)] = json.loads('"' + match.group(2) + '"')
    return found


def rho_classes(constants: dict) -> dict:
    """Every key a project file may hold, as a path with `<row>` for a node row,
    and the class Rho gives it."""
    classes = {}

    def table(name):
        return dict(word.split(":", 1) for word in constants[name].split())

    for key, value in table("PROJECT_FILE_KEYS").items():
        classes[key] = value
    for key, value in table("PROJECT_PLAN_KEYS").items():
        classes["plan." + key] = value
    for key, value in table("PROJECT_NODE_FIELDS").items():
        classes[f"plan.node.{ROW}.{key}"] = value
    for key in constants["PROJECT_OMITTED_KEYS"].split():
        classes["omitted." + key] = "text"
    classes[f"omitted.node.{ROW}.password"] = "text"
    return classes


def schema_classes(schema: dict) -> dict:
    """Every property of the schema, as a path, and the `$defs` class it refers to."""
    classes = {}

    def walk(node, path):
        if "$ref" in node:
            classes[path] = node["$ref"].rsplit("/", 1)[-1]
            return
        for key, child in node.get("properties", {}).items():
            walk(child, f"{path}.{key}" if path else key)
        for pattern, child in node.get("patternProperties", {}).items():
            if pattern != "^[1-9][0-9]*$":
                raise AssertionError(f"{path}: a row is ^[1-9][0-9]*$, not {pattern}")
            walk(child, f"{path}.{ROW}")

    walk(schema, "")
    return classes


def class_pattern(schema: dict, name: str):
    definition = schema["$defs"][name]
    if "const" in definition:
        return re.compile("^" + re.escape(definition["const"]) + "$")
    return re.compile(definition["pattern"])


def samples(constants: dict):
    for line in constants["PROJECT_CLASS_SAMPLES"].splitlines():
        if line:
            name, value, wanted = line.split(";")
            yield name, value.replace("\\t", "\t"), wanted == "1"


# A validator for what the file uses of JSON Schema: type object and string,
# properties, patternProperties, additionalProperties false, required, $ref into
# $defs, const and pattern.
def validate(instance, schema: dict, node: dict = None, path: str = "") -> list:
    node = schema if node is None else node
    if "$ref" in node:
        return validate(instance, schema, schema["$defs"][node["$ref"].rsplit("/", 1)[-1]], path)
    problems = []
    kind = node.get("type")
    shown = path or "(top)"
    if kind == "object":
        if not isinstance(instance, dict):
            return [f"{shown}: not an object"]
        for key in node.get("required", []):
            if key not in instance:
                problems.append(f"{shown}: {key} is missing")
        for key, value in instance.items():
            child = f"{path}.{key}" if path else key
            if key in node.get("properties", {}):
                problems += validate(value, schema, node["properties"][key], child)
                continue
            matched = False
            for pattern, sub in node.get("patternProperties", {}).items():
                if re.search(pattern, key):
                    matched = True
                    problems += validate(value, schema, sub, child)
            if not matched and node.get("additionalProperties") is False:
                problems.append(f"{child}: not a property the schema has")
        return problems
    if kind == "string":
        if not isinstance(instance, str):
            return [f"{shown}: not a string"]
        if "const" in node and instance != node["const"]:
            problems.append(f"{shown}: not {node['const']}")
        if "pattern" in node and not re.search(node["pattern"], instance):
            problems.append(f"{shown}: {instance!r} does not match {node['pattern']}")
        return problems
    return [f"{shown}: the validator does not know type {kind}"]


def nest(flat: dict) -> dict:
    """Flat `a.b.c` keys as nested objects, as a JSON export is."""
    tree = {}
    for key, value in flat.items():
        node = tree
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return tree


def check(ok: bool, what: str, failures: list) -> None:
    print(("[PASS] " if ok else "[FAIL] ") + what)
    if not ok:
        failures.append(what)


def main() -> int:
    failures = []
    schema = json.loads(SCHEMA.read_text())
    constants = rho_constants()
    check(schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema",
          "the schema is JSON Schema 2020-12", failures)
    check(schema.get("$id", "").endswith("/mesh-project.v1.json") and
          schema["properties"]["schema"]["$ref"] == "#/$defs/schema" and
          schema["$defs"]["format"].get("const") == constants["PROJECT_FORMAT"],
          "it is the schema of version 1 of the format Mesh writes", failures)

    rho = rho_classes(constants)
    published = schema_classes(schema)
    only_rho = sorted(set(rho) - set(published))
    only_schema = sorted(set(published) - set(rho))
    check(not only_rho, f"every key Mesh writes or reads is in the schema {only_rho or ''}", failures)
    check(not only_schema, f"the schema has no key Mesh does not know {only_schema or ''}", failures)
    differ = sorted(f"{key}: Rho {rho[key]}, schema {published[key]}"
                    for key in set(rho) & set(published) if rho[key] != published[key])
    check(not differ, f"every key has the same class in both {differ or ''}", failures)

    names = {name for name, _, _ in samples(constants)}
    used = set(rho.values())
    check(used <= names, f"every class a key takes has samples {sorted(used - names) or ''}", failures)
    check(names <= set(schema["$defs"]), f"every class is in $defs {sorted(names - set(schema['$defs'])) or ''}", failures)
    wrong = []
    for name, value, wanted in samples(constants):
        if name in schema["$defs"] and bool(class_pattern(schema, name).search(value)) != wanted:
            wrong.append(f"{name} [{value}] should be {'taken' if wanted else 'refused'}")
    check(not wrong, f"the schema's patterns take and refuse each sample as Rho does {wrong or ''}", failures)

    # A file as an export writes it validates; one with an unknown key does not.
    example = nest({
        "format": "robohub-mesh-project", "schema": "1", "exported.by": "robohub-mesh 0.21.0",
        "exported.at": "2026-11-19T10:00:00Z", "mesh.id": "garden", "mesh.name": "Garden",
        "mesh.area": "3", "mesh.rest_port": "8081", "mesh.uplink_port": "5002",
        "plan.gateway": "native", "plan.node.count": "1", "plan.node.1.number": "1",
        "plan.node.1.id": "0123456789abcdef", "plan.node.1.parent": "gateway",
        "omitted.gateway_key": "the gateway key", "omitted.node.1.password": "the Wi-Fi password",
    })
    check(validate(example, schema) == [], f"an export validates {validate(example, schema)}", failures)
    example["plan"]["node"]["1"]["password"] = "secret"
    check(any("password" in p for p in validate(example, schema)), "a password in clear does not", failures)

    print("mesh project schema test " + ("failed" if failures else "ok"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
