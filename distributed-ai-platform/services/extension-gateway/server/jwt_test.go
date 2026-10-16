package server

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"
)

// signHS256 builds a JWT the way PyJWT does (base64url, no padding).
func signHS256(t *testing.T, secret string, claims map[string]any) string {
	t.Helper()
	return signWithHeader(t, secret, map[string]any{"alg": "HS256", "typ": "JWT"}, claims)
}

func signWithHeader(t *testing.T, secret string, header, claims map[string]any) string {
	t.Helper()
	enc := func(v any) string {
		b, err := json.Marshal(v)
		if err != nil {
			t.Fatal(err)
		}
		return base64.RawURLEncoding.EncodeToString(b)
	}
	signing := enc(header) + "." + enc(claims)
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(signing))
	return signing + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

func TestHS256Validator(t *testing.T) {
	now := time.Unix(1_800_000_000, 0)
	validate := NewHS256Validator([]byte(testSecret), func() time.Time { return now })
	n := now.Unix()
	good := map[string]any{"sub": "alice", "iat": n - 10, "exp": n + 3600}

	if err := validate(signHS256(t, testSecret, good)); err != nil {
		t.Fatalf("valid token rejected: %v", err)
	}
	// Internal service tokens (create_internal_token) carry extra claims.
	internal := map[string]any{"sub": "ssh-executor", "iat": n, "exp": n + 300, "internal": true}
	if err := validate(signHS256(t, testSecret, internal)); err != nil {
		t.Fatalf("internal token rejected: %v", err)
	}

	without := func(k string) map[string]any {
		c := map[string]any{}
		for kk, v := range good {
			if kk != k {
				c[kk] = v
			}
		}
		return c
	}
	with := func(k string, v any) map[string]any {
		c := without(k)
		c[k] = v
		return c
	}
	noneTok := signWithHeader(t, testSecret, map[string]any{"alg": "none"}, good)
	cases := []struct {
		name  string
		token string
		want  error
	}{
		{"wrong secret", signHS256(t, "other", good), ErrTokenSignature},
		{"expired", signHS256(t, testSecret, with("exp", n-1)), ErrTokenExpired},
		{"missing sub", signHS256(t, testSecret, without("sub")), ErrTokenClaims},
		{"empty sub", signHS256(t, testSecret, with("sub", " ")), ErrTokenClaims},
		{"missing exp", signHS256(t, testSecret, without("exp")), ErrTokenClaims},
		{"missing iat", signHS256(t, testSecret, without("iat")), ErrTokenClaims},
		{"string exp", signHS256(t, testSecret, with("exp", "never")), ErrTokenClaims},
		{"iat far future", signHS256(t, testSecret, with("iat", n+3600)), ErrTokenClaims},
		{"nbf future", signHS256(t, testSecret, with("nbf", n+100)), ErrTokenClaims},
		{"alg none", noneTok, ErrTokenAlgorithm},
		{"alg HS512", signWithHeader(t, testSecret, map[string]any{"alg": "HS512"}, good), ErrTokenAlgorithm},
		{"two parts", "a.b", ErrTokenMalformed},
		{"garbage", "!!.??.**", ErrTokenMalformed},
		{"empty", "", ErrTokenMalformed},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if err := validate(tc.token); !errors.Is(err, tc.want) {
				t.Fatalf("err = %v, want %v", err, tc.want)
			}
		})
	}
}

// A tampered payload must fail even if it still parses.
func TestHS256ValidatorDetectsTampering(t *testing.T) {
	now := time.Now()
	tok := signHS256(t, testSecret, map[string]any{"sub": "alice", "iat": now.Unix(), "exp": now.Unix() + 60})
	evil := base64.RawURLEncoding.EncodeToString([]byte(`{"sub":"bob","iat":1,"exp":99999999999}`))
	head, sig := tok[:strings.Index(tok, ".")], tok[strings.LastIndex(tok, ".")+1:]
	if err := NewHS256Validator([]byte(testSecret), nil)(head + "." + evil + "." + sig); !errors.Is(err, ErrTokenSignature) {
		t.Fatalf("tampered token: err = %v", err)
	}
}
