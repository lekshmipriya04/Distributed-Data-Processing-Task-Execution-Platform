package main

import (
	"strings"
	"testing"
	"time"
)

func envOf(m map[string]string) func(string) string {
	return func(k string) string { return m[k] }
}

func TestLoadConfigDefaults(t *testing.T) {
	cfg, err := loadConfig(envOf(nil))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.ListenAddr != ":8090" || cfg.UpstreamURL != "" || cfg.Environment != "development" ||
		cfg.UpstreamTimeout != 15*time.Second {
		t.Fatalf("defaults = %+v", cfg)
	}
}

func TestLoadConfigFromEnv(t *testing.T) {
	cfg, err := loadConfig(envOf(map[string]string{
		"LISTEN_ADDR": ":9000", "UPSTREAM_URL": "http://ssh-executor:8011", "JWT_SECRET": "s3cret",
		"UPSTREAM_TIMEOUT_SECONDS": "5", "ENVIRONMENT": "Production", "JWT_ALGORITHM": "HS256",
	}))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.ListenAddr != ":9000" || cfg.UpstreamURL != "http://ssh-executor:8011" || !cfg.production() ||
		cfg.UpstreamTimeout != 5*time.Second || cfg.JWTSecret != "s3cret" {
		t.Fatalf("cfg = %+v", cfg)
	}
}

func TestLoadConfigLegacyAddr(t *testing.T) {
	cfg, _ := loadConfig(envOf(map[string]string{"EXTENSION_GATEWAY_ADDR": ":7000"}))
	if cfg.ListenAddr != ":7000" {
		t.Fatalf("addr = %q", cfg.ListenAddr)
	}
}

func TestLoadConfigRejects(t *testing.T) {
	prod := func(extra map[string]string) map[string]string {
		m := map[string]string{"ENVIRONMENT": "production", "JWT_SECRET": "real", "UPSTREAM_URL": "http://u:1"}
		for k, v := range extra {
			m[k] = v
		}
		return m
	}
	cases := map[string]map[string]string{
		"prod without secret":     prod(map[string]string{"JWT_SECRET": ""}),
		"prod placeholder secret": prod(map[string]string{"JWT_SECRET": placeholderJWTSecret}),
		"prod without upstream":   prod(map[string]string{"UPSTREAM_URL": ""}),
		"unsupported algorithm":   {"JWT_ALGORITHM": "RS256"},
		"bad timeout":             {"UPSTREAM_TIMEOUT_SECONDS": "0"},
		"non-numeric timeout":     {"UPSTREAM_TIMEOUT_SECONDS": "soon"},
	}
	for name, env := range cases {
		if _, err := loadConfig(envOf(env)); err == nil {
			t.Errorf("%s: expected an error", name)
		}
	}
	if _, err := loadConfig(envOf(prod(nil))); err != nil {
		t.Fatalf("valid production config rejected: %v", err)
	}
	// Dev may run with the placeholder (it matches the Python services' dev default).
	if _, err := loadConfig(envOf(map[string]string{"JWT_SECRET": placeholderJWTSecret})); err != nil &&
		!strings.Contains(err.Error(), "production") {
		t.Fatalf("dev placeholder rejected: %v", err)
	}
}
