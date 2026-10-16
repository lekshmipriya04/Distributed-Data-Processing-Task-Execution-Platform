# Extension gateway (Go). Build context is the repo's distributed-ai-platform/
# directory, e.g.:
#   docker build -f services/extension-gateway/Dockerfile .
#
# Runtime env: LISTEN_ADDR (default :8090), UPSTREAM_URL (http://ssh-executor:8011),
# JWT_SECRET (same as the Python services), ENVIRONMENT, UPSTREAM_TIMEOUT_SECONDS.

FROM golang:1.27-alpine AS build
WORKDIR /src
ENV CGO_ENABLED=0 GOTOOLCHAIN=local
# Module has no third-party dependencies (stdlib only), so there is no go.sum
# to download against; copying go.mod first still keeps the layer cache useful.
COPY services/extension-gateway/go.mod ./
COPY services/extension-gateway/ ./
RUN go build -trimpath -ldflags="-s -w" -o /out/extension-gateway .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/extension-gateway /extension-gateway
USER nonroot:nonroot
EXPOSE 8090
# The image has no shell or curl; the binary probes its own /health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD ["/extension-gateway", "-healthcheck"]
ENTRYPOINT ["/extension-gateway"]
