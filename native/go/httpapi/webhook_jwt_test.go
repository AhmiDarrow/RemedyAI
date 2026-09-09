package httpapi

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"math/big"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/gateway"
)

// testJWTSigner mints RS256 tokens and publishes its public key to the JWKS
// caches, so webhook tests exercise real signature verification.
type testJWTSigner struct {
	key *rsa.PrivateKey
	kid string
}

func newTestJWTSigner(t *testing.T, kid string) *testJWTSigner {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	gateway.InjectTestRSAKey(
		kid,
		base64.RawURLEncoding.EncodeToString(key.N.Bytes()),
		base64.RawURLEncoding.EncodeToString(big.NewInt(int64(key.E)).Bytes()),
	)
	t.Cleanup(gateway.ClearTestRSAKeys)
	return &testJWTSigner{key: key, kid: kid}
}

func (s *testJWTSigner) sign(t *testing.T, claims map[string]any) string {
	t.Helper()
	enc := func(v any) string {
		b, err := json.Marshal(v)
		if err != nil {
			t.Fatal(err)
		}
		return base64.RawURLEncoding.EncodeToString(b)
	}
	signing := enc(map[string]any{"alg": "RS256", "typ": "JWT", "kid": s.kid}) + "." + enc(claims)
	sum := sha256.Sum256([]byte(signing))
	sig, err := rsa.SignPKCS1v15(rand.Reader, s.key, crypto.SHA256, sum[:])
	if err != nil {
		t.Fatal(err)
	}
	return signing + "." + base64.RawURLEncoding.EncodeToString(sig)
}
