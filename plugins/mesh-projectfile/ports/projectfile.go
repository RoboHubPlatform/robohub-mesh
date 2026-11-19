// The projectfile port: what a Mesh project file needs that the Rho runtime does
// not have (ADR-0007).
//
// A project file is a mesh's definition carried as YAML or JSON (ADR-0004), with
// its secrets left out or sealed under a passphrase (ADR-0005). Mesh writes and
// reads the YAML with the runtime's own flat YAML functions; this port gives it
// the rest, each as a function of strings:
//
//   - projectfile_json_flat reads a JSON document of nested objects as the same
//     `a.b.c=value` lines rho_yaml_flat gives, and refuses what those lines cannot
//     hold (a list, a null, a duplicate key, a key with a dot), naming the path;
//   - projectfile_flat_json writes such lines as a JSON document, keys sorted as
//     rho_yaml_write_flat sorts them;
//   - projectfile_seal and projectfile_open seal and open the secrets with
//     AES-256-GCM under a key PBKDF2-HMAC-SHA256 derives from a passphrase;
//   - projectfile_digest names a file by its SHA-256, the name a preview gives
//     the file it was made from.
//
// The port holds no state and touches no file: Mesh's Rho code does every read,
// write and permission. Nothing here logs, so a passphrase or a secret never
// leaves the call it was given to.
//
// A project of the robohub platform asks for it by name (`ports: [projectfile]`).
package main

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/pbkdf2"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

// The sealing parameters of schema 1. A sealed block names them, and a block that
// names others is not opened: a file must not choose how much work opening it is.
const (
	projectfileCipher        = "aes-256-gcm"
	projectfileKDF           = "pbkdf2-hmac-sha256"
	projectfileIterations    = 600000
	projectfileSaltBytes     = 16
	projectfileNonceBytes    = 12
	projectfileKeyBytes      = 32
	projectfileMinPassphrase = 12
	projectfileMaxDepth      = 16
)

// projectfileDigest is the SHA-256 of a text, as 64 hex digits.
func projectfileDigest(text string) string {
	sum := sha256.Sum256([]byte(text))
	return hex.EncodeToString(sum[:])
}

// projectfileKey derives the sealing key from a passphrase and a salt.
func projectfileKey(passphrase string, salt []byte) ([]byte, bool) {
	key, err := pbkdf2.Key(sha256.New, passphrase, salt, projectfileIterations, projectfileKeyBytes)
	if err != nil {
		return nil, false
	}
	return key, true
}

func projectfileAEAD(key []byte) (cipher.AEAD, bool) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, false
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, false
	}
	return aead, true
}

// projectfileSeal encrypts `plain` under a passphrase of at least 12 characters,
// with `aad` bound to it, and answers the sealed block as flat lines: cipher,
// data, iterations, kdf, nonce and salt, every value hex or a name. "" when the
// passphrase is too short or no random value can be had.
func projectfileSeal(passphrase string, plain string, aad string) string {
	if utf8.RuneCountInString(passphrase) < projectfileMinPassphrase {
		return ""
	}
	salt := make([]byte, projectfileSaltBytes)
	nonce := make([]byte, projectfileNonceBytes)
	if _, err := io.ReadFull(rand.Reader, salt); err != nil {
		return ""
	}
	if _, err := io.ReadFull(rand.Reader, nonce); err != nil {
		return ""
	}
	key, ok := projectfileKey(passphrase, salt)
	if !ok {
		return ""
	}
	aead, ok := projectfileAEAD(key)
	if !ok {
		return ""
	}
	sealed := aead.Seal(nil, nonce, []byte(plain), []byte(aad))
	return "cipher=" + projectfileCipher + "\n" +
		"data=" + hex.EncodeToString(sealed) + "\n" +
		"iterations=" + strconv.Itoa(projectfileIterations) + "\n" +
		"kdf=" + projectfileKDF + "\n" +
		"nonce=" + hex.EncodeToString(nonce) + "\n" +
		"salt=" + hex.EncodeToString(salt) + "\n"
}

// projectfileOpen opens a sealed block (the flat lines projectfileSeal answers)
// with a passphrase and the associated data the block was sealed with. It
// answers "" on any failure — a wrong passphrase, an altered block, other
// associated data, other parameters — because none of them may be told apart.
func projectfileOpen(passphrase string, fields string, aad string) string {
	values := map[string]string{}
	for _, line := range strings.Split(fields, "\n") {
		key, value, found := strings.Cut(line, "=")
		if !found || key == "" {
			continue
		}
		if _, seen := values[key]; seen {
			return ""
		}
		values[key] = value
	}
	if values["cipher"] != projectfileCipher || values["kdf"] != projectfileKDF ||
		values["iterations"] != strconv.Itoa(projectfileIterations) {
		return ""
	}
	salt, err := hex.DecodeString(values["salt"])
	if err != nil || len(salt) != projectfileSaltBytes {
		return ""
	}
	nonce, err := hex.DecodeString(values["nonce"])
	if err != nil || len(nonce) != projectfileNonceBytes {
		return ""
	}
	data, err := hex.DecodeString(values["data"])
	if err != nil || len(data) == 0 {
		return ""
	}
	if utf8.RuneCountInString(passphrase) < projectfileMinPassphrase {
		return ""
	}
	key, ok := projectfileKey(passphrase, salt)
	if !ok {
		return ""
	}
	aead, ok := projectfileAEAD(key)
	if !ok {
		return ""
	}
	plain, err := aead.Open(nil, nonce, data, []byte(aad))
	if err != nil {
		return ""
	}
	return string(plain)
}

/* -------------------------------------------------------------------------- */
/*  JSON as flat lines                                                        */
/* -------------------------------------------------------------------------- */

type projectfileReader struct {
	decoder  *json.Decoder
	out      strings.Builder
	problems []string
}

// projectfileJsonFlat reads a JSON document of nested objects as `a.b.c=value`
// lines, a number or a boolean as its text, and a key `_` as its object's own
// value (as rho_yaml_flat reads one). A document it cannot read that way is
// answered as problem lines, each starting with `!` and naming the path: a list,
// a null, a duplicate key, a key that is empty or holds `.`, `=` or a line break,
// a value with a line break, text after the document, or a syntax error.
func projectfileJsonFlat(text string) string {
	reader := &projectfileReader{decoder: json.NewDecoder(strings.NewReader(text))}
	reader.decoder.UseNumber()
	token, err := reader.decoder.Token()
	if err != nil {
		return "!: not a JSON document: " + projectfileJSONError(err) + "\n"
	}
	if delim, ok := token.(json.Delim); !ok || delim != '{' {
		return "!: a project file is a JSON object\n"
	}
	if !reader.object("", 1) {
		return strings.Join(reader.problems, "")
	}
	if _, err := reader.decoder.Token(); err != io.EOF {
		reader.problems = append(reader.problems, "!: text after the document\n")
	}
	if len(reader.problems) > 0 {
		return strings.Join(reader.problems, "")
	}
	return reader.out.String()
}

func projectfileJSONError(err error) string {
	return strings.ReplaceAll(err.Error(), "\n", " ")
}

func projectfileJoin(path, key string) string {
	if key == "_" {
		return path
	}
	if path == "" {
		return key
	}
	return path + "." + key
}

func projectfileShown(path string) string {
	if path == "" {
		return "(top)"
	}
	return path
}

// object reads the members of an object whose `{` was read; false on a syntax
// error, after which nothing more can be read.
func (r *projectfileReader) object(path string, depth int) bool {
	if depth > projectfileMaxDepth {
		r.problems = append(r.problems, "!"+projectfileShown(path)+": nested deeper than "+strconv.Itoa(projectfileMaxDepth)+" objects\n")
		return false
	}
	seen := map[string]bool{}
	for {
		token, err := r.decoder.Token()
		if err != nil {
			r.problems = append(r.problems, "!"+projectfileShown(path)+": "+projectfileJSONError(err)+"\n")
			return false
		}
		if delim, ok := token.(json.Delim); ok && delim == '}' {
			return true
		}
		key, ok := token.(string)
		if !ok {
			r.problems = append(r.problems, "!"+projectfileShown(path)+": a key must be a string\n")
			return false
		}
		child := projectfileJoin(path, key)
		if key == "" || strings.ContainsAny(key, ".=\n\r") {
			r.problems = append(r.problems, "!"+projectfileShown(path)+": the key "+strconv.Quote(key)+" is empty or holds '.', '=' or a line break\n")
		} else if key == "_" && path == "" {
			r.problems = append(r.problems, "!(top): the key \"_\" is only a value of an object below the top\n")
		} else if seen[key] {
			r.problems = append(r.problems, "!"+child+": the key is given twice\n")
		}
		seen[key] = true
		value, err := r.decoder.Token()
		if err != nil {
			r.problems = append(r.problems, "!"+child+": "+projectfileJSONError(err)+"\n")
			return false
		}
		switch typed := value.(type) {
		case json.Delim:
			if typed == '{' {
				if !r.object(child, depth+1) {
					return false
				}
				continue
			}
			r.problems = append(r.problems, "!"+child+": a list is not allowed; a project file holds objects of values\n")
			if !r.skip(1) {
				return false
			}
		case nil:
			r.problems = append(r.problems, "!"+child+": null is not allowed; leave the key out\n")
		case string:
			r.value(child, typed)
		case json.Number:
			r.value(child, typed.String())
		case bool:
			r.value(child, strconv.FormatBool(typed))
		default:
			r.problems = append(r.problems, "!"+child+": a value this file cannot hold\n")
		}
	}
}

func (r *projectfileReader) value(path, value string) {
	if strings.ContainsAny(value, "\n\r") {
		r.problems = append(r.problems, "!"+path+": a value holds a line break\n")
		return
	}
	r.out.WriteString(path)
	r.out.WriteString("=")
	r.out.WriteString(value)
	r.out.WriteString("\n")
}

// skip reads past the rest of a list or object whose opening was read.
func (r *projectfileReader) skip(depth int) bool {
	for depth > 0 {
		token, err := r.decoder.Token()
		if err != nil {
			return false
		}
		if delim, ok := token.(json.Delim); ok {
			if delim == '{' || delim == '[' {
				depth++
			} else {
				depth--
			}
		}
	}
	return true
}

/* -------------------------------------------------------------------------- */
/*  Flat lines as JSON                                                        */
/* -------------------------------------------------------------------------- */

type projectfileNode struct {
	value    string
	hasValue bool
	children map[string]*projectfileNode
}

// projectfileFlatJson writes `a.b.c=value` lines as a JSON document of nested
// objects of string values, indented by two spaces, the keys of each object in
// the order rho_yaml_write_flat writes them (a numeric key by its number). A key
// that is both a value and an object holds its value under `_`, first. A key
// given twice keeps its last value, as rho_yaml_write_flat does.
func projectfileFlatJson(flat string) string {
	root := &projectfileNode{children: map[string]*projectfileNode{}}
	for _, line := range strings.Split(flat, "\n") {
		eq := strings.Index(line, "=")
		if eq <= 0 {
			continue
		}
		key := strings.TrimSpace(line[:eq])
		node := root
		for _, part := range strings.Split(key, ".") {
			next, ok := node.children[part]
			if !ok {
				next = &projectfileNode{children: map[string]*projectfileNode{}}
				node.children[part] = next
			}
			node = next
		}
		node.value = line[eq+1:]
		node.hasValue = true
	}
	var out bytes.Buffer
	projectfileWriteObject(&out, root, 0)
	out.WriteString("\n")
	return out.String()
}

func projectfileLess(a, b string) bool {
	// An object's own value comes before its members, as in the YAML.
	if a == "_" || b == "_" {
		return a == "_" && b != "_"
	}
	na, ea := strconv.Atoi(a)
	nb, eb := strconv.Atoi(b)
	if ea == nil && eb == nil {
		return na < nb
	}
	return a < b
}

func projectfileQuote(value string) string {
	var out bytes.Buffer
	encoder := json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	if encoder.Encode(value) != nil {
		return "\"\""
	}
	return strings.TrimRight(out.String(), "\n")
}

func projectfileWriteObject(out *bytes.Buffer, node *projectfileNode, depth int) {
	keys := make([]string, 0, len(node.children))
	for key := range node.children {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool { return projectfileLess(keys[i], keys[j]) })
	out.WriteString("{")
	indent := strings.Repeat("  ", depth+1)
	first := true
	member := func(key, rendered string) {
		if !first {
			out.WriteString(",")
		}
		first = false
		out.WriteString("\n" + indent + projectfileQuote(key) + ": " + rendered)
	}
	for _, key := range keys {
		child := node.children[key]
		if len(child.children) == 0 {
			member(key, projectfileQuote(child.value))
			continue
		}
		var nested bytes.Buffer
		if child.hasValue {
			child.children["_"] = &projectfileNode{value: child.value, hasValue: true, children: map[string]*projectfileNode{}}
		}
		projectfileWriteObject(&nested, child, depth+1)
		member(key, nested.String())
	}
	if !first {
		out.WriteString("\n" + strings.Repeat("  ", depth))
	}
	out.WriteString("}")
}
