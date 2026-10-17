// Command extension-gateway serves the HTTP API consumed by the Distributed
// Compute VS Code extension. It is a thin adapter over ssh-executor: every
// request is authenticated with the user's platform JWT and forwarded with
// that same token, so ssh-executor's owner scoping applies unchanged.
//
// Configuration is read from the environment; see loadConfig in config.go.
// Without UPSTREAM_URL it serves an empty in-memory store (local dev only);
// without JWT_SECRET it accepts any non-empty bearer token (local dev only).
// Both are refused when ENVIRONMENT=production.
//
// `extension-gateway -healthcheck` probes GET /health on the local listener
// and exits 0/1 (used by the container HEALTHCHECK; the image has no shell).
package main

import (
	"context"
	"errors"
	"flag"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/distributed-ai-platform/extension-gateway/server"
)

func main() {
	healthcheck := flag.Bool("healthcheck", false, "probe the local /health endpoint and exit")
	flag.Parse()

	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))

	cfg, err := loadConfig(os.Getenv)
	if err != nil {
		logger.Error("invalid_config", "err", err.Error())
		os.Exit(1)
	}
	if *healthcheck {
		os.Exit(probeHealth(cfg.ListenAddr))
	}

	srv := server.New(server.Config{
		Store:      buildStore(cfg, logger),
		Validate:   buildValidator(cfg, logger),
		LocalToken: buildLocalToken(cfg, logger),
		Proxy:      buildProxy(cfg, logger),
		Logger:     logger,
	})

	httpSrv := server.NewHTTPServer(cfg.ListenAddr, srv.Handler())
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(shutdownCtx)
	}()

	logger.Info("extension_gateway_listening", "addr", cfg.ListenAddr, "environment", cfg.Environment)
	if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		logger.Error("server_exited", "err", err.Error())
		os.Exit(1)
	}
}

func buildStore(cfg appConfig, logger *slog.Logger) server.Store {
	if cfg.UpstreamURL == "" {
		logger.Warn("store_in_memory",
			"detail", "UPSTREAM_URL is not set: serving an EMPTY in-memory store (local dev only); "+
				"set UPSTREAM_URL=http://ssh-executor:8011 to serve real nodes and executions")
		return server.NewMemStore()
	}
	st, err := server.NewUpstreamStore(server.UpstreamConfig{
		BaseURL: cfg.UpstreamURL,
		Timeout: cfg.UpstreamTimeout,
		Logger:  logger,
	})
	if err != nil {
		logger.Error("invalid_config", "err", err.Error())
		os.Exit(1)
	}
	logger.Info("store_upstream", "upstream_url", cfg.UpstreamURL, "timeout", cfg.UpstreamTimeout.String())
	return st
}

// localTokenTTL is long because a local single-user install has nobody to
// re-authenticate against; the extension re-requests a token when it expires.
const localTokenTTL = 30 * 24 * time.Hour

// buildLocalToken enables one-click sign-in for local installs. It is only
// on in development, where the Python services already accept anonymous calls
// as the same user, so it grants nothing those services do not.
func buildLocalToken(cfg appConfig, logger *slog.Logger) server.LocalTokenIssuer {
	if cfg.Environment != "development" {
		return nil
	}
	secret := cfg.JWTSecret
	if secret == "" {
		// Same default as the Python services, so their check still passes.
		secret = placeholderJWTSecret
	}
	logger.Info("local_sign_in_enabled", "subject", server.LocalSubject)
	return server.NewLocalTokenIssuer([]byte(secret), server.LocalSubject, localTokenTTL, nil)
}

// buildProxy serves training, engines and datasets when ssh-executor is configured.
func buildProxy(cfg appConfig, logger *slog.Logger) *server.Proxy {
	if cfg.UpstreamURL == "" {
		return nil
	}
	p, err := server.NewProxy(server.ProxyConfig{
		SSHURL:     cfg.UpstreamURL,
		StorageURL: cfg.StorageURL,
		Logger:     logger,
	})
	if err != nil {
		logger.Error("invalid_config", "err", err.Error())
		os.Exit(1)
	}
	if cfg.StorageURL == "" {
		logger.Warn("datasets_unavailable", "detail", "STORAGE_URL is not set: /datasets answers 501")
	}
	return p
}

func buildValidator(cfg appConfig, logger *slog.Logger) server.TokenValidator {
	if cfg.JWTSecret == "" {
		logger.Warn("auth_dev_mode",
			"detail", "JWT_SECRET is not set: bearer tokens are NOT verified by the gateway (local dev only); "+
				"ssh-executor still verifies forwarded tokens")
		return server.DevValidator
	}
	if cfg.JWTSecret == placeholderJWTSecret {
		logger.Warn("auth_placeholder_secret", "detail", "JWT_SECRET is the development placeholder")
	}
	return server.NewHS256Validator([]byte(cfg.JWTSecret), nil)
}

func probeHealth(listenAddr string) int {
	host, port, err := net.SplitHostPort(listenAddr)
	if err != nil {
		return 1
	}
	if host == "" || host == "0.0.0.0" || host == "::" {
		host = "127.0.0.1"
	}
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get("http://" + net.JoinHostPort(host, port) + "/health")
	if err != nil {
		return 1
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return 1
	}
	return 0
}
