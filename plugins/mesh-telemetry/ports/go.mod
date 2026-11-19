module github.com/robohub/mesh-telemetry/ports

go 1.26.11

// The port itself imports nothing outside the standard library: the generator
// merges it into a project whose sql port registers the driver. The driver is
// here for the tests alone, which run the store against a real SQLite database.
require modernc.org/sqlite v1.34.5

require (
	github.com/dustin/go-humanize v1.0.1 // indirect
	github.com/google/uuid v1.6.0 // indirect
	github.com/mattn/go-isatty v0.0.20 // indirect
	github.com/ncruces/go-strftime v0.1.9 // indirect
	github.com/remyoudompheng/bigfft v0.0.0-20230129092748-24d4a6f8daec // indirect
	golang.org/x/sys v0.22.0 // indirect
	modernc.org/libc v1.55.3 // indirect
	modernc.org/mathutil v1.6.0 // indirect
	modernc.org/memory v1.8.0 // indirect
)
