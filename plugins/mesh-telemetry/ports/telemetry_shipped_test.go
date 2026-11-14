package main

import (
	"os"
	"strings"
	"testing"
)

// What Mesh relies on the telemetry port for is in it: moving a board's history
// between meshes, maintenance (checkpoint, vacuum, storage status) and faults.
func TestTelemetryPortCarriesWhatMeshCalls(t *testing.T) {
	data, err := os.ReadFile("telemetry.go")
	if err != nil {
		t.Fatal(err)
	}
	body := string(data)
	for _, want := range []string{
		"func telemetryMoveSource(", "telemetry_reading", "telemetry_rollup_1h", "telemetry_source",
		"func telemetryCheckpoint(mode string) string",
		"PRAGMA wal_checkpoint(",
		"LIMIT 5000",
		"func telemetryVacuum(mode string) string",
		"PRAGMA incremental_vacuum",
		"func telemetryStorageStatus(project string) string",
		"telemetry_fault",
		"func telemetryFaultMaxId(project string) uint32",
		"func telemetryFaultNew(project string, afterID uint32, limit uint32) string",
		"func telemetryFaultList(project string, sinceText string, limit uint32) string",
		"func telemetryFaultClear(project string, id uint32) string",
		"func telemetryFaultPrune(project string, days uint32) string",
		"func telemetryFaultRows(project string, limit uint32) string",
	} {
		if !strings.Contains(body, want) {
			t.Errorf("the telemetry port does not contain %q", want)
		}
	}
}
