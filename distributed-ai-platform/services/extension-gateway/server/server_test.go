package server

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func intp(i int) *int           { return &i }
func fp(f float64) *float64     { return &f }

func newTestServer(seed ...ComputeNode) *Server {
	st := NewMemStore()
	for _, n := range seed {
		st.SeedNode(n)
	}
	return New(Config{Store: st})
}

func do(t *testing.T, s *Server, method, path, token string, body any) *httptest.ResponseRecorder {
	t.Helper()
	var r *http.Request
	if body != nil {
		b, _ := json.Marshal(body)
		r = httptest.NewRequest(method, path, bytes.NewReader(b))
	} else {
		r = httptest.NewRequest(method, path, nil)
	}
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	w := httptest.NewRecorder()
	s.Handler().ServeHTTP(w, r)
	return w
}

func TestHealthNeedsNoAuth(t *testing.T) {
	s := newTestServer()
	w := do(t, s, "GET", "/health", "", nil)
	if w.Code != http.StatusOK {
		t.Fatalf("health = %d, want 200", w.Code)
	}
	if got := w.Header().Get("X-Request-Id"); got == "" {
		t.Fatal("expected X-Request-Id header")
	}
}

func TestAuthRequired(t *testing.T) {
	s := newTestServer()
	if w := do(t, s, "GET", "/nodes", "", nil); w.Code != http.StatusUnauthorized {
		t.Fatalf("no-token /nodes = %d, want 401", w.Code)
	}
	if w := do(t, s, "GET", "/nodes", "tok", nil); w.Code != http.StatusOK {
		t.Fatalf("with-token /nodes = %d, want 200", w.Code)
	}
}

func TestListNodesEmptyIsHonest(t *testing.T) {
	s := newTestServer()
	w := do(t, s, "GET", "/nodes", "tok", nil)
	var out struct {
		Items []ComputeNode `json:"items"`
		Total int           `json:"total"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	if out.Total != 0 || len(out.Items) != 0 {
		t.Fatalf("expected empty node list, got total=%d", out.Total)
	}
}

func TestClusterStatusAggregates(t *testing.T) {
	s := newTestServer(
		ComputeNode{ID: "a", Name: "A", Status: "online", CPUTotal: intp(8), CPUAllocatable: intp(2), MemoryTotalGB: fp(16)},
		ComputeNode{ID: "b", Name: "B", Status: "offline", CPUTotal: intp(4), CPUAllocatable: intp(4), MemoryTotalGB: fp(32)},
	)
	w := do(t, s, "GET", "/cluster/status", "tok", nil)
	var cs ClusterStatus
	if err := json.Unmarshal(w.Body.Bytes(), &cs); err != nil {
		t.Fatal(err)
	}
	if cs.NodesTotal != 2 || cs.NodesOnline != 1 {
		t.Fatalf("nodes total/online = %d/%d, want 2/1", cs.NodesTotal, cs.NodesOnline)
	}
	if cs.CPUTotal != 12 || cs.CPUAllocatable != 6 {
		t.Fatalf("cpu total/alloc = %d/%d, want 12/6", cs.CPUTotal, cs.CPUAllocatable)
	}
	if cs.MemoryTotalGB != 48 {
		t.Fatalf("mem total = %v, want 48", cs.MemoryTotalGB)
	}
}

func TestGetNodeNotFound(t *testing.T) {
	s := newTestServer()
	if w := do(t, s, "GET", "/nodes/missing", "tok", nil); w.Code != http.StatusNotFound {
		t.Fatalf("missing node = %d, want 404", w.Code)
	}
}

func TestCreateExecutionValidatesRuntime(t *testing.T) {
	s := newTestServer()
	if w := do(t, s, "POST", "/executions", "tok", ExecutionRequest{Runtime: "cobol"}); w.Code != http.StatusBadRequest {
		t.Fatalf("bad runtime = %d, want 400", w.Code)
	}
	w := do(t, s, "POST", "/executions", "tok", ExecutionRequest{Runtime: "python"})
	if w.Code != http.StatusCreated {
		t.Fatalf("create = %d, want 201", w.Code)
	}
	var e Execution
	if err := json.Unmarshal(w.Body.Bytes(), &e); err != nil {
		t.Fatal(err)
	}
	if e.ID == "" || e.Status != "created" {
		t.Fatalf("unexpected execution %+v", e)
	}
}

func TestCreateExecutionRejectsUnknownFields(t *testing.T) {
	s := newTestServer()
	r := httptest.NewRequest("POST", "/executions", bytes.NewReader([]byte(`{"runtime":"python","bogus":1}`)))
	r.Header.Set("Authorization", "Bearer tok")
	w := httptest.NewRecorder()
	s.Handler().ServeHTTP(w, r)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("unknown field = %d, want 400", w.Code)
	}
}

func TestRoundTripExecution(t *testing.T) {
	s := newTestServer()
	created := do(t, s, "POST", "/executions", "tok", ExecutionRequest{Runtime: "ray"})
	var e Execution
	_ = json.Unmarshal(created.Body.Bytes(), &e)
	got := do(t, s, "GET", "/executions/"+e.ID, "tok", nil)
	if got.Code != http.StatusOK {
		t.Fatalf("get execution = %d, want 200", got.Code)
	}
	_ = context.Background()
}
