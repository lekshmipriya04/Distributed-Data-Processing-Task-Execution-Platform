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

// TokenValidator verifies a bearer token. Production uses NewHS256Validator
// (same secret and rules as the Python services); DevValidator only requires a
// non-empty token and is for local development without JWT_SECRET.
type TokenValidator func(token string) error

// Config configures the server.
type Config struct {
	Store Store
	// Validate checks bearer tokens. nil means DevValidator (tests/local dev
	// only); main.go always sets it explicitly.
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
		cfg.Validate = DevValidator
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
	s.mux.HandleFunc("PATCH /nodes/{id}", s.handleUpdateNodeSlice)
	s.mux.HandleFunc("GET /runtimes", s.handleListRuntimes)
	s.mux.HandleFunc("GET /executions", s.handleListExecutions)
	s.mux.HandleFunc("POST /executions", s.handleCreateExecution)
	s.mux.HandleFunc("GET /executions/{id}", s.handleGetExecution)
	s.mux.HandleFunc("POST /executions/{id}/cancel", s.handleCancelExecution)
}

// -- middleware ----------------------------------------------------------

type ctxKey string

const (
	requestIDKey ctxKey = "requestID"
	tokenKey     ctxKey = "bearerToken"
)

// TokenFromContext returns the caller's validated bearer token, which
// UpstreamStore forwards so ssh-executor scopes data to the token's subject.
func TokenFromContext(ctx context.Context) string {
	tok, _ := ctx.Value(tokenKey).(string)
	return tok
}

func (s *Server) withRequestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get("X-Request-Id")
		if id == "" || len(id) > 128 {
			id = s.cfg.RequestID()
		}
		w.Header().Set("X-Request-Id", id)
		ctx := context.WithValue(r.Context(), requestIDKey, id)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// withAuth enforces a valid bearer token on everything except /health; a
// missing or invalid token is always 401. The validated token is stored in the
// request context for forwarding upstream.
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
		if token == "" {
			s.writeError(w, r, http.StatusUnauthorized, "missing_bearer_token",
				"Authorization: Bearer <token> is required")
			return
		}
		if err := s.cfg.Validate(token); err != nil {
			s.cfg.Logger.Info("auth_rejected", "reason", err.Error(), "path", r.URL.Path)
			s.writeError(w, r, http.StatusUnauthorized, "invalid_token", "Authentication failed")
			return
		}
		ctx := context.WithValue(r.Context(), tokenKey, token)
		next.ServeHTTP(w, r.WithContext(ctx))
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
	var se *StatusError
	switch {
	case errors.As(err, &se):
		if se.Status >= 500 {
			s.cfg.Logger.Error("store_error", "status", se.Status, "code", se.Code, "err", err.Error())
		}
		s.writeError(w, r, se.Status, se.Code, se.Message)
	case errors.Is(err, context.DeadlineExceeded):
		s.writeError(w, r, http.StatusGatewayTimeout, "upstream_timeout", "The backend did not respond in time")
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

// DevValidator accepts any non-empty token without verifying it. Local
// development only (no JWT_SECRET, ENVIRONMENT != production); ssh-executor
// still verifies the forwarded token itself.
func DevValidator(token string) error {
	if strings.TrimSpace(token) == "" {
		return errors.New("empty token")
	}
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
