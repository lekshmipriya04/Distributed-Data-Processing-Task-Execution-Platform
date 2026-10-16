package server

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
)

// Token validation errors (for logs/tests; clients only ever see a generic 401).
var (
	ErrTokenMalformed = errors.New("token malformed")
	ErrTokenAlgorithm = errors.New("token algorithm not allowed")
	ErrTokenSignature = errors.New("token signature invalid")
	ErrTokenExpired   = errors.New("token expired")
	ErrTokenClaims    = errors.New("token claims invalid")
)

// iatLeeway tolerates small clock skew for tokens issued "in the future".
const iatLeeway = 60 * time.Second

// NewHS256Validator returns a TokenValidator that accepts exactly the tokens
// the Python services accept (shared/common/auth.py decode_access_token):
// HS256 signed with JWT_SECRET, with required claims sub, exp and iat, and exp
// in the future. Any other alg (including "none") is rejected.
func NewHS256Validator(secret []byte, now func() time.Time) TokenValidator {
	if now == nil {
		now = time.Now
	}
	key := append([]byte(nil), secret...)
	return func(token string) error {
		return validateHS256(token, key, now())
	}
}

func validateHS256(token string, key []byte, now time.Time) error {
	parts := strings.Split(token, ".")
	if len(parts) != 3 || parts[0] == "" || parts[1] == "" || parts[2] == "" {
		return ErrTokenMalformed
	}
	headerJSON, err := b64url(parts[0])
	if err != nil {
		return ErrTokenMalformed
	}
	var header struct {
		Alg string `json:"alg"`
	}
	if err := json.Unmarshal(headerJSON, &header); err != nil {
		return ErrTokenMalformed
	}
	if header.Alg != "HS256" {
		return fmt.Errorf("%w: %q", ErrTokenAlgorithm, header.Alg)
	}

	sig, err := b64url(parts[2])
	if err != nil {
		return ErrTokenMalformed
	}
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(parts[0] + "." + parts[1]))
	if !hmac.Equal(sig, mac.Sum(nil)) {
		return ErrTokenSignature
	}

	payload, err := b64url(parts[1])
	if err != nil {
		return ErrTokenMalformed
	}
	var claims map[string]any
	if err := json.Unmarshal(payload, &claims); err != nil {
		return ErrTokenMalformed
	}
	sub, ok := claims["sub"].(string)
	if !ok || strings.TrimSpace(sub) == "" {
		return fmt.Errorf("%w: sub", ErrTokenClaims)
	}
	exp, ok := claims["exp"].(float64)
	if !ok {
		return fmt.Errorf("%w: exp", ErrTokenClaims)
	}
	iat, ok := claims["iat"].(float64)
	if !ok {
		return fmt.Errorf("%w: iat", ErrTokenClaims)
	}
	nowUnix := float64(now.Unix())
	if exp <= nowUnix {
		return ErrTokenExpired
	}
	if iat > nowUnix+iatLeeway.Seconds() {
		return fmt.Errorf("%w: iat in the future", ErrTokenClaims)
	}
	if nbfRaw, present := claims["nbf"]; present {
		nbf, ok := nbfRaw.(float64)
		if !ok || nbf > nowUnix {
			return fmt.Errorf("%w: nbf", ErrTokenClaims)
		}
	}
	return nil
}

// b64url decodes base64url, tolerating optional '=' padding.
func b64url(s string) ([]byte, error) {
	return base64.RawURLEncoding.DecodeString(strings.TrimRight(s, "="))
}
