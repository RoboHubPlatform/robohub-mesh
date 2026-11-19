package main

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

// The tests of the projectfile port (ADR-0007): sealing round trips and fails
// closed, JSON is read as the flat lines the YAML reader gives and refused where
// those lines cannot hold it, flat lines are written as sorted JSON, and the
// digest is SHA-256.

const testPassphrase = "correct horse battery"
const testAAD = "robohub-mesh-project|1|garden|2026-11-19T10:00:00Z"

func TestSealRoundTrip(t *testing.T) {
	plain := "gateway_key=" + strings.Repeat("ab", 32) + "\nplan.node.2.password=hunter2 hunter2\n"
	sealed := projectfileSeal(testPassphrase, plain, testAAD)
	if sealed == "" {
		t.Fatal("seal answered nothing")
	}
	if strings.Contains(sealed, "hunter2") || strings.Contains(sealed, strings.Repeat("ab", 32)) {
		t.Fatal("a secret is in the sealed block in clear")
	}
	if got := projectfileOpen(testPassphrase, sealed, testAAD); got != plain {
		t.Fatalf("open gave %q, want %q", got, plain)
	}
}

func TestSealParameters(t *testing.T) {
	sealed := projectfileSeal(testPassphrase, "x=1\n", testAAD)
	fields := map[string]string{}
	for _, line := range strings.Split(strings.TrimSpace(sealed), "\n") {
		key, value, _ := strings.Cut(line, "=")
		fields[key] = value
	}
	if fields["cipher"] != "aes-256-gcm" || fields["kdf"] != "pbkdf2-hmac-sha256" || fields["iterations"] != "600000" {
		t.Fatalf("the block names other parameters: %v", fields)
	}
	salt, _ := hex.DecodeString(fields["salt"])
	nonce, _ := hex.DecodeString(fields["nonce"])
	if len(salt) != 16 || len(nonce) != 12 {
		t.Fatalf("salt %d bytes, nonce %d bytes; want 16 and 12", len(salt), len(nonce))
	}
	again := projectfileSeal(testPassphrase, "x=1\n", testAAD)
	if again == sealed {
		t.Fatal("two seals of the same text are the same: the salt and nonce are not random")
	}
}

func TestSealRefusesAShortPassphrase(t *testing.T) {
	if projectfileSeal("elevenchars", "x=1\n", testAAD) != "" {
		t.Fatal("an 11-character passphrase sealed")
	}
	if projectfileSeal("twelve chars", "x=1\n", testAAD) == "" {
		t.Fatal("a 12-character passphrase did not seal")
	}
}

func TestOpenFailsClosed(t *testing.T) {
	plain := "gateway_key=" + strings.Repeat("cd", 32) + "\n"
	sealed := projectfileSeal(testPassphrase, plain, testAAD)
	if projectfileOpen("wrong horse battery", sealed, testAAD) != "" {
		t.Fatal("a wrong passphrase opened the block")
	}
	if projectfileOpen(testPassphrase, sealed, strings.Replace(testAAD, "garden", "orchard", 1)) != "" {
		t.Fatal("the block opened under another mesh id: it can be moved into another file")
	}
	// One flipped bit of the ciphertext.
	altered := strings.Builder{}
	for _, line := range strings.Split(sealed, "\n") {
		if strings.HasPrefix(line, "data=") {
			data, _ := hex.DecodeString(strings.TrimPrefix(line, "data="))
			data[0] ^= 1
			line = "data=" + hex.EncodeToString(data)
		}
		altered.WriteString(line + "\n")
	}
	if projectfileOpen(testPassphrase, altered.String(), testAAD) != "" {
		t.Fatal("an altered ciphertext opened")
	}
	if projectfileOpen(testPassphrase, strings.Replace(sealed, "iterations=600000", "iterations=1", 1), testAAD) != "" {
		t.Fatal("a block naming other iterations was opened")
	}
	if projectfileOpen(testPassphrase, "", testAAD) != "" {
		t.Fatal("an empty block opened")
	}
}

func TestJSONReadsAsFlatLines(t *testing.T) {
	doc := `{"format": "robohub-mesh-project", "schema": 1, "plan": {"node": {"1": {"board": "pico2w", "number": "1"}, "count": "1"},
		"gateway": {"_": "native", "x": true}}}`
	got := projectfileJsonFlat(doc)
	for _, want := range []string{"format=robohub-mesh-project\n", "schema=1\n", "plan.node.1.board=pico2w\n",
		"plan.node.count=1\n", "plan.gateway=native\n", "plan.gateway.x=true\n"} {
		if !strings.Contains(got, want) {
			t.Errorf("the flat lines %q lack %q", got, want)
		}
	}
	if strings.HasPrefix(got, "!") {
		t.Fatalf("a good document was refused: %s", got)
	}
}

func TestJSONRefusals(t *testing.T) {
	cases := map[string]string{
		`{"plan": {"nodes": [1, 2]}}`:      "!plan.nodes: a list",
		`{"plan": {"name": null}}`:         "!plan.name: null",
		`{"mesh": {"id": "a", "id": "b"}}`: "!mesh.id: the key is given twice",
		`{"a.b": "x"}`:                     "holds '.'",
		`{"mesh": {"name": "two\nlines"}}`: "!mesh.name: a value holds a line break",
		`["not", "an", "object"]`:          "!: a project file is a JSON object",
		`{"format": "x"} {"again": "y"}`:   "!: text after the document",
		`{"format": "x",}`:                 "!",
		`not json at all`:                  "!: not a JSON document",
		`{"_": "top"}`:                     "the key \"_\" is only a value",
	}
	for doc, want := range cases {
		got := projectfileJsonFlat(doc)
		if !strings.HasPrefix(got, "!") || !strings.Contains(got, want) {
			t.Errorf("%s: got %q, want a refusal with %q", doc, got, want)
		}
	}
}

func TestFlatLinesWriteSortedJSON(t *testing.T) {
	flat := "plan.node.10.name=Ten\nplan.node.2.name=Two\nformat=robohub-mesh-project\nplan.gateway=native\nplan.gateway.x=1\nmesh.name=Ünïcode & <html>\n"
	got := projectfileFlatJson(flat)
	var tree map[string]any
	if err := json.Unmarshal([]byte(got), &tree); err != nil {
		t.Fatalf("not JSON: %v\n%s", err, got)
	}
	if strings.Index(got, `"2"`) > strings.Index(got, `"10"`) {
		t.Fatalf("numeric keys are not in number order:\n%s", got)
	}
	if strings.Index(got, `"format"`) > strings.Index(got, `"mesh"`) || strings.Index(got, `"mesh"`) > strings.Index(got, `"plan"`) {
		t.Fatalf("keys are not sorted:\n%s", got)
	}
	if !strings.Contains(got, `"_": "native"`) || strings.Index(got, `"_"`) > strings.Index(got, `"x"`) {
		t.Fatalf("a key that is a value and an object does not hold its value first under _:\n%s", got)
	}
	if !strings.Contains(got, "Ünïcode & <html>") {
		t.Fatalf("text is escaped more than JSON needs:\n%s", got)
	}
	// Read back, the same lines, whatever their order.
	back := projectfileJsonFlat(got)
	for _, line := range strings.Split(strings.TrimSpace(flat), "\n") {
		if !strings.Contains(back, line+"\n") {
			t.Errorf("the round trip lost %q:\n%s", line, back)
		}
	}
	if projectfileFlatJson(flat) != got {
		t.Fatal("two writes of the same lines differ")
	}
}

func TestDigestIsSHA256(t *testing.T) {
	if got := projectfileDigest("abc"); got != "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad" {
		t.Fatalf("digest of abc is %s", got)
	}
}

// What the port promises Mesh is what it has: the functions Mesh declares, from
// the standard library alone, and nothing that writes a file or logs.
func TestPortTouchesNoFileAndLogsNothing(t *testing.T) {
	data, err := os.ReadFile("projectfile.go")
	if err != nil {
		t.Fatal(err)
	}
	body := string(data)
	for _, want := range []string{
		"func projectfileJsonFlat(text string) string", "func projectfileFlatJson(flat string) string",
		"func projectfileSeal(passphrase string, plain string, aad string) string",
		"func projectfileOpen(passphrase string, fields string, aad string) string",
		"func projectfileDigest(text string) string", "\"crypto/pbkdf2\"",
	} {
		if !strings.Contains(body, want) {
			t.Errorf("the port does not contain %q", want)
		}
	}
	for _, banned := range []string{"\"os\"", "\"log\"", "\"fmt\"", "\"os/exec\"", "\"net/http\"", "golang.org/"} {
		if strings.Contains(body, banned) {
			t.Errorf("the port imports %s", banned)
		}
	}
}
