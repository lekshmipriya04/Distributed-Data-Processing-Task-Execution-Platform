// Package server implements the extension-facing control-plane HTTP service.
//
// This is the Go backend the Distributed Compute VS Code extension talks to. It
// adapts/aggregates the existing platform (Python ssh-executor, scheduler,
// resource-manager, Postgres) behind the stable JSON contract the extension
// expects. Data is served through the Store interface; no values are fabricated
// in the default wiring.
package server

import "time"

// ComputeNode is the extension-facing view of an SSH compute node. Pointer
// fields are nil when the backend has not reported a value (shown as "Unknown"
// in the UI) rather than a misleading zero.
type ComputeNode struct {
	ID                  string    `json:"id"`
	Name                string    `json:"name"`
	OSType              string    `json:"osType,omitempty"`
	OSVersion           string    `json:"osVersion,omitempty"`
	Status              string    `json:"status"` // online|busy|degraded|offline|unreachable|draining|unknown
	CPUTotal            *int      `json:"cpuTotal,omitempty"`
	CPUAllocatable      *int      `json:"cpuAllocatable,omitempty"`
	CPUReserved         *int      `json:"cpuReserved,omitempty"`
	MemoryTotalGB       *float64  `json:"memoryTotalGb,omitempty"`
	MemoryAllocatableGB *float64  `json:"memoryAllocatableGb,omitempty"`
	MemoryReservedGB    *float64  `json:"memoryReservedGb,omitempty"`
	GPUCount            *int      `json:"gpuCount,omitempty"`
	ResourceEnforcement string    `json:"resourceEnforcement,omitempty"` // strict|best_effort
	ActiveTasks         int       `json:"activeTasks"`
	LastHealthCheck     *time.Time `json:"lastHealthCheck,omitempty"`
}

// Execution is a submitted job/execution record.
type Execution struct {
	ID        string     `json:"id"`
	Runtime   string     `json:"runtime"` // python|spark|ray
	Status    string     `json:"status"`  // created|queued|scheduling|running|completed|failed|cancelled|timed_out|node_lost
	Total     int        `json:"total"`
	Completed int        `json:"completed"`
	Failed    int        `json:"failed"`
	CreatedAt time.Time  `json:"createdAt"`
	EndedAt   *time.Time `json:"endedAt,omitempty"`
}

// ExecutionRequest is the body for POST /executions.
type ExecutionRequest struct {
	Runtime        string `json:"runtime"`
	Code           string `json:"code,omitempty"`
	DatasetID      string `json:"datasetId,omitempty"`
	CPUPerTask     int    `json:"cpuPerTask,omitempty"`
	MemoryPerTask  float64 `json:"memoryPerTaskGb,omitempty"`
	TimeoutSeconds int    `json:"timeoutSeconds,omitempty"`
}

// ClusterStatus is the aggregate shown in the cluster dashboard. The four
// capacity concepts are kept distinct (capacity vs allocatable vs reserved vs
// measured) rather than conflated.
type ClusterStatus struct {
	NodesTotal          int     `json:"nodesTotal"`
	NodesOnline         int     `json:"nodesOnline"`
	CPUTotal            int     `json:"cpuTotal"`
	CPUAllocatable      int     `json:"cpuAllocatable"`
	CPUReserved         int     `json:"cpuReserved"`
	MemoryTotalGB       float64 `json:"memoryTotalGb"`
	MemoryAllocatableGB float64 `json:"memoryAllocatableGb"`
	MemoryReservedGB    float64 `json:"memoryReservedGb"`
	JobsRunning         int     `json:"jobsRunning"`
}

// apiError is the uniform error envelope.
type apiError struct {
	Error     string `json:"error"`
	Code      string `json:"code"`
	RequestID string `json:"requestId,omitempty"`
}
