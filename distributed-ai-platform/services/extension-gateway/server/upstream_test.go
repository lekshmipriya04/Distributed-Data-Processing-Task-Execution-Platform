package server

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// errStore fails every call with err (for error-rendering tests).
type errStore struct{ err error }

func (e errStore) ListNodes(context.Context) ([]ComputeNode, error)     { return nil, e.err }
func (e errStore) GetNode(context.Context, string) (ComputeNode, error) { return ComputeNode{}, e.err }
func (e errStore) ClusterStatus(context.Context) (ClusterStatus, error) {
	return ClusterStatus{}, e.err
}
func (e errStore) ListRuntimes(context.Context) ([]Runtime, error)         { return nil, e.err }
func (e errStore) ListExecutions(context.Context) ([]Execution, error)     { return nil, e.err }
func (e errStore) GetExecution(context.Context, string) (Execution, error) { return Execution{}, e.err }
func (e errStore) CancelExecution(context.Context, string) (Execution, error) {
	return Execution{}, e.err
}
func (e errStore) CreateExecution(context.Context, ExecutionRequest) (Execution, error) {
	return Execution{}, e.err
}

const testSecret = "unit-test-secret"

// fakeExecutor is an httptest stand-in for ssh-executor that records every
// request it receives.
type fakeExecutor struct {
	mu       sync.Mutex
	requests []recorded
}

type recorded struct {
	Method, Path, Auth, RequestID string
	Body                          map[string]any
}

func (f *fakeExecutor) record(r *http.Request) recorded {
	rec := recorded{Method: r.Method, Path: r.URL.Path, Auth: r.Header.Get("Authorization"),
		RequestID: r.Header.Get("X-Request-Id")}
	if b, _ := io.ReadAll(r.Body); len(b) > 0 {
		_ = json.Unmarshal(b, &rec.Body)
	}
	f.mu.Lock()
	f.requests = append(f.requests, rec)
	f.mu.Unlock()
	return rec
}

func (f *fakeExecutor) all() []recorded {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]recorded(nil), f.requests...)
}

const (
	upNode1 = `{"id":"n1","owner_id":"alice","name":"laptop","host":"10.0.0.5","port":22,"username":"u",
		"auth_type":"managed_key","os_type":"linux","os_info":"Ubuntu 24.04","detected_cpu":8,
		"detected_memory_gb":16.0,"detected_gpu":0,"gpu_count":1,"runtimes":{"python":"3.12.1","go":"1.27"},
		"execution_backend":"ssh","resource_enforcement":"strict","allocated_cpu":4,"allocated_memory_gb":8.0,
		"status":"online","health_status":"online","last_health_check":"2026-10-16T10:00:00.123456+00:00",
		"created_at":"2026-10-01T00:00:00Z","updated_at":"2026-10-01T00:00:00Z"}`
	upNode2 = `{"id":"n2","owner_id":"alice","name":"pi","host":"10.0.0.6","port":22,"username":"u",
		"auth_type":"password","detected_cpu":0,"detected_memory_gb":0,"allocated_cpu":1,"allocated_memory_gb":1.0,
		"status":"online","health_status":"unreachable","created_at":"2026-10-01T00:00:00Z","updated_at":"2026-10-01T00:00:00Z"}`
	upBatchRunning = `{"id":"b1","owner_id":"alice","runtime":"node","status":"running","message":null,"total":3,
		"completed":1,"failed":0,"cancelled":0,"cpu_per_task":2,"timeout_seconds":60,"created_at":"2026-10-16T09:00:00"}`
	upBatchDone = `{"id":"b2","owner_id":"alice","runtime":"python","status":"completed_with_errors","message":"1 task failed",
		"total":2,"completed":1,"failed":1,"cancelled":0,"cpu_per_task":1,"timeout_seconds":120,
		"created_at":"2026-10-15T09:00:00+00:00","finished_at":"2026-10-15T09:05:00+00:00"}`
)

func (f *fakeExecutor) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	rec := f.record(r)
	w.Header().Set("Content-Type", "application/json")
	write := func(status int, body string) {
		w.WriteHeader(status)
		_, _ = io.WriteString(w, body)
	}
	switch rec.Method + " " + rec.Path {
	case "GET /api/v1/ssh/nodes":
		write(200, `{"items":[`+upNode1+`,`+upNode2+`],"total":2}`)
	case "GET /api/v1/ssh/nodes/n1":
		write(200, upNode1)
	case "GET /api/v1/ssh/nodes/n1/capacity":
		write(200, `{"node_id":"n1","name":"laptop","status":"online","cpu_total":4,"cpu_reserved":2,
			"cpu_available":2,"memory_total_gb":8.0,"memory_reserved_gb":1.5,"memory_available_gb":6.5,"active_leases":1}`)
	case "GET /api/v1/ssh/nodes/n2/capacity":
		write(500, `{"detail":"boom"}`)
	case "GET /api/v1/ssh/runtimes":
		write(200, `{"items":[{"id":"python","name":"Python","filename":"main.py","kind":"interpreted",
			"example_code":"print(input())","windows_supported":true,"available_node_ids":["n1"]},
			{"id":"custom","name":"Custom","filename":"","kind":"custom","example_code":"",
			"windows_supported":true,"available_node_ids":null}]}`)
	case "GET /api/v1/ssh/batches":
		write(200, `{"items":[`+upBatchRunning+`,`+upBatchDone+`]}`)
	case "GET /api/v1/ssh/batches/b1":
		write(200, upBatchRunning)
	case "POST /api/v1/ssh/batches/b1/cancel":
		write(200, strings.Replace(upBatchRunning, `"running"`, `"cancelling"`, 1))
	case "POST /api/v1/ssh/batches":
		switch rec.Body["runtime"] {
		case "cobol":
			write(400, `{"detail":"Unknown runtime 'cobol'"}`)
		case "zig":
			write(400, `{"detail":"No selected online node has runtime 'zig'"}`)
		default:
			b := map[string]any{"id": "b3", "owner_id": "alice", "runtime": rec.Body["runtime"], "status": "pending",
				"total": 1, "completed": 0, "failed": 0, "cancelled": 0, "cpu_per_task": 1, "timeout_seconds": 120,
				"created_at": "2026-10-16T11:00:00Z"}
			w.WriteHeader(201)
			_ = json.NewEncoder(w).Encode(b)
		}
	default:
		write(404, `{"detail":"Not Found"}`)
	}
}

// newUpstreamGateway wires a Server -> UpstreamStore -> fake ssh-executor with
// real HS256 validation.
func newUpstreamGateway(t *testing.T, upstream http.Handler, timeout time.Duration) (*Server, *httptest.Server) {
	t.Helper()
	ts := httptest.NewServer(upstream)
	t.Cleanup(ts.Close)
	st, err := NewUpstreamStore(UpstreamConfig{BaseURL: ts.URL, Timeout: timeout})
	if err != nil {
		t.Fatal(err)
	}
	return New(Config{Store: st, Validate: NewHS256Validator([]byte(testSecret), nil)}), ts
}

func userToken(t *testing.T, sub string) string {
	t.Helper()
	now := time.Now().Unix()
	return signHS256(t, testSecret, map[string]any{"sub": sub, "iat": now, "exp": now + 600})
}

func TestUpstreamListNodesMapsAndForwardsToken(t *testing.T) {
	fake := &fakeExecutor{}
	s, _ := newUpstreamGateway(t, fake, time.Second)
	tok := userToken(t, "alice")

	w := do(t, s, "GET", "/nodes", tok, nil)
	if w.Code != 200 {
		t.Fatalf("status = %d: %s", w.Code, w.Body.String())
	}
	out := decodeBody[struct {
		Items []ComputeNode `json:"items"`
		Total int           `json:"total"`
	}](t, w)
	if out.Total != 2 {
		t.Fatalf("total = %d", out.Total)
	}
	n1, n2 := out.Items[0], out.Items[1]
	if n1.ID != "n1" || n1.Name != "laptop" || n1.Host != "10.0.0.5" || n1.Status != "online" ||
		n1.OSType != "linux" || n1.OSVersion != "Ubuntu 24.04" || n1.ResourceEnforcement != "strict" {
		t.Fatalf("n1 identity = %+v", n1)
	}
	if *n1.CPUTotal != 8 || *n1.CPUAllocatable != 4 || *n1.CPUReserved != 2 ||
		*n1.MemoryTotalGB != 16 || *n1.MemoryAllocatableGB != 8 || *n1.MemoryReservedGB != 1.5 ||
		*n1.GPUCount != 1 || n1.ActiveTasks != 1 {
		t.Fatalf("n1 capacity = %+v", n1)
	}
	if n1.Runtimes["go"] != "1.27" || n1.LastHealthCheck == nil {
		t.Fatalf("n1 runtimes/health = %+v", n1)
	}
	// Undetected values stay unknown; failed capacity lookup leaves reserved nil.
	if n2.Status != "unreachable" || n2.CPUTotal != nil || n2.MemoryTotalGB != nil || n2.GPUCount != nil ||
		n2.CPUReserved != nil || *n2.CPUAllocatable != 1 {
		t.Fatalf("n2 = %+v", n2)
	}

	reqs := fake.all()
	if len(reqs) != 3 {
		t.Fatalf("upstream calls = %d, want 3 (list + 2 capacity)", len(reqs))
	}
	for _, r := range reqs {
		if r.Auth != "Bearer "+tok {
			t.Fatalf("%s %s forwarded auth %q, want the caller's token", r.Method, r.Path, r.Auth)
		}
		if r.RequestID == "" {
			t.Fatalf("%s %s missing X-Request-Id", r.Method, r.Path)
		}
	}
}

func TestUpstreamGetNode(t *testing.T) {
	fake := &fakeExecutor{}
	s, _ := newUpstreamGateway(t, fake, time.Second)
	n := decodeBody[ComputeNode](t, do(t, s, "GET", "/nodes/n1", userToken(t, "alice"), nil))
	if n.ID != "n1" || n.CPUReserved == nil || *n.CPUReserved != 2 {
		t.Fatalf("node = %+v", n)
	}
	if w := do(t, s, "GET", "/nodes/missing", userToken(t, "alice"), nil); w.Code != 404 {
		t.Fatalf("missing node = %d, want 404", w.Code)
	}
}

func TestUpstreamClusterStatus(t *testing.T) {
	s, _ := newUpstreamGateway(t, &fakeExecutor{}, time.Second)
	cs := decodeBody[ClusterStatus](t, do(t, s, "GET", "/cluster/status", userToken(t, "alice"), nil))
	want := ClusterStatus{NodesTotal: 2, NodesOnline: 1, CPUTotal: 8, CPUAllocatable: 5, CPUReserved: 2,
		MemoryTotalGB: 16, MemoryAllocatableGB: 9, MemoryReservedGB: 1.5, JobsRunning: 1}
	if cs != want {
		t.Fatalf("cluster = %+v, want %+v", cs, want)
	}
}

func TestUpstreamRuntimes(t *testing.T) {
	s, _ := newUpstreamGateway(t, &fakeExecutor{}, time.Second)
	out := decodeBody[struct {
		Items []Runtime `json:"items"`
	}](t, do(t, s, "GET", "/runtimes", userToken(t, "alice"), nil))
	if len(out.Items) != 2 {
		t.Fatalf("runtimes = %+v", out)
	}
	py := out.Items[0]
	if py.ID != "python" || py.Filename != "main.py" || py.Kind != "interpreted" || !py.WindowsSupported ||
		py.ExampleCode == "" || len(py.AvailableNodeIDs) != 1 {
		t.Fatalf("python runtime = %+v", py)
	}
	if out.Items[1].AvailableNodeIDs == nil {
		t.Fatal("availableNodeIds must be [] not null")
	}
}

func TestUpstreamExecutions(t *testing.T) {
	fake := &fakeExecutor{}
	s, _ := newUpstreamGateway(t, fake, time.Second)
	tok := userToken(t, "alice")

	list := decodeBody[struct {
		Items []Execution `json:"items"`
		Total int         `json:"total"`
	}](t, do(t, s, "GET", "/executions", tok, nil))
	if list.Total != 2 {
		t.Fatalf("executions = %+v", list)
	}
	b1, b2 := list.Items[0], list.Items[1]
	if b1.ID != "b1" || b1.Runtime != "node" || b1.Status != "running" || b1.Total != 3 || b1.Completed != 1 ||
		b1.CPUPerTask != 2 || b1.TimeoutSeconds != 60 || b1.EndedAt != nil || b1.CreatedAt.IsZero() {
		t.Fatalf("b1 = %+v", b1)
	}
	if b2.Status != "completed_with_errors" || b2.Message != "1 task failed" || b2.Failed != 1 || b2.EndedAt == nil {
		t.Fatalf("b2 = %+v", b2)
	}

	if e := decodeBody[Execution](t, do(t, s, "GET", "/executions/b1", tok, nil)); e.ID != "b1" {
		t.Fatalf("get = %+v", e)
	}

	w := do(t, s, "POST", "/executions/b1/cancel", tok, nil)
	if w.Code != 200 {
		t.Fatalf("cancel = %d: %s", w.Code, w.Body.String())
	}
	if e := decodeBody[Execution](t, w); e.Status != "cancelling" {
		t.Fatalf("cancel status = %q", e.Status)
	}
}

func TestUpstreamCreateExecutionMapsBody(t *testing.T) {
	fake := &fakeExecutor{}
	s, _ := newUpstreamGateway(t, fake, time.Second)
	tok := userToken(t, "alice")

	w := do(t, s, "POST", "/executions", tok, ExecutionRequest{
		Runtime: "custom", Code: "pub fn main() void {}", Inputs: []string{"1", "2"}, NodeIDs: []string{"n1"},
		CustomCommand: "zig run {file}", CustomFilename: "main.zig", CPUPerTask: 2, TimeoutSeconds: 30,
	})
	if w.Code != http.StatusCreated {
		t.Fatalf("create = %d: %s", w.Code, w.Body.String())
	}
	if e := decodeBody[Execution](t, w); e.ID != "b3" || e.Runtime != "custom" || e.Status != "pending" {
		t.Fatalf("created = %+v", e)
	}
	reqs := fake.all()
	last := reqs[len(reqs)-1]
	if last.Method != "POST" || last.Path != "/api/v1/ssh/batches" || last.Auth != "Bearer "+tok {
		t.Fatalf("upstream call = %+v", last)
	}
	b := last.Body
	if b["runtime"] != "custom" || b["code"] != "pub fn main() void {}" || b["custom_command"] != "zig run {file}" ||
		b["custom_filename"] != "main.zig" || b["cpu_per_task"] != float64(2) || b["timeout_seconds"] != float64(30) {
		t.Fatalf("upstream body = %+v", b)
	}
	if in, _ := b["inputs"].([]any); len(in) != 2 {
		t.Fatalf("inputs = %+v", b["inputs"])
	}
	if ids, _ := b["node_ids"].([]any); len(ids) != 1 || ids[0] != "n1" {
		t.Fatalf("node_ids = %+v", b["node_ids"])
	}

	// Minimal body: optional fields are omitted so ssh-executor defaults apply.
	do(t, s, "POST", "/executions", tok, ExecutionRequest{Runtime: "ruby", Code: "puts 1"})
	reqs = fake.all()
	min := reqs[len(reqs)-1].Body
	for _, k := range []string{"inputs", "node_ids", "custom_command", "custom_filename", "cpu_per_task", "timeout_seconds"} {
		if _, ok := min[k]; ok {
			t.Fatalf("minimal body should omit %s: %+v", k, min)
		}
	}
}

func TestUpstreamValidationDetailPassesThrough(t *testing.T) {
	s, _ := newUpstreamGateway(t, &fakeExecutor{}, time.Second)
	w := do(t, s, "POST", "/executions", userToken(t, "alice"), ExecutionRequest{Runtime: "zig", Code: "x"})
	if w.Code != 400 {
		t.Fatalf("status = %d", w.Code)
	}
	if e := decodeBody[apiError](t, w); e.Code != "invalid_request" || !strings.Contains(e.Error, "runtime 'zig'") {
		t.Fatalf("error = %+v", e)
	}
}

func TestUpstreamErrorMapping(t *testing.T) {
	cases := []struct {
		name       string
		status     int
		body       string
		wantStatus int
		wantCode   string
		wantMsg    string // substring; "" = don't check
	}{
		{"400 detail", 400, `{"detail":"code must not be empty"}`, 400, "invalid_request", "code must not be empty"},
		{"422 list", 422, `{"detail":[{"loc":["body","node_ids",0],"msg":"Input should be a valid UUID","type":"uuid"}]}`,
			400, "invalid_request", "0: Input should be a valid UUID"},
		{"401", 401, `{"detail":"Token has expired"}`, 401, "invalid_token", "Authentication failed"},
		{"403", 403, `{"detail":"nope"}`, 403, "forbidden", ""},
		{"404", 404, `{"detail":"Batch not found"}`, 404, "not_found", ""},
		{"409", 409, `{"detail":"host_key_mismatch"}`, 409, "conflict", "host_key_mismatch"},
		{"429", 429, `{"detail":"slow down"}`, 429, "rate_limited", ""},
		{"500 hides detail", 500, `{"detail":"psycopg error: password=hunter2"}`, 502, "upstream_error", ""},
		{"503", 503, `upstream down`, 502, "upstream_error", ""},
		{"504", 504, ``, 504, "upstream_timeout", ""},
		{"non-json 200", 200, `<html>`, 502, "upstream_bad_response", ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s, _ := newUpstreamGateway(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(tc.status)
				_, _ = io.WriteString(w, tc.body)
			}), time.Second)
			w := do(t, s, "GET", "/executions/b9", userToken(t, "alice"), nil)
			if w.Code != tc.wantStatus {
				t.Fatalf("status = %d, want %d (%s)", w.Code, tc.wantStatus, w.Body.String())
			}
			e := decodeBody[apiError](t, w)
			if e.Code != tc.wantCode {
				t.Fatalf("code = %q, want %q", e.Code, tc.wantCode)
			}
			if tc.wantMsg != "" && !strings.Contains(e.Error, tc.wantMsg) {
				t.Fatalf("message = %q, want it to contain %q", e.Error, tc.wantMsg)
			}
			if strings.Contains(w.Body.String(), "hunter2") {
				t.Fatal("5xx upstream detail leaked to the client")
			}
		})
	}
}

func TestUpstreamTimeout(t *testing.T) {
	release := make(chan struct{})
	s, _ := newUpstreamGateway(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-release:
		case <-r.Context().Done():
		}
	}), 50*time.Millisecond)
	defer close(release)
	w := do(t, s, "GET", "/executions", userToken(t, "alice"), nil)
	if w.Code != http.StatusGatewayTimeout {
		t.Fatalf("status = %d, want 504", w.Code)
	}
	if e := decodeBody[apiError](t, w); e.Code != "upstream_timeout" {
		t.Fatalf("code = %q", e.Code)
	}
}

func TestUpstreamUnavailable(t *testing.T) {
	s, ts := newUpstreamGateway(t, &fakeExecutor{}, time.Second)
	ts.Close()
	w := do(t, s, "GET", "/nodes", userToken(t, "alice"), nil)
	if w.Code != http.StatusBadGateway {
		t.Fatalf("status = %d, want 502", w.Code)
	}
	if e := decodeBody[apiError](t, w); e.Code != "upstream_unavailable" {
		t.Fatalf("code = %q", e.Code)
	}
}

func TestUpstreamRejectsBadTokenBeforeCallingUpstream(t *testing.T) {
	fake := &fakeExecutor{}
	s, _ := newUpstreamGateway(t, fake, time.Second)
	bad := signHS256(t, "other-secret", map[string]any{"sub": "alice", "iat": time.Now().Unix(), "exp": time.Now().Unix() + 60})
	for _, tok := range []string{"", "not-a-jwt", bad} {
		if w := do(t, s, "GET", "/nodes", tok, nil); w.Code != http.StatusUnauthorized {
			t.Fatalf("token %q: status = %d, want 401", tok, w.Code)
		}
	}
	if n := len(fake.all()); n != 0 {
		t.Fatalf("upstream was called %d times with an invalid token", n)
	}
}

func TestNewUpstreamStoreURLHandling(t *testing.T) {
	for _, bad := range []string{"", "ssh-executor:8011", "ftp://x", "http://"} {
		if _, err := NewUpstreamStore(UpstreamConfig{BaseURL: bad}); err == nil {
			t.Fatalf("expected error for %q", bad)
		}
	}
	for in, want := range map[string]string{
		"http://ssh-executor:8011":             "http://ssh-executor:8011/api/v1/ssh",
		"http://ssh-executor:8011/":            "http://ssh-executor:8011/api/v1/ssh",
		"http://ssh-executor:8011/api/v1/ssh/": "http://ssh-executor:8011/api/v1/ssh",
	} {
		st, err := NewUpstreamStore(UpstreamConfig{BaseURL: in})
		if err != nil || st.base != want {
			t.Fatalf("%q -> %q (%v), want %q", in, st.base, err, want)
		}
	}
}
