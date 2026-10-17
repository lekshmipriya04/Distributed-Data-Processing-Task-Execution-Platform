package main

import (
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"
)

// placeholderJWTSecret is the default in shared/common/config.py and
// config/.env.example; it must never be accepted in production.
const placeholderJWTSecret = "change_me_in_production"

type appConfig struct {
	ListenAddr      string
	UpstreamURL     string // empty -> in-memory MemStore (local dev only)
	StorageURL      string // storage-service origin for /datasets; optional
	UpstreamTimeout time.Duration
	JWTSecret       string // empty -> DevValidator (local dev only)
	Environment     string // development | staging | production
}

func (c appConfig) production() bool { return c.Environment == "production" }

// loadConfig reads the gateway configuration from the environment.
//
//	LISTEN_ADDR               listen address (default :8090; legacy EXTENSION_GATEWAY_ADDR)
//	UPSTREAM_URL              ssh-executor origin, e.g. http://ssh-executor:8011
//	UPSTREAM_TIMEOUT_SECONDS  per-call upstream timeout (default 15)
//	STORAGE_URL               storage-service origin for /datasets, e.g. http://storage-service:8001
//	JWT_SECRET                HS256 secret shared with the Python services
//	JWT_ALGORITHM             must be HS256 if set
//	ENVIRONMENT               development (default) | staging | production
//
// In production JWT_SECRET (non-placeholder) and UPSTREAM_URL are mandatory.
func loadConfig(getenv func(string) string) (appConfig, error) {
	cfg := appConfig{
		ListenAddr:      strings.TrimSpace(getenv("LISTEN_ADDR")),
		UpstreamURL:     strings.TrimSpace(getenv("UPSTREAM_URL")),
		StorageURL:      strings.TrimSpace(getenv("STORAGE_URL")),
		UpstreamTimeout: 15 * time.Second,
		JWTSecret:       getenv("JWT_SECRET"),
		Environment:     strings.ToLower(strings.TrimSpace(getenv("ENVIRONMENT"))),
	}
	if cfg.ListenAddr == "" {
		cfg.ListenAddr = strings.TrimSpace(getenv("EXTENSION_GATEWAY_ADDR"))
	}
	if cfg.ListenAddr == "" {
		cfg.ListenAddr = ":8090"
	}
	if cfg.Environment == "" {
		cfg.Environment = "development"
	}
	if raw := strings.TrimSpace(getenv("UPSTREAM_TIMEOUT_SECONDS")); raw != "" {
		n, err := strconv.Atoi(raw)
		if err != nil || n <= 0 {
			return cfg, fmt.Errorf("UPSTREAM_TIMEOUT_SECONDS must be a positive integer, got %q", raw)
		}
		cfg.UpstreamTimeout = time.Duration(n) * time.Second
	}
	if alg := strings.TrimSpace(getenv("JWT_ALGORITHM")); alg != "" && !strings.EqualFold(alg, "HS256") {
		return cfg, fmt.Errorf("JWT_ALGORITHM=%s is not supported; the gateway validates HS256 only", alg)
	}
	if cfg.production() {
		if cfg.JWTSecret == "" || cfg.JWTSecret == placeholderJWTSecret {
			return cfg, errors.New("JWT_SECRET must be set to a real secret when ENVIRONMENT=production")
		}
		if cfg.UpstreamURL == "" {
			return cfg, errors.New("UPSTREAM_URL is required when ENVIRONMENT=production")
		}
	}
	return cfg, nil
}
