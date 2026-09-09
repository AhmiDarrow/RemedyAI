package gateway

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"math/big"
	"testing"
	"time"
)

// signTestRS256 mints a JWT signed by key and registers its public half under
// kid so the verifier resolves it without touching the network.
func signTestRS256(t *testing.T, key *rsa.PrivateKey, kid string, claims map[string]any) string {
	t.Helper()
	enc := func(v any) string {
		b, err := json.Marshal(v)
		if err != nil {
			t.Fatal(err)
		}
		return base64.RawURLEncoding.EncodeToString(b)
	}
	head := enc(map[string]any{"alg": "RS256", "typ": "JWT", "kid": kid})
	body := enc(claims)
	signing := head + "." + body
	sum := sha256.Sum256([]byte(signing))
	sig, err := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA256, sum[:])
	if err != nil {
		t.Fatal(err)
	}
	return signing + "." + base64.RawURLEncoding.EncodeToString(sig)
}

func TestGoogleChatInboundJWT(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	const kid = "gchat-test-key"
	InjectTestRSAKey(
		kid,
		base64.RawURLEncoding.EncodeToString(key.N.Bytes()),
		base64.RawURLEncoding.EncodeToString(big.NewInt(int64(key.E)).Bytes()),
	)
	t.Cleanup(ClearTestRSAKeys)

	const project = "1234567890"
	g := New(Config{RateLimitPerMin: 100, HeartbeatInterval: time.Hour})
	ch := NewGoogleChat(g, GoogleChatConfig{AccessToken: "tok", ProjectNumber: project, AllowAll: true})
	now := time.Now()

	good := signTestRS256(t, key, kid, map[string]any{
		"iss": GoogleChatIssuer,
		"aud": project,
		"exp": float64(now.Add(time.Hour).Unix()),
		"iat": float64(now.Add(-time.Minute).Unix()),
	})
	if !ch.VerifyInboundAuth("Bearer " + good) {
		t.Fatal("a Google-signed JWT for this project must be accepted")
	}

	wrongAud := signTestRS256(t, key, kid, map[string]any{
		"iss": GoogleChatIssuer,
		"aud": "9999999999",
		"exp": float64(now.Add(time.Hour).Unix()),
	})
	if ch.VerifyInboundAuth("Bearer " + wrongAud) {
		t.Fatal("a JWT minted for another project must be rejected")
	}

	wrongIssuer := signTestRS256(t, key, kid, map[string]any{
		"iss": "attacker@example.com",
		"aud": project,
		"exp": float64(now.Add(time.Hour).Unix()),
	})
	if ch.VerifyInboundAuth("Bearer " + wrongIssuer) {
		t.Fatal("a JWT from another issuer must be rejected")
	}

	expired := signTestRS256(t, key, kid, map[string]any{
		"iss": GoogleChatIssuer,
		"aud": project,
		"exp": float64(now.Add(-time.Hour).Unix()),
	})
	if ch.VerifyInboundAuth("Bearer " + expired) {
		t.Fatal("an expired JWT must be rejected")
	}

	// Right claims, signature from a key the JWKS does not know.
	other, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	forged := signTestRS256(t, other, kid, map[string]any{
		"iss": GoogleChatIssuer,
		"aud": project,
		"exp": float64(now.Add(time.Hour).Unix()),
	})
	if ch.VerifyInboundAuth("Bearer " + forged) {
		t.Fatal("a JWT signed by an unknown key must be rejected")
	}
}
