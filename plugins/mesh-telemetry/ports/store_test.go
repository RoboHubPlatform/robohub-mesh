package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// The store's behaviour, against a real SQLite database with Mesh's schema and a
// stand-in gateway: what an outbox page does to the store, what a page read
// again or replayed does, how a reading finds its series, and the roll-up and
// retention passes.

// openStore gives a test a fresh store of its own.
func openStore(t *testing.T) {
	t.Helper()
	telemetryMu.Lock()
	telemetryReady = false
	telemetryDB = SqlDb{}
	telemetryMu.Unlock()
	telemetryCollisionMu.Lock()
	telemetryCollisions = map[string]int64{}
	telemetryCollisionMu.Unlock()
	if !telemetryOpen(filepath.Join(t.TempDir(), "telemetry.db")) {
		t.Fatalf("the store did not open: %s", rhoSqlLastErrMsg)
	}
}

// gateway is a stand-in robohub-gateway outbox. It answers the page it is
// given, after the cursor Mesh asks from unless it is told to replay, and
// records the acks it is sent and the tokens they carry.
type gateway struct {
	mu     sync.Mutex
	page   []map[string]any
	replay bool
	acks   []string
	tokens []string
	afters []string
	server *httptest.Server
}

func newGateway(t *testing.T) *gateway {
	g := &gateway{}
	g.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		g.mu.Lock()
		defer g.mu.Unlock()
		g.tokens = append(g.tokens, r.Header.Get("Authorization"))
		switch r.URL.Path {
		case "/api/v1/telemetry/outbox":
			after := r.URL.Query().Get("after")
			g.afters = append(g.afters, after)
			var cursor uint32
			fmt.Sscanf(after, "%d", &cursor)
			out := []map[string]any{}
			for _, record := range g.page {
				if g.replay || telemetryUint(record["seq"]) > cursor {
					out = append(out, record)
				}
			}
			_ = json.NewEncoder(w).Encode(out)
		case "/api/v1/telemetry/ack":
			body, _ := io.ReadAll(r.Body)
			g.acks = append(g.acks, string(body))
			w.WriteHeader(http.StatusNoContent)
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(g.server.Close)
	return g
}

func (g *gateway) serve(page []map[string]any, replay bool) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.page = page
	g.replay = replay
}

func (g *gateway) lastAck() string {
	g.mu.Lock()
	defer g.mu.Unlock()
	if len(g.acks) == 0 {
		return ""
	}
	return g.acks[len(g.acks)-1]
}

func reading(seq uint32, source string, area, node uint32, field string, value float64, status int64, received int64) map[string]any {
	record := map[string]any{
		"seq": float64(seq), "area_id": float64(area), "node_id": float64(node), "field": field,
		"value": value, "status": status, "received_ms": received,
	}
	if source != "" {
		record["source"] = source
	}
	return record
}

func decode(t *testing.T, text string) map[string]any {
	t.Helper()
	var out map[string]any
	if err := json.Unmarshal([]byte(text), &out); err != nil {
		t.Fatalf("not JSON: %q", text)
	}
	return out
}

func count(t *testing.T, query string, args ...any) int64 {
	t.Helper()
	handle, ok := rhoSqlHandle(telemetryDB.raw)
	if !ok {
		t.Fatal("the store is not open")
	}
	var n int64
	if err := handle.QueryRow(query, args...).Scan(&n); err != nil {
		t.Fatalf("%s: %v", query, err)
	}
	return n
}

func exec(t *testing.T, query string, args ...any) {
	t.Helper()
	handle, _ := rhoSqlHandle(telemetryDB.raw)
	if _, err := handle.Exec(query, args...); err != nil {
		t.Fatalf("%s: %v", query, err)
	}
}

func latestValue(t *testing.T, project, source, field string) float64 {
	t.Helper()
	answer := decode(t, telemetryLatest(project, source, field))
	if answer["ok"] != true {
		t.Fatalf("no latest value for %s %s", source, field)
	}
	return answer["value"].(float64)
}

// @verifies MESH-TEL-0001, MESH-TEL-0002
func TestAPageIsStoredOnceAndAcknowledged(t *testing.T) {
	openStore(t)
	g := newGateway(t)
	now := time.Now().UnixMilli()
	events := filepath.Join(t.TempDir(), "events.jsonl")
	g.serve([]map[string]any{
		reading(1, "board-a", 5, 1, "temp", 20.5, 0, now-3000),
		reading(2, "board-a", 5, 1, "temp", 21.0, 0, now-2000),
		reading(3, "board-a", 5, 1, "temp", 21.5, 0, now-1000),
	}, false)
	answer := decode(t, telemetryPoll("Sec", "Sec@gw", g.server.URL, "tok", events))
	if answer["ok"] != true || answer["fetched"].(float64) != 3 || answer["last_seq"].(float64) != 3 {
		t.Fatalf("the first page: %v", answer)
	}
	if got := count(t, "SELECT COUNT(*) FROM telemetry_reading"); got != 3 {
		t.Fatalf("stored %d readings, want 3", got)
	}
	if got := telemetryCursor("Sec@gw"); got != 3 {
		t.Fatalf("the cursor is %d, want 3", got)
	}
	if ack := g.lastAck(); ack != `{"seq":3}` {
		t.Fatalf("the ack was %q", ack)
	}
	for _, token := range g.tokens {
		if token != "Bearer tok" {
			t.Fatalf("a request to the gateway carried %q", token)
		}
	}
	// The next poll asks after the cursor, and an empty page changes nothing.
	answer = decode(t, telemetryPoll("Sec", "Sec@gw", g.server.URL, "tok", events))
	if answer["fetched"].(float64) != 0 || g.afters[len(g.afters)-1] != "3" {
		t.Fatalf("the second poll: %v after %v", answer, g.afters)
	}
	if latestValue(t, "Sec", "board-a", "temp") != 21.5 {
		t.Fatal("the latest value is not the newest reading")
	}
	data, _ := os.ReadFile(events)
	if strings.Count(string(data), "\n") != 3 {
		t.Fatalf("the live feed has %d lines, want 3", strings.Count(string(data), "\n"))
	}
}

// A gateway that sends again what was committed — a page read again after a
// lost ack, or a feed replayed by whoever answers at the gateway's address —
// adds no reading, does not put an old value back as the latest, raises no
// fault again and puts nothing on the live feed.
// @verifies MESH-TEL-0002, MESH-TEL-0006
func TestAReplayedPageAddsNothing(t *testing.T) {
	openStore(t)
	g := newGateway(t)
	now := time.Now().UnixMilli()
	events := filepath.Join(t.TempDir(), "events.jsonl")
	g.serve([]map[string]any{
		reading(1, "board-a", 5, 1, "temp", 20.0, 0, now-3000),
		reading(2, "board-a", 5, 1, "volt", 3.1, 2, now-2000),
		reading(3, "board-a", 5, 1, "temp", 22.0, 0, now-1000),
	}, false)
	decode(t, telemetryPoll("Sec", "Sec@gw", g.server.URL, "", events))
	faults := count(t, "SELECT COUNT(*) FROM telemetry_fault")
	if faults != 1 {
		t.Fatalf("a reading with status 2 raised %d faults, want 1", faults)
	}
	feed, _ := os.ReadFile(events)

	// The same sequences again, with other values, as if the outbox were replayed.
	g.serve([]map[string]any{
		reading(1, "board-a", 5, 1, "temp", 99.0, 0, now-500),
		reading(2, "board-a", 5, 1, "volt", 0.1, 2, now-400),
		reading(3, "board-a", 5, 1, "temp", 98.0, 0, now-300),
	}, true)
	answer := decode(t, telemetryPoll("Sec", "Sec@gw", g.server.URL, "", events))
	if answer["ok"] != true || answer["fetched"].(float64) != 0 {
		t.Fatalf("a replayed page was taken: %v", answer)
	}
	if got := count(t, "SELECT COUNT(*) FROM telemetry_reading"); got != 3 {
		t.Fatalf("a replay left %d readings, want 3", got)
	}
	if got := latestValue(t, "Sec", "board-a", "temp"); got != 22.0 {
		t.Fatalf("a replay made the latest value %v", got)
	}
	if got := count(t, "SELECT COUNT(*) FROM telemetry_fault"); got != faults {
		t.Fatalf("a replay raised its fault again: %d faults", got)
	}
	if again, _ := os.ReadFile(events); string(again) != string(feed) {
		t.Fatal("a replay reached the live feed")
	}
	if telemetryCursor("Sec@gw") != 3 {
		t.Fatal("a replay moved the cursor")
	}

	// One sequence twice in one page is one reading, and the first one.
	g.serve([]map[string]any{
		reading(4, "board-a", 5, 1, "temp", 23.0, 0, now-200),
		reading(4, "board-a", 5, 1, "temp", 77.0, 0, now-100),
	}, false)
	answer = decode(t, telemetryPoll("Sec", "Sec@gw", g.server.URL, "", events))
	if answer["fetched"].(float64) != 1 {
		t.Fatalf("a sequence repeated in a page was taken twice: %v", answer)
	}
	if got := latestValue(t, "Sec", "board-a", "temp"); got != 23.0 {
		t.Fatalf("the repeated sequence made the latest value %v", got)
	}
	if got := count(t, "SELECT COUNT(*) FROM telemetry_reading WHERE seq = 4"); got != 1 {
		t.Fatalf("sequence 4 is stored %d times", got)
	}
}

// The sequence is a gateway's: two gateways' sequence 1 are two readings.
// @verifies MESH-TEL-0002
func TestASequenceIsPerGateway(t *testing.T) {
	openStore(t)
	first, second := newGateway(t), newGateway(t)
	now := time.Now().UnixMilli()
	first.serve([]map[string]any{reading(1, "board-a", 5, 1, "temp", 20, 0, now)}, false)
	second.serve([]map[string]any{reading(1, "board-b", 7, 1, "temp", 30, 0, now)}, false)
	decode(t, telemetryPoll("Sec", "Sec@one", first.server.URL, "", ""))
	decode(t, telemetryPoll("Other", "Other@two", second.server.URL, "", ""))
	if got := count(t, "SELECT COUNT(*) FROM telemetry_reading"); got != 2 {
		t.Fatalf("two gateways' first readings are %d rows", got)
	}
	if telemetryCursor("Sec@one") != 1 || telemetryCursor("Other@two") != 1 {
		t.Fatal("each gateway keeps its own cursor")
	}
}

// A series is a source and a field: a board's own identity, the source the plan
// bound at a place for a reading without one, and a legacy source for a place
// nothing is bound to. A board that reports from another's place is its own
// series and one fault.
// @verifies MESH-TEL-0004
func TestASeriesIsKeyedBySource(t *testing.T) {
	openStore(t)
	g := newGateway(t)
	now := time.Now().UnixMilli()
	if answer := decode(t, telemetrySourceBind("Sec", "board-a", "Sec@gw", 5, 1, "Board A", "")); answer["ok"] != true {
		t.Fatalf("binding: %v", answer)
	}
	g.serve([]map[string]any{
		reading(1, "board-a", 5, 1, "temp", 20, 0, now-5000),
		reading(2, "", 5, 1, "temp", 21, 0, now-4000),        // placed by the plan
		reading(3, "", 5, 2, "temp", 30, 0, now-3000),        // nothing bound there
		reading(4, "board-b", 5, 1, "temp", 40, 0, now-2000), // claims board-a's place
		reading(5, "board-b", 5, 1, "temp", 41, 0, now-1000),
	}, false)
	decode(t, telemetryPoll("Sec", "Sec@gw", g.server.URL, "", ""))
	if got := count(t, "SELECT COUNT(*) FROM telemetry_reading WHERE source = 'board-a'"); got != 2 {
		t.Fatalf("board-a has %d readings, want 2", got)
	}
	if got := count(t, "SELECT COUNT(*) FROM telemetry_reading WHERE source = 'legacy:5.2'"); got != 1 {
		t.Fatalf("the unbound place has %d readings, want 1", got)
	}
	if got := count(t, "SELECT COUNT(*) FROM telemetry_reading WHERE source = 'board-b'"); got != 2 {
		t.Fatalf("board-b has %d readings, want 2: it is never merged into board-a", got)
	}
	if latestValue(t, "Sec", "board-a", "temp") != 21 || latestValue(t, "Sec", "board-b", "temp") != 41 {
		t.Fatal("each series has its own latest value")
	}
	if got := count(t, "SELECT COUNT(*) FROM telemetry_fault WHERE source = 'board-b' AND message LIKE '%board-a%'"); got != 1 {
		t.Fatalf("a board at another's place raised %d faults, want 1", got)
	}
	if got := count(t, "SELECT area_id FROM telemetry_source WHERE project = 'Sec' AND source = 'board-b'"); got != -1 {
		t.Fatalf("a board the plan has not placed is recorded at area %d, want unplaced", got)
	}
	// Binding a board where readings were kept apart adopts them.
	answer := decode(t, telemetrySourceBind("Sec", "board-c", "Sec@gw", 5, 2, "Board C", ""))
	if answer["adopted"].(float64) != 1 {
		t.Fatalf("binding adopted %v readings, want 1", answer["adopted"])
	}
	if count(t, "SELECT COUNT(*) FROM telemetry_reading WHERE source = 'legacy:5.2'") != 0 ||
		latestValue(t, "Sec", "board-c", "temp") != 30 {
		t.Fatal("the adopted readings are board-c's")
	}
	// The same field of another project is another series.
	if decode(t, telemetryLatest("Other", "board-a", "temp"))["ok"] == true {
		t.Fatal("a project reads another project's series")
	}
}

// Completed readings are rolled up by minute and the minutes by hour, with
// their minimum, maximum, mean and count.
// @verifies MESH-TEL-0005
func TestReadingsAreRolledUp(t *testing.T) {
	openStore(t)
	hour := (time.Now().Add(-3*time.Hour).UnixMilli() / 3600000) * 3600000
	insert := func(seq int, at int64, value float64) {
		exec(t, "INSERT INTO telemetry_reading (project, gateway_id, seq, source, area_id, node_id, field, value, status, received_ms) "+
			"VALUES ('Sec', 'Sec@gw', ?, 'board-a', 5, 1, 'temp', ?, 0, ?)", seq, value, at)
	}
	insert(1, hour+1000, 10)
	insert(2, hour+30000, 20)
	insert(3, hour+59000, 30)
	insert(4, hour+60000, 50)
	insert(5, hour+30*60000, 70)
	// A reading with a fault status is not part of the series.
	exec(t, "INSERT INTO telemetry_reading (project, gateway_id, seq, source, area_id, node_id, field, value, status, received_ms) "+
		"VALUES ('Sec', 'Sec@gw', 6, 'board-a', 5, 1, 'temp', 1000, 2, ?)", hour+2000)
	if answer := decode(t, telemetryRollup("Sec")); answer["ok"] != true {
		t.Fatalf("the roll-up: %v", answer)
	}
	handle, _ := rhoSqlHandle(telemetryDB.raw)
	var low, high, mean float64
	var samples int64
	if err := handle.QueryRow("SELECT value_min, value_max, value_avg, samples FROM telemetry_rollup_1m "+
		"WHERE project = 'Sec' AND source = 'board-a' AND field = 'temp' AND bucket_ms = ?", hour).
		Scan(&low, &high, &mean, &samples); err != nil {
		t.Fatalf("the first minute was not rolled up: %v", err)
	}
	if low != 10 || high != 30 || mean != 20 || samples != 3 {
		t.Fatalf("the first minute is %v..%v mean %v of %d", low, high, mean, samples)
	}
	if got := count(t, "SELECT COUNT(*) FROM telemetry_rollup_1m WHERE project = 'Sec'"); got != 3 {
		t.Fatalf("%d minute buckets, want 3", got)
	}
	if err := handle.QueryRow("SELECT value_min, value_max, samples FROM telemetry_rollup_1h "+
		"WHERE project = 'Sec' AND bucket_ms = ?", hour).Scan(&low, &high, &samples); err != nil {
		t.Fatalf("the hour was not rolled up: %v", err)
	}
	if low != 10 || high != 70 || samples != 5 {
		t.Fatalf("the hour is %v..%v of %d", low, high, samples)
	}
	// Rolling up again changes nothing.
	decode(t, telemetryRollup("Sec"))
	if count(t, "SELECT COUNT(*) FROM telemetry_rollup_1m") != 3 || count(t, "SELECT SUM(samples) FROM telemetry_rollup_1h") != 5 {
		t.Fatal("a second roll-up counted readings twice")
	}
}

// Retention removes each window's oldest rows after rolling them up, so a chart
// of an older season is still there after its raw readings are gone; faults are
// kept for their own window.
// @verifies MESH-TEL-0005
func TestRetentionPrunesAfterRollingUp(t *testing.T) {
	openStore(t)
	day := int64(24 * 3600 * 1000)
	now := time.Now().UnixMilli()
	ages := []int64{1, 20, 100, 2000}
	for index, age := range ages {
		exec(t, "INSERT INTO telemetry_reading (project, gateway_id, seq, source, area_id, node_id, field, value, status, received_ms) "+
			"VALUES ('Sec', 'Sec@gw', ?, 'board-a', 5, 1, 'temp', ?, 0, ?)", index+1, float64(age), now-age*day)
		// Another project's readings are its own to prune.
		exec(t, "INSERT INTO telemetry_reading (project, gateway_id, seq, source, area_id, node_id, field, value, status, received_ms) "+
			"VALUES ('Other', 'Other@gw', ?, 'board-b', 7, 1, 'temp', ?, 0, ?)", index+1, float64(age), now-age*day)
	}
	answer := decode(t, telemetryPrune("Sec", 0, 0, 0))
	if answer["ok"] != true {
		t.Fatalf("the retention pass: %v", answer)
	}
	ageOf := func(table, column string) []int64 {
		handle, _ := rhoSqlHandle(telemetryDB.raw)
		rows, err := handle.Query("SELECT " + column + " FROM " + table + " WHERE project = 'Sec' ORDER BY " + column + " DESC")
		if err != nil {
			t.Fatal(err)
		}
		defer rows.Close()
		var out []int64
		for rows.Next() {
			var at int64
			_ = rows.Scan(&at)
			out = append(out, (now-at+day/2)/day)
		}
		return out
	}
	if got := fmt.Sprint(ageOf("telemetry_reading", "received_ms")); got != "[1]" {
		t.Fatalf("raw readings kept, by age in days: %s, want the last 14 days", got)
	}
	if got := fmt.Sprint(ageOf("telemetry_rollup_1m", "bucket_ms")); got != "[1 20]" {
		t.Fatalf("minute roll-ups kept: %s, want the last 90 days", got)
	}
	if got := fmt.Sprint(ageOf("telemetry_rollup_1h", "bucket_ms")); got != "[1 20 100]" {
		t.Fatalf("hourly roll-ups kept: %s, want the last 1825 days", got)
	}
	if got := count(t, "SELECT COUNT(*) FROM telemetry_reading WHERE project = 'Other'"); got != 4 {
		t.Fatalf("pruning Sec removed another project's readings: %d left", got)
	}
	// The series over the older season reads the hourly roll-up.
	series := decode(t, telemetrySeries("Sec", "board-a", "temp",
		fmt.Sprint(now-120*day), fmt.Sprint(now-90*day), "3600000"))
	points, _ := series["points"].([]any)
	if len(points) != 1 || points[0].(map[string]any)["v"].(float64) != 100 {
		t.Fatalf("the series of a pruned season is %v", series)
	}
	// Explicit windows are honoured.
	decode(t, telemetryPrune("Other", 10, 30, 50))
	if count(t, "SELECT COUNT(*) FROM telemetry_rollup_1h WHERE project = 'Other'") != 2 {
		t.Fatal("a shorter hourly window kept more than it allows")
	}
	// Faults have a window of their own.
	telemetryFaultRecord("Sec", "Sec@gateway", "gateway", "ERROR", "old", 0, 0, 2, fmt.Sprint(now-400*day))
	telemetryFaultRecord("Sec", "Sec@gateway", "gateway", "ERROR", "new", 0, 0, 2, fmt.Sprint(now-day))
	if removed := decode(t, telemetryFaultPrune("Sec", 0))["removed"].(float64); removed != 1 {
		t.Fatalf("the fault pass removed %v faults, want the one older than 365 days", removed)
	}
	if count(t, "SELECT COUNT(*) FROM telemetry_fault WHERE message = 'new'") != 1 {
		t.Fatal("the recent fault was removed")
	}
}
