package main

import (
	"strings"
	"testing"
)

// A device's fields merged from the messages it published (telemetryDeviceFields):
// the Tasmota plug's topics, the `$` path, the onoff type, and the Shelly plug's
// single topic read as it always was.

const tasmotaTopics = "plug/stat/RESULT\nplug/stat/POWER\nplug/tele/STATE\nplug/tele/SENSOR\n"

const tasmotaFields = `"switch|state||onoff|POWER|1"
"switch|state||onoff|$|1"
"power|power_w|W|f32|ENERGY/Power|1"
"voltage|voltage_v|V|f32|ENERGY/Voltage|1"
"energy_total|energy_total_kwh|kWh|f32|ENERGY/Total|1"
`

// fieldsOf reads the answer as field -> "value|age|problem".
func fieldsOf(t *testing.T, answer string) map[string]string {
	t.Helper()
	out := map[string]string{}
	for _, line := range strings.Split(strings.TrimSuffix(answer, "\n"), "\n") {
		if line == "" {
			continue
		}
		parts := strings.SplitN(line, "\t", 4)
		if len(parts) != 4 {
			t.Fatalf("a line is not field, value, age and problem: %q", line)
		}
		if _, twice := out[parts[0]]; twice {
			t.Fatalf("field %s answered twice in %q", parts[0], answer)
		}
		out[parts[0]] = parts[1] + "|" + parts[2] + "|" + parts[3]
	}
	return out
}

// For MESH-0031; the telemetry-store suite carries the tag (tests/tests.yml).
func TestOnOffReadsEverySpelling(t *testing.T) {
	for word, want := range map[string]string{
		"ON": "1", "on": "1", "On": "1", "true": "1", "TRUE": "1", "1": "1",
		"OFF": "0", "off": "0", "Off": "0", "false": "0", "False": "0", "0": "0",
	} {
		got := fieldsOf(t, telemetryDeviceFields("plug/stat/POWER\t0\t"+word+"\n", tasmotaTopics, tasmotaFields))
		if got["state"] != want+"|0|" {
			t.Errorf("bare %q read as %q, want %s", word, got["state"], want)
		}
		got = fieldsOf(t, telemetryDeviceFields("plug/stat/RESULT\t0\t{\"POWER\":\""+word+"\"}\n", tasmotaTopics, tasmotaFields))
		if got["state"] != want+"|0|" {
			t.Errorf("JSON string %q read as %q, want %s", word, got["state"], want)
		}
	}
	for doc, want := range map[string]string{
		`{"POWER":true}`: "1", `{"POWER":false}`: "0", `{"POWER":1}`: "1", `{"POWER":0}`: "0",
	} {
		got := fieldsOf(t, telemetryDeviceFields("plug/stat/RESULT\t2\t"+doc+"\n", tasmotaTopics, tasmotaFields))
		if got["state"] != want+"|2|" {
			t.Errorf("%s read as %q, want %s", doc, got["state"], want)
		}
	}
}

// For MESH-0031; the telemetry-store suite carries the tag (tests/tests.yml).
func TestAWordThatIsNeitherIsAProtocolErrorThatKeepsTheValue(t *testing.T) {
	// The newest message carrying the state says something that is not a state;
	// an older one said ON. The field is not answered with a value at all — not
	// with the older ON, not as off — so the store keeps what it had.
	listing := "plug/stat/POWER\t1\tBLINK\nplug/stat/RESULT\t9\t{\"POWER\":\"ON\"}\n"
	got := fieldsOf(t, telemetryDeviceFields(listing, tasmotaTopics, tasmotaFields))
	state := got["state"]
	if !strings.HasPrefix(state, "|1|plug/stat/POWER: ") || !strings.Contains(state, "BLINK") {
		t.Fatalf("a word neither on nor off is not a protocol error naming the topic and word: %q", state)
	}
	for _, doc := range []string{`{"POWER":"maybe"}`, `{"POWER":2}`, `{"POWER":null}`, `{"POWER":{"a":1}}`} {
		got := fieldsOf(t, telemetryDeviceFields("plug/stat/RESULT\t0\t"+doc+"\n", tasmotaTopics, tasmotaFields))
		if !strings.HasPrefix(got["state"], "|0|plug/stat/RESULT: ") {
			t.Errorf("%s is not a protocol error: %q", doc, got["state"])
		}
	}
}

// For MESH-0030; the telemetry-store suite carries the tag (tests/tests.yml).
func TestDollarIsTheWholePayload(t *testing.T) {
	fields := "\"switch|state||onoff|$|1\"\n\"level|level||f32|$|0.5\"\n"
	got := fieldsOf(t, telemetryDeviceFields("d/a\t0\tON\n", "d/a\n", fields))
	if got["state"] != "1|0|" {
		t.Errorf("a bare ON is not the state: %q", got)
	}
	got = fieldsOf(t, telemetryDeviceFields("d/a\t0\t\"OFF\"\n", "d/a\n", fields))
	if got["state"] != "0|0|" {
		t.Errorf("a JSON string payload is not the state: %q", got)
	}
	got = fieldsOf(t, telemetryDeviceFields("d/a\t0\t42\n", "d/a\n", "\"level|level||f32|$|0.5\"\n"))
	if got["level"] != "21|0|" {
		t.Errorf("a bare number is not scaled: %q", got)
	}
	// A JSON object is not a scalar: $ carries nothing in it, and is no error.
	got = fieldsOf(t, telemetryDeviceFields("d/a\t0\t{\"POWER\":\"ON\"}\n", "d/a\n", fields))
	if len(got) != 0 {
		t.Errorf("$ read something from a JSON object: %q", got)
	}
	// A path into a payload that is not JSON carries nothing, and is no error.
	got = fieldsOf(t, telemetryDeviceFields("plug/stat/POWER\t0\tnot json at all\n", tasmotaTopics,
		"\"power|power_w|W|f32|ENERGY/Power|1\"\n"))
	if len(got) != 0 {
		t.Errorf("a path read something from a payload that is not JSON: %q", got)
	}
}

// For MESH-0029, MESH-0030; the telemetry-store suite carries the tag (tests/tests.yml).
func TestTopicsAreMergedNewestFirst(t *testing.T) {
	// Newest first, as the bus answers. The relay's newest word is OFF on
	// stat/POWER; tele/STATE, older, still says ON; the meter is only in SENSOR.
	listing := "plug/stat/POWER\t0\tOFF\n" +
		"plug/tele/SENSOR\t3\t{\"ENERGY\":{\"Power\":12.5,\"Voltage\":230.1,\"Total\":1.25}}\n" +
		"plug/tele/STATE\t5\t{\"POWER\":\"ON\",\"Wifi\":{\"RSSI\":80}}\n" +
		"plug/tele/LWT\t60\tOnline\n" +
		"other/stat/POWER\t0\tON\n"
	got := fieldsOf(t, telemetryDeviceFields(listing, tasmotaTopics, tasmotaFields))
	want := map[string]string{
		"state":            "0|0|",
		"power_w":          "12.5|3|",
		"voltage_v":        "230.1|3|",
		"energy_total_kwh": "1.25|3|",
	}
	for field, value := range want {
		if got[field] != value {
			t.Errorf("%s = %q, want %q (all: %v)", field, got[field], value, got)
		}
	}
	if len(got) != len(want) {
		t.Errorf("fields answered beyond the field map's: %v", got)
	}
	// The other order: tele/STATE is newer than stat/POWER and wins.
	listing = "plug/tele/STATE\t1\t{\"POWER\":\"ON\"}\nplug/stat/POWER\t4\tOFF\n"
	if got := fieldsOf(t, telemetryDeviceFields(listing, tasmotaTopics, tasmotaFields)); got["state"] != "1|1|" {
		t.Errorf("the newer tele/STATE does not win: %v", got)
	}
}

// For MESH-0029; the telemetry-store suite carries the tag (tests/tests.yml).
func TestAFieldNoMessageCarriesIsNotAnswered(t *testing.T) {
	// Only the relay has spoken: the meter's fields are not answered, so the
	// store keeps their last values rather than reading them as zero.
	got := fieldsOf(t, telemetryDeviceFields("plug/stat/RESULT\t0\t{\"POWER\":\"ON\"}\n", tasmotaTopics, tasmotaFields))
	if len(got) != 1 || got["state"] != "1|0|" {
		t.Errorf("fields answered that no message carried: %v", got)
	}
	// A message on a topic that is not a status topic is not read at all.
	got = fieldsOf(t, telemetryDeviceFields("plug/tele/LWT\t0\tON\n", tasmotaTopics, tasmotaFields))
	if len(got) != 0 {
		t.Errorf("a topic outside the status topics was read: %v", got)
	}
	if got := telemetryDeviceFields("", tasmotaTopics, tasmotaFields); got != "" {
		t.Errorf("no messages answered %q", got)
	}
}

// For MESH-0029; the telemetry-store suite carries the tag (tests/tests.yml).
func TestTheShellyTopicReadsAsBefore(t *testing.T) {
	// One status topic, JSON, bool and numbers with a scale: what
	// telemetry_json_bool and telemetry_json_number gave before.
	fields := "\"switch|state||bool|output|1\"\n\"power|power_w|W|f32|apower|1\"\n" +
		"\"voltage|voltage_v|V|f32|voltage|1\"\n\"energy_total|energy_total_kwh|kWh|f32|aenergy/total|0.001\"\n"
	payload := `{"id":0,"output":true,"apower":12.5,"voltage":230.1,"aenergy":{"total":1000.0}}`
	got := fieldsOf(t, telemetryDeviceFields("sp/status/switch:0\t4\t"+payload+"\nsp/online\t9\ttrue\n",
		"sp/status/switch:0\n", fields))
	want := map[string]string{"state": "1|4|", "power_w": "12.5|4|", "voltage_v": "230.1|4|", "energy_total_kwh": "1|4|"}
	for field, value := range want {
		if got[field] != value {
			t.Errorf("%s = %q, want %q", field, got[field], value)
		}
	}
	// bool stays a JSON boolean: a word is not one, and it is no protocol error.
	got = fieldsOf(t, telemetryDeviceFields("sp/status/switch:0\t0\t{\"output\":\"ON\"}\n", "sp/status/switch:0\n", fields))
	if len(got) != 0 {
		t.Errorf("bool read a word: %v", got)
	}
	// The numbers are what telemetry_json_number answers for the same paths.
	if telemetryJsonNumber(payload, "aenergy/total", "0.001") != "1" || telemetryJsonBool(payload, "output") != "true" {
		t.Errorf("the single-path readers changed")
	}
}

// A Zigbee plug through Zigbee2MQTT: its whole state on the prefix itself, its
// availability beside it, and a neighbour whose address starts with its own in
// the listing, which is not read (MESH-ZB-0014; the telemetry-store suite carries
// the tag).
func TestZigbeeStateIsReadFromThePrefixItself(t *testing.T) {
	topics := "zigbee/home/zb1/heater\n"
	fields := `"switch|state||onoff|state|1"
"power|power_w|W|f32|power|1"
"energy_total|energy_total_kwh|kWh|f32|energy|1"
`
	listing := "zigbee/home/zb1/heater2\t0\t{\"state\":\"ON\",\"power\":99}\n" +
		"zigbee/home/zb1/heater/availability\t1\t{\"state\":\"online\"}\n" +
		"zigbee/home/zb1/heater\t3\t{\"state\":\"OFF\",\"power\":0,\"current\":0,\"energy\":3.42}\n"
	got := fieldsOf(t, telemetryDeviceFields(listing, topics, fields))
	if got["state"] != "0|3|" || got["power_w"] != "0|3|" || got["energy_total_kwh"] != "3.42|3|" {
		t.Errorf("the plug's own topic is read, not its neighbour's: %v", got)
	}
}
