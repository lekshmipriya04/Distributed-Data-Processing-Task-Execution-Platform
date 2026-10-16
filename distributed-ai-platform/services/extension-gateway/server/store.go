package server

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"
)

// ErrNotFound is returned by a Store when a requested entity does not exist
// (or is not visible to the caller).
var ErrNotFound = errors.New("not found")

// ErrNotImplemented is returned when a capability is not wired to a real
// backend yet. Handlers translate it to HTTP 501 so the extension can show an
// honest "not supported by connected backend" message instead of fake success.
var ErrNotImplemented = errors.New("not implemented")

// StatusError is a Store error that carries the HTTP status (and a message
// safe to show the user) to return to the extension. UpstreamStore uses it to
// map ssh-executor failures (400 validation, 401, 409, 5xx, timeouts, ...).
type StatusError struct {
	Status  int    // HTTP status to return to the extension
	Code    string // machine-readable error code
	Message string // user-facing, secret-free message
	Err     error  // underlying cause, for logs only
}

func (e *StatusError) Error() string {
	if e.Err != nil {
		return fmt.Sprintf("%d %s: %s: %v", e.Status, e.Code, e.Message, e.Err)
	}
	return fmt.Sprintf("%d %s: %s", e.Status, e.Code, e.Message)
}

func (e *StatusError) Unwrap() error { return e.Err }

// Store is the data boundary. UpstreamStore adapts ssh-executor over HTTP for
// production; tests and local dev use MemStore.
//
// Implementations must scope every call to the caller: UpstreamStore forwards
// the caller's bearer token (see TokenFromContext) so ssh-executor applies its
// owner_id = jwt.sub scoping.
type Store interface {
	ListNodes(ctx context.Context) ([]ComputeNode, error)
	GetNode(ctx context.Context, id string) (ComputeNode, error)
	ClusterStatus(ctx context.Context) (ClusterStatus, error)
	ListRuntimes(ctx context.Context) ([]Runtime, error)
	ListExecutions(ctx context.Context) ([]Execution, error)
	GetExecution(ctx context.Context, id string) (Execution, error)
	CreateExecution(ctx context.Context, req ExecutionRequest) (Execution, error)
	CancelExecution(ctx context.Context, id string) (Execution, error)
}

// MemStore is an in-memory Store for tests and local/demo runs. It never
// invents data on its own: it starts empty and only holds what is seeded.
// Executions created here never run; they stay "pending" until cancelled.
type MemStore struct {
	mu         sync.RWMutex
	nodes      map[string]ComputeNode
	executions map[string]Execution
	runtimes   []Runtime
	now        func() time.Time
	seq        int
}

func NewMemStore() *MemStore {
	return &MemStore{
		nodes:      map[string]ComputeNode{},
		executions: map[string]Execution{},
		now:        time.Now,
	}
}

// SeedNode adds a node (used by tests and explicit demo mode only).
func (s *MemStore) SeedNode(n ComputeNode) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.nodes[n.ID] = n
}

// SeedRuntime adds a runtime (used by tests and explicit demo mode only).
func (s *MemStore) SeedRuntime(r Runtime) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.runtimes = append(s.runtimes, r)
}

func (s *MemStore) ListNodes(_ context.Context) ([]ComputeNode, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]ComputeNode, 0, len(s.nodes))
	for _, n := range s.nodes {
		out = append(out, n)
	}
	return out, nil
}

func (s *MemStore) GetNode(_ context.Context, id string) (ComputeNode, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	n, ok := s.nodes[id]
	if !ok {
		return ComputeNode{}, ErrNotFound
	}
	return n, nil
}

func (s *MemStore) ClusterStatus(ctx context.Context) (ClusterStatus, error) {
	nodes, _ := s.ListNodes(ctx)
	execs, _ := s.ListExecutions(ctx)
	return aggregateCluster(nodes, execs), nil
}

func (s *MemStore) ListRuntimes(_ context.Context) ([]Runtime, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return append([]Runtime{}, s.runtimes...), nil
}

func (s *MemStore) ListExecutions(_ context.Context) ([]Execution, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]Execution, 0, len(s.executions))
	for _, e := range s.executions {
		out = append(out, e)
	}
	return out, nil
}

func (s *MemStore) GetExecution(_ context.Context, id string) (Execution, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	e, ok := s.executions[id]
	if !ok {
		return Execution{}, ErrNotFound
	}
	return e, nil
}

func (s *MemStore) CreateExecution(_ context.Context, req ExecutionRequest) (Execution, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.seq++
	total := len(req.Inputs)
	if total == 0 {
		total = 1
	}
	e := Execution{
		ID:             fmt.Sprintf("exec-%d", s.seq),
		Runtime:        req.Runtime,
		Status:         "pending",
		Total:          total,
		CPUPerTask:     req.CPUPerTask,
		TimeoutSeconds: req.TimeoutSeconds,
		CreatedAt:      s.now().UTC(),
	}
	s.executions[e.ID] = e
	return e, nil
}

func (s *MemStore) CancelExecution(_ context.Context, id string) (Execution, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	e, ok := s.executions[id]
	if !ok {
		return Execution{}, ErrNotFound
	}
	if !isTerminalStatus(e.Status) {
		now := s.now().UTC()
		e.Status = "cancelled"
		e.Cancelled = e.Total - e.Completed - e.Failed
		e.EndedAt = &now
		s.executions[id] = e
	}
	return e, nil
}

// isTerminalStatus reports whether a batch status is final.
func isTerminalStatus(status string) bool {
	switch status {
	case "cancelled", "completed", "completed_with_errors", "failed":
		return true
	}
	return false
}

// aggregateCluster sums node capacity and counts running executions. Nodes
// that did not report a value contribute nothing (never a guessed number).
func aggregateCluster(nodes []ComputeNode, execs []Execution) ClusterStatus {
	cs := ClusterStatus{}
	for _, n := range nodes {
		cs.NodesTotal++
		if n.Status == "online" || n.Status == "busy" {
			cs.NodesOnline++
		}
		if n.CPUTotal != nil {
			cs.CPUTotal += *n.CPUTotal
		}
		if n.CPUAllocatable != nil {
			cs.CPUAllocatable += *n.CPUAllocatable
		}
		if n.CPUReserved != nil {
			cs.CPUReserved += *n.CPUReserved
		}
		if n.MemoryTotalGB != nil {
			cs.MemoryTotalGB += *n.MemoryTotalGB
		}
		if n.MemoryAllocatableGB != nil {
			cs.MemoryAllocatableGB += *n.MemoryAllocatableGB
		}
		if n.MemoryReservedGB != nil {
			cs.MemoryReservedGB += *n.MemoryReservedGB
		}
	}
	for _, e := range execs {
		if e.Status == "running" || e.Status == "cancelling" {
			cs.JobsRunning++
		}
	}
	return cs
}
