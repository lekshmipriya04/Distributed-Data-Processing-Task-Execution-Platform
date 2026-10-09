package server

import (
	"context"
	"errors"
	"sync"
	"time"
)

// ErrNotFound is returned by a Store when a requested entity does not exist.
var ErrNotFound = errors.New("not found")

// ErrNotImplemented is returned when a capability is not wired to a real
// backend yet. Handlers translate it to HTTP 501 so the extension can show an
// honest "not supported by connected backend" message instead of fake success.
var ErrNotImplemented = errors.New("not implemented")

// Store is the data boundary. The production implementation adapts the existing
// platform services and Postgres; tests and local dev use MemStore.
type Store interface {
	ListNodes(ctx context.Context) ([]ComputeNode, error)
	GetNode(ctx context.Context, id string) (ComputeNode, error)
	ClusterStatus(ctx context.Context) (ClusterStatus, error)
	ListExecutions(ctx context.Context) ([]Execution, error)
	GetExecution(ctx context.Context, id string) (Execution, error)
	CreateExecution(ctx context.Context, req ExecutionRequest) (Execution, error)
}

// MemStore is an in-memory Store for tests and local/demo runs. It never
// invents data on its own: it starts empty and only holds what is seeded.
type MemStore struct {
	mu         sync.RWMutex
	nodes      map[string]ComputeNode
	executions map[string]Execution
	now        func() time.Time
	newID      func() string
}

func NewMemStore() *MemStore {
	return &MemStore{
		nodes:      map[string]ComputeNode{},
		executions: map[string]Execution{},
		now:        time.Now,
		newID:      func() string { return "exec_" + time.Now().UTC().Format("20060102150405.000000") },
	}
}

// SeedNode adds a node (used by tests and explicit demo mode only).
func (s *MemStore) SeedNode(n ComputeNode) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.nodes[n.ID] = n
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

func (s *MemStore) ClusterStatus(_ context.Context) (ClusterStatus, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	cs := ClusterStatus{}
	for _, n := range s.nodes {
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
	for _, e := range s.executions {
		if e.Status == "running" {
			cs.JobsRunning++
		}
	}
	return cs, nil
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
	e := Execution{
		ID:        s.newID(),
		Runtime:   req.Runtime,
		Status:    "created",
		CreatedAt: s.now().UTC(),
	}
	s.executions[e.ID] = e
	return e, nil
}
