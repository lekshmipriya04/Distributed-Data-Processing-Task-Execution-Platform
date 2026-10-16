package server

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func intp(i int) *int       { return &i }
func fp(f float64) *float64 { return &f }

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

func decodeBody[T any](t *testing.T, w *httptest.ResponseRecorder) T {
	t.Helper()
	var out T
	if err := json.Unmarshal(w.Body.Bytes(), &out); err != nil {
		t.Fatalf("decode %q: %v", w.Body.String(), err)
	}
	return out
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
	out := decodeBody[struct {
		Items []ComputeNode `json:"items"`
		Total int           `json:"total"`
	}](t, do(t, s, "GET", "/nodes", "tok", nil))
	if out.Total != 0 || len(out.Items) != 0 {
		t.Fatalf("expected empty node list, got total=%d", out.Total)
	}
}

func TestClusterStatusAggregates(t *testing.T) {
	s := newTestServer(
		ComputeNode{ID: "a", Name: "A", Status: "online", CPUTotal: intp(8), CPUAllocatable: intp(2), MemoryTotalGB: fp(16)},
		ComputeNode{ID: "b", Name: "B", Status: "offline", CPUTotal: intp(4), CPUAllocatable: intp(4), MemoryTotalGB: fp(32)},
	)
	cs := decodeBody[ClusterStatus](t, do(t, s, "GET", "/cluster/status", "tok", nil))
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

func TestCreateExecutionValidation(t *testing.T) {
	s := newTestServer()
	cases := []struct {
		name string
		req  ExecutionRequest
		code string
	}{
		{"empty runtime", ExecutionRequest{Code: "x"}, "invalid_runtime"},
		{"runtime with spaces", ExecutionRequest{Runtime: "py thon", Code: "x"}, "invalid_runtime"},
		{"runtime too long", ExecutionRequest{Runtime: "abcdefghijklmnopqrstuvwxyz0123456789", Code: "x"}, "invalid_runtime"},
		{"empty code", ExecutionRequest{Runtime: "python", Code: "  "}, "invalid_code"},
		{"negative cpu", ExecutionRequest{Runtime: "python", Code: "x", CPUPerTask: -1}, "invalid_cpu"},
		{"negative timeout", ExecutionRequest{Runtime: "python", Code: "x", TimeoutSeconds: -5}, "invalid_timeout"},
		{"custom without {file}", ExecutionRequest{Runtime: "custom", Code: "x", CustomCommand: "zig run main.zig"}, "invalid_custom_command"},
		{"custom bad filename", ExecutionRequest{Runtime: "custom", Code: "x", CustomCommand: "zig run {file}", CustomFilename: "../evil"}, "invalid_custom_filename"},
		{"custom fields on builtin", ExecutionRequest{Runtime: "python", Code: "x", CustomCommand: "python {file}"}, "invalid_custom_command"},
		{"blank node id", ExecutionRequest{Runtime: "python", Code: "x", NodeIDs: []string{""}}, "invalid_node_ids"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			w := do(t, s, "POST", "/executions", "tok", tc.req)
			if w.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, want 400 (%s)", w.Code, w.Body.String())
			}
			if got := decodeBody[apiError](t, w).Code; got != tc.code {
				t.Fatalf("code = %q, want %q", got, tc.code)
			}
		})
	}
}

func TestCreateExecutionAcceptsAnyRuntimeID(t *testing.T) {
	s := newTestServer()
	for _, rt := range []string{"python", "bash", "node", "go", "cpp", "csharp", "custom"} {
		req := ExecutionRequest{Runtime: rt, Code: "print(1)"}
		if rt == "custom" {
			req.CustomCommand = "zig run {file}"
			req.CustomFilename = "main.zig"
		}
		w := do(t, s, "POST", "/executions", "tok", req)
		if w.Code != http.StatusCreated {
			t.Fatalf("runtime %s: create = %d, want 201 (%s)", rt, w.Code, w.Body.String())
		}
		e := decodeBody[Execution](t, w)
		if e.ID == "" || e.Status != "pending" || e.Runtime != rt {
			t.Fatalf("unexpected execution %+v", e)
		}
	}
}

func TestCreateExecutionRejectsUnknownFields(t *testing.T) {
	s := newTestServer()
	r := httptest.NewRequest("POST", "/executions", bytes.NewReader([]byte(`{"runtime":"python","code":"x","bogus":1}`)))
	r.Header.Set("Authorization", "Bearer tok")
	w := httptest.NewRecorder()
	s.Handler().ServeHTTP(w, r)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("unknown field = %d, want 400", w.Code)
	}
}

func TestRoundTripAndCancelExecution(t *testing.T) {
	s := newTestServer()
	e := decodeBody[Execution](t, do(t, s, "POST", "/executions", "tok",
		ExecutionRequest{Runtime: "bash", Code: "cat", Inputs: []string{"a", "b"}}))
	if e.Total != 2 {
		t.Fatalf("total = %d, want 2", e.Total)
	}
	if got := do(t, s, "GET", "/executions/"+e.ID, "tok", nil); got.Code != http.StatusOK {
		t.Fatalf("get execution = %d, want 200", got.Code)
	}
	w := do(t, s, "POST", "/executions/"+e.ID+"/cancel", "tok", nil)
	if w.Code != http.StatusOK {
		t.Fatalf("cancel = %d, want 200", w.Code)
	}
	if c := decodeBody[Execution](t, w); c.Status != "cancelled" || c.EndedAt == nil {
		t.Fatalf("cancelled execution = %+v", c)
	}
	if w := do(t, s, "POST", "/executions/nope/cancel", "tok", nil); w.Code != http.StatusNotFound {
		t.Fatalf("cancel missing = %d, want 404", w.Code)
	}
}

func TestListRuntimesMemStore(t *testing.T) {
	st := NewMemStore()
	st.SeedRuntime(Runtime{ID: "python", Name: "Python", AvailableNodeIDs: []string{"n1"}})
	s := New(Config{Store: st})
	out := decodeBody[struct {
		Items []Runtime `json:"items"`
		Total int       `json:"total"`
	}](t, do(t, s, "GET", "/runtimes", "tok", nil))
	if out.Total != 1 || out.Items[0].ID != "python" {
		t.Fatalf("runtimes = %+v", out)
	}
}

func TestStatusErrorRendering(t *testing.T) {
	s := New(Config{Store: errStore{err: &StatusError{Status: 409, Code: "conflict", Message: "busy"}}})
	w := do(t, s, "GET", "/nodes", "tok", nil)
	if w.Code != http.StatusConflict {
		t.Fatalf("status = %d, want 409", w.Code)
	}
	if e := decodeBody[apiError](t, w); e.Code != "conflict" || e.Error != "busy" || e.RequestID == "" {
		t.Fatalf("envelope = %+v", e)
	}
}

func TestUpdateNodeSliceValidationAndMemStore(t *testing.T) {
	s := newTestServer(ComputeNode{ID: "n1", Name: "rig", Status: "online", CPUAllocatable: intp(2)})
	for _, body := range []any{
		map[string]any{},
		map[string]any{"cpuAllocatable": 0},
		map[string]any{"gpuMemoryAllocatableGb": -1},
		map[string]any{"bogus": 1},
	} {
		if w := do(t, s, "PATCH", "/nodes/n1", "tok", body); w.Code != http.StatusBadRequest {
			t.Fatalf("%v = %d, want 400", body, w.Code)
		}
	}
	if w := do(t, s, "PATCH", "/nodes/missing", "tok", NodeSliceRequest{CPUAllocatable: intp(1)}); w.Code != http.StatusNotFound {
		t.Fatalf("missing = %d", w.Code)
	}
	w := do(t, s, "PATCH", "/nodes/n1", "tok", NodeSliceRequest{CPUAllocatable: intp(4), GPUMemoryAllocatableGB: fp(8)})
	n := decodeBody[ComputeNode](t, w)
	if w.Code != 200 || *n.CPUAllocatable != 4 || *n.GPUMemoryAllocatableGB != 8 {
		t.Fatalf("patch = %d %+v", w.Code, n)
	}
}

func TestCreateExecutionRejectsBadPolicy(t *testing.T) {
	s := newTestServer()
	w := do(t, s, "POST", "/executions", "tok", map[string]any{"runtime": "python", "code": "x", "executionPolicy": "turbo"})
	if w.Code != http.StatusBadRequest {
		t.Fatalf("bad policy = %d", w.Code)
	}
}
