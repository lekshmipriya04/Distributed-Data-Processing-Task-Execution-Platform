package server

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"strings"
	"time"
)

// TokenValidator verifies a bearer token. The production validator checks the
// platform JWT signature/expiry; the default dev validator only requires a
// non-empty token (documented, not for production).
type TokenValidator func(token string) error

// Config configures the server.
type Config struct {
	Store     Store
	Validate  TokenValidator
	Logger    *slog.Logger
	RequestID func() string
}

// Server holds the router and dependencies.
type Server struct {
	cfg Config
	mux *http.ServeMux
}

// New builds a Server with routes registered.
func New(cfg Config) *Server {
	if cfg.Logger == nil {
		cfg.Logger = slog.Default()
	}
	if cfg.Validate == nil {
		cfg.Validate = devValidator
	}
	if cfg.RequestID == nil {
		cfg.RequestID = randomRequestID
	}
	s := &Server{cfg: cfg, mux: http.NewServeMux()}
	s.routes()
	return s
}

// Handler returns the top-level http.Handler with middleware applied.
func (s *Server) Handler() http.Handler {
	return s.withRequestID(s.withAuth(s.mux))
}

func (s *Server) routes() {
	s.mux.HandleFunc("GET /health", s.handleHealth)
	s.mux.HandleFunc("GET /cluster/status", s.handleClusterStatus)
	s.mux.HandleFunc("GET /nodes", s.handleListNodes)
	s.mux.HandleFunc("GET /nodes/{id}", s.handleGetNode)
	s.mux.HandleFunc("GET /executions", s.handleListExecutions)
	s.mux.HandleFunc("POST /executions", s.handleCreateExecution)
	s.mux.HandleFunc("GET /executions/{id}", s.handleGetExecution)
}

// -- middleware ----------------------------------------------------------

type ctxKey string

const requestIDKey ctxKey = "requestID"

func (s *Server) withRequestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get("X-Request-Id")
		if id == "" {
			id = s.cfg.RequestID()
		}
		w.Header().Set("X-Request-Id", id)
		ctx := context.WithValue(r.Context(), requestIDKey, id)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// withAuth enforces a bearer token on everything except /health.
func (s *Server) withAuth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/health" {
			next.ServeHTTP(w, r)
			return
		}
		auth := r.Header.Get("Authorization")
		const prefix = "Bearer "
		if !strings.HasPrefix(auth, prefix) {
			s.writeError(w, r, http.StatusUnauthorized, "missing_bearer_token",
				"Authorization: Bearer <token> is required")
			return
		}
		token := strings.TrimSpace(strings.TrimPrefix(auth, prefix))
		if err := s.cfg.Validate(token); err != nil {
			s.writeError(w, r, http.StatusUnauthorized, "invalid_token", "Authentication failed")
			return
		}
		next.ServeHTTP(w, r)
	})
}

// -- helpers -------------------------------------------------------------

func (s *Server) writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func (s *Server) writeError(w http.ResponseWriter, r *http.Request, status int, code, msg string) {
	id, _ := r.Context().Value(requestIDKey).(string)
	s.writeJSON(w, status, apiError{Error: msg, Code: code, RequestID: id})
}

// writeStoreError maps store errors to HTTP without leaking internals.
func (s *Server) writeStoreError(w http.ResponseWriter, r *http.Request, err error) {
	switch {
	case errors.Is(err, ErrNotFound):
		s.writeError(w, r, http.StatusNotFound, "not_found", "Resource not found")
	case errors.Is(err, ErrNotImplemented):
		s.writeError(w, r, http.StatusNotImplemented, "not_implemented",
			"This capability is not supported by the connected backend yet")
	default:
		s.cfg.Logger.Error("store_error", "err", err.Error())
		s.writeError(w, r, http.StatusInternalServerError, "internal", "Internal error")
	}
}

func devValidator(token string) error {
	if strings.TrimSpace(token) == "" {
		return errors.New("empty token")
	}
	// TODO: verify platform JWT signature + expiry with the configured key.
	return nil
}

func randomRequestID() string {
	b := make([]byte, 8)
	_, _ = rand.Read(b)
	return "eg-" + hex.EncodeToString(b)
}

// NewHTTPServer builds a configured *http.Server with sane timeouts.
func NewHTTPServer(addr string, h http.Handler) *http.Server {
	return &http.Server{
		Addr:              addr,
		Handler:           h,
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       30 * time.Second,
		WriteTimeout:      60 * time.Second,
		IdleTimeout:       120 * time.Second,
	}
}
