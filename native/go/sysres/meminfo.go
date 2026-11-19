package sysres

import (
	"strconv"
	"strings"
)

// parseMeminfo reads MemTotal and MemAvailable (kB) from /proc/meminfo text.
// Kernels without MemAvailable fall back to MemFree+Cached.
func parseMeminfo(text string) (total, avail uint64) {
	var free, cached uint64
	haveAvail := false
	for _, line := range strings.Split(text, "\n") {
		name, rest, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		fields := strings.Fields(rest)
		if len(fields) == 0 {
			continue
		}
		n, err := strconv.ParseUint(fields[0], 10, 64)
		if err != nil {
			continue
		}
		n *= 1024
		switch strings.TrimSpace(name) {
		case "MemTotal":
			total = n
		case "MemAvailable":
			avail, haveAvail = n, true
		case "MemFree":
			free = n
		case "Cached":
			cached = n
		}
	}
	if !haveAvail {
		avail = free + cached
	}
	if total == 0 {
		return 0, 0
	}
	if avail > total {
		avail = total
	}
	return total, avail
}
