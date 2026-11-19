package main

import (
	"database/sql"
	"fmt"
	"os"
	"strings"
	"sync"

	_ "modernc.org/sqlite"
)

// What the telemetry port needs from the sql port.
//
// The telemetry port is written against the sql port, which the generator merges
// into the same file and which defines these. They are declared here, in a test
// file, so the port's source compiles and is type-checked where it lives, an
// editor can follow it, and the store's tests run against a real SQLite
// database. They are never generated: the generator reads only the non-test
// files in this directory, and the real definitions are the sql port's. If the
// sql port changes one of these signatures, the real build breaks and so does
// this file's copy once it is brought in line.
//
// The bodies do what the sql port's do, over database/sql and the driver the
// generated project registers (modernc's "sqlite"), with Mesh's own schema.

type SqlDb struct{ raw uint64 }

type ResultSqlDbString struct {
	isOk bool
	ok   SqlDb
}

type rhoSqlStatementBatch struct {
	SQL  string
	Rows [][]any
}

var rhoSqlLastErrMsg string

var (
	testSqlMu      sync.Mutex
	testSqlHandles = map[uint64]*sql.DB{}
	testSqlNextID  uint64
)

// testSqlSchemaPath is Mesh's schema, the file the sql port embeds.
const testSqlSchemaPath = "../../../db/schema.sql"

func stdSqlSqlOpen(path string) ResultSqlDbString {
	handle, err := sql.Open("sqlite", path+"?_pragma=busy_timeout(5000)")
	if err == nil {
		err = handle.Ping()
	}
	if err != nil {
		rhoSqlLastErrMsg = err.Error()
		return ResultSqlDbString{}
	}
	testSqlMu.Lock()
	defer testSqlMu.Unlock()
	testSqlNextID++
	testSqlHandles[testSqlNextID] = handle
	return ResultSqlDbString{isOk: true, ok: SqlDb{raw: testSqlNextID}}
}

func stdSqlSqlApply(db SqlDb, schema string) uint32 {
	handle, ok := rhoSqlHandle(db.raw)
	if !ok {
		return 0
	}
	if strings.TrimSpace(schema) == "" {
		raw, err := os.ReadFile(testSqlSchemaPath)
		if err != nil {
			rhoSqlLastErrMsg = err.Error()
			return 0
		}
		schema = string(raw)
	}
	created := uint32(0)
	for _, statement := range testSqlStatements(schema) {
		if _, err := handle.Exec(statement); err != nil {
			if strings.Contains(err.Error(), "already exists") || strings.Contains(err.Error(), "duplicate column name") {
				continue
			}
			rhoSqlLastErrMsg = err.Error()
			return created
		}
		created++
	}
	return created
}

// testSqlStatements splits the schema at each semicolon outside a comment; the
// schema has no string literal that holds one.
func testSqlStatements(schema string) []string {
	var lines []string
	for _, line := range strings.Split(schema, "\n") {
		if at := strings.Index(line, "--"); at >= 0 {
			line = line[:at]
		}
		lines = append(lines, line)
	}
	var out []string
	for _, statement := range strings.Split(strings.Join(lines, "\n"), ";") {
		if statement = strings.TrimSpace(statement); statement != "" {
			out = append(out, statement)
		}
	}
	return out
}

func rhoSqlExecBatch(db SqlDb, name string, statements []rhoSqlStatementBatch) uint32 {
	handle, ok := rhoSqlHandle(db.raw)
	if !ok {
		return 0
	}
	fail := func(err error) uint32 {
		rhoSqlLastErrMsg = fmt.Sprintf("%s: %v", name, err)
		return 0
	}
	transaction, err := handle.Begin()
	if err != nil {
		return fail(err)
	}
	for _, statement := range statements {
		if len(statement.Rows) == 0 {
			continue
		}
		prepared, err := transaction.Prepare(statement.SQL)
		if err != nil {
			_ = transaction.Rollback()
			return fail(err)
		}
		for _, row := range statement.Rows {
			if _, err := prepared.Exec(row...); err != nil {
				_ = prepared.Close()
				_ = transaction.Rollback()
				return fail(err)
			}
		}
		_ = prepared.Close()
	}
	if err := transaction.Commit(); err != nil {
		return fail(err)
	}
	return 1
}

func rhoSqlHandle(raw uint64) (*sql.DB, bool) {
	testSqlMu.Lock()
	defer testSqlMu.Unlock()
	handle, ok := testSqlHandles[raw]
	return handle, ok
}
