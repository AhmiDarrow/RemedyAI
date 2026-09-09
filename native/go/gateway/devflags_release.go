//go:build !remedydev

package gateway

// Release builds carry no environment kill switches: every helper below is a
// constant false so the compiler removes the branches that consult them.

func devAllowAll(string) bool { return false }

func devSkipTeamsJWT() bool { return false }

func devSkipTeamsJWKS() bool { return false }
