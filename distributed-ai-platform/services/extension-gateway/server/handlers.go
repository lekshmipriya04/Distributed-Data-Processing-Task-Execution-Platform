package server

import (
	"encoding/json"
	"net/http"
)

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	s.writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *Server) handleClusterStatus(w http.ResponseWriter, r *http.Request) {
	cs, err := s.cfg.Store.ClusterStatus(r.Context())
	if err != nil {
		s.writeStoreError(w, r, err)
		return
	}
	s.writeJSON(w, http.StatusOK, cs)
}

func (s *Server) handleListNodes(w http.ResponseWriter, r *http.Request) {
	nodes, err := s.cfg.Store.ListNodes(r.Context())
	if err != nil {
		s.writeStoreError(w, r, err)
		return
	}
	s.writeJSON(w, http.StatusOK, map[string]any{"items": nodes, "total": len(nodes)})
}

func (s *Server) handleGetNode(w http.ResponseWriter, r *http.Request) {
	node, err := s.cfg.Store.GetNode(r.Context(), r.PathValue("id"))
	if err != nil {
		s.writeStoreError(w, r, err)
		return
	}
	s.writeJSON(w, http.StatusOK, node)
}

func (s *Server) handleListExecutions(w http.ResponseWriter, r *http.Request) {
	execs, err := s.cfg.Store.ListExecutions(r.Context())
	if err != nil {
		s.writeStoreError(w, r, err)
		return
	}
	s.writeJSON(w, http.StatusOK, map[string]any{"items": execs, "total": len(execs)})
}

func (s *Server) handleGetExecution(w http.ResponseWriter, r *http.Request) {
	exec, err := s.cfg.Store.GetExecution(r.Context(), r.PathValue("id"))
	if err != nil {
		s.writeStoreError(w, r, err)
		return
	}
	s.writeJSON(w, http.StatusOK, exec)
}

func (s *Server) handleCreateExecution(w http.ResponseWriter, r *http.Request) {
	var req ExecutionRequest
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&req); err != nil {
		s.writeError(w, r, http.StatusBadRequest, "invalid_body", "Request body is not valid JSON")
		return
	}
	if req.Runtime != "python" && req.Runtime != "spark" && req.Runtime != "ray" {
		s.writeError(w, r, http.StatusBadRequest, "invalid_runtime",
			"runtime must be one of python, spark, ray")
		return
	}
	exec, err := s.cfg.Store.CreateExecution(r.Context(), req)
	if err != nil {
		s.writeStoreError(w, r, err)
		return
	}
	s.writeJSON(w, http.StatusCreated, exec)
}
