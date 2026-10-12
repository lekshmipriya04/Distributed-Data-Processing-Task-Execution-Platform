"""
API Gateway reverse proxy.
Streams responses from downstream services back to the client.

Fixes applied:
  BUG-01: Use StreamingResponse (not Response) so async byte streams are handled correctly.
  BUG-02: Always close the httpx.AsyncClient via BackgroundTask to prevent FD/memory leaks.
"""
import asyncio
import httpx
import structlog
from fastapi import Request, HTTPException
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

logger = structlog.get_logger(__name__)


async def proxy_request(request: Request, target_url: str) -> StreamingResponse:
    """
    Proxies the incoming request to the target_url.
    Forwards the Authorization header exactly as it arrives.
    Streams the response body back to the caller.
    """
    logger.info("proxying_request", method=request.method, target_url=target_url)

    # Forward all headers except host (let httpx set the correct Host for the target)
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}

    # Read the full request body (needed for POST/PUT/PATCH)
    body = await request.body()

    # Generous timeout: this is a reverse proxy in front of services that do
    # real work (HDFS writes on dataset upload, Spark/Ray submits, training
    # reads). httpx's 5s default made cold-HDFS uploads (~5-6s) surface as a
    # spurious 502 even though storage-service had already persisted the row.
    # Allow a fast connect but a long read/write for slow upstream operations.
    client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
    try:
        req = client.build_request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=body,
        )

        # stream=True keeps the response body as an async iterator — never fully buffered
        resp = await client.send(req, stream=True)

        # BUG-01 fix: StreamingResponse consumes the async iterator correctly.
        # BUG-02 fix: close_all() closes both the response stream AND the client
        #             so no file descriptors or connection pool slots are leaked.
        async def close_all() -> None:
            await resp.aclose()
            await client.aclose()

        return StreamingResponse(
            resp.aiter_raw(),
            status_code=resp.status_code,
            headers={
                k: v
                for k, v in resp.headers.items()
                # Drop transfer-encoding — Starlette re-encodes the stream
                if k.lower() != "transfer-encoding"
            },
            background=BackgroundTask(close_all),
        )

    except httpx.RequestError as e:
        # If we never got a response, close the client here directly
        await client.aclose()
        logger.error("proxy_request_failed", error=str(e), target_url=target_url)
        raise HTTPException(status_code=502, detail=f"Bad Gateway: {str(e)}")
