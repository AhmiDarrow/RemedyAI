//go:build remedydev

package gateway

import (
	"log"
	"os"
	"strings"
)

// Development-only kill switches. These compile only with -tags remedydev so a
// release binary has no environment variable that widens messenger access or
// skips webhook authentication.

func devEnvTruthy(key string) bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv(key))) {
	case "1", "true", "yes", "on":
		log.Printf("gateway: DEV override %s is set (remedydev build)", key)
		return true
	default:
		return false
	}
}

// devAllowAll reports whether REMEDY_<CHANNEL>_ALLOW_ALL is set (dev builds only).
func devAllowAll(envKey string) bool { return devEnvTruthy(envKey) }

// devSkipTeamsJWT skips Bot Framework JWT checks entirely (dev builds only).
func devSkipTeamsJWT() bool { return devEnvTruthy("REMEDY_TEAMS_SKIP_JWT") }

// devSkipTeamsJWKS skips the RS256 signature check only (dev builds only).
func devSkipTeamsJWKS() bool { return devEnvTruthy("REMEDY_TEAMS_SKIP_JWKS") }
