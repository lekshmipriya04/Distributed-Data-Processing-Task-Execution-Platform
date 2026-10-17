package server

import (
	"net/http"
	"testing"
	"time"
)

func TestLocalTokenDisabledByDefault(t *testing.T) {
	s := newTestServer()
	if w := do(t, s, "POST", "/auth/local-token", "", nil); w.Code != http.StatusUnauthorized {
		t.Fatalf("without an issuer the route must not be open, got %d", w.Code)
	}
}

func TestLocalTokenIssuesValidToken(t *testing.T) {
	const secret = "a-local-secret-that-is-long-enough-1234"
	now := time.Unix(1_800_000_000, 0)
	clock := func() time.Time { return now }
	s := New(Config{
		Store:      NewMemStore(),
		Validate:   NewHS256Validator([]byte(secret), clock),
		LocalToken: NewLocalTokenIssuer([]byte(secret), LocalSubject, time.Hour, clock),
	})

	w := do(t, s, "POST", "/auth/local-token", "", nil)
	if w.Code != http.StatusOK {
		t.Fatalf("local-token = %d %s", w.Code, w.Body.String())
	}
	if got := w.Header().Get("Cache-Control"); got != "no-store" {
		t.Fatalf("Cache-Control = %q, want no-store", got)
	}
	resp := decodeBody[localTokenResponse](t, w)
	if resp.Subject != LocalSubject || resp.Token == "" {
		t.Fatalf("unexpected response %+v", resp)
	}
	if want := now.Add(time.Hour).UTC().Format(time.RFC3339); resp.ExpiresAt != want {
		t.Fatalf("expiresAt = %s, want %s", resp.ExpiresAt, want)
	}
	if w := do(t, s, "GET", "/nodes", resp.Token, nil); w.Code != http.StatusOK {
		t.Fatalf("issued token rejected by the gateway: %d", w.Code)
	}
	if err := validateHS256(resp.Token, []byte("some-other-secret"), now); err == nil {
		t.Fatal("token must not validate under a different secret")
	}
}
