// Command extension-gateway serves the Go control-plane HTTP API consumed by the
// Distributed Compute VS Code extension.
//
// The default Store is an empty in-memory store (it never fabricates nodes or
// executions). Wire a platform-backed Store before production use. Set
// EXTENSION_GATEWAY_ADDR to change the listen address (default :8090).
package main

import (
	"log/slog"
	"os"

	"github.com/distributed-ai-platform/extension-gateway/server"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))

	addr := os.Getenv("EXTENSION_GATEWAY_ADDR")
	if addr == "" {
		addr = ":8090"
	}

	srv := server.New(server.Config{
		Store:  server.NewMemStore(), // TODO: replace with platform-backed Store.
		Logger: logger,
	})

	httpSrv := server.NewHTTPServer(addr, srv.Handler())
	logger.Info("extension_gateway_listening", "addr", addr)
	if err := httpSrv.ListenAndServe(); err != nil {
		logger.Error("server_exited", "err", err.Error())
		os.Exit(1)
	}
}
