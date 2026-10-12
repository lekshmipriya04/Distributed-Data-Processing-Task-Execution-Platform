"""Persistence + SSH orchestration for provider nodes.

Blocking paramiko work is pushed onto worker threads via ``asyncio.to_thread``
so the async event loop is never stalled. Credentials are encrypted before they
touch the database and decrypted only in-memory when a connection is opened.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from capabilities import detect_capabilities
from config import get_settings
from crypto import decrypt_secret, encrypt_secret
from models import SSHNode
from os_adapters import detect_adapter
from schemas import NodeConnectRequest, NodeRegisterRequest
from ssh_client import SSHExecutor
from ssh_transport import SSHTransport
from task_executor import detect_os, select_adapter

logger = structlog.get_logger(__name__)

# Capability fields carried from a detect result into persistence.
_CAP_KEYS = (
    "os_type", "gpu_vendor", "gpu_backend", "gpu_model", "gpu_count",
    "gpu_memory_gb", "python_version", "java_version", "spark_version",
    "ray_version", "execution_backend", "resource_enforcement",
)


class NodeService:
    def __init__(self, db_session: AsyncSession):
        self._db = db_session
        self._settings = get_settings()

    def _check_host_allowed(self, host: str) -> None:
        allowed = self._settings.allowed_hosts
        if allowed and host not in allowed:
            raise ValueError(f"Host {host} is not in the provider allowlist")

    def _transport_from_request(self, request: NodeConnectRequest) -> SSHTransport:
        return SSHTransport(
            host=request.host,
            port=request.port,
            username=request.username,
            password=request.password if request.auth_type == "password" else None,
            private_key=request.private_key if request.auth_type == "private_key" else None,
            connect_timeout=self._settings.connect_timeout,
        )

    def _transport_for(self, node: SSHNode) -> SSHTransport:
        secret = decrypt_secret(node.secret_encrypted)
        return SSHTransport(
            host=node.host,
            port=node.port,
            username=node.username,
            password=secret if node.auth_type == "password" else None,
            private_key=secret if node.auth_type == "private_key" else None,
            host_key_type=node.host_key_type,
            host_key_b64=node.host_key_b64,
            connect_timeout=self._settings.connect_timeout,
        )

    def _detect_sync(self, request: NodeConnectRequest) -> dict:
        """Blocking: SSH in, detect OS + full capabilities, capture host key."""
        transport = self._transport_from_request(request)
        try:
            transport.connect()
            os_type = detect_os(transport, timeout=self._settings.connect_timeout)
            caps = detect_capabilities(transport, select_adapter(os_type))
            result = {
                "status": "success",
                # legacy field names kept so the existing UI keeps working
                "detected_cpu": caps.cpu_total,
                "detected_memory_gb": caps.memory_total_gb,
                "detected_gpu": caps.gpu_count,
                "os_info": caps.os_version,
            }
            result.update({k: getattr(caps, k) for k in _CAP_KEYS})
            result.update(transport.capture_host_key())
            return result
        except Exception as exc:
            logger.error("ssh_detect_failed", host=request.host, error=str(exc))
            return {"status": "error", "message": str(exc)}
        finally:
            transport.close()

    # -- connect + detect (no persistence) --------------------------------
    async def connect_and_detect(self, request: NodeConnectRequest) -> dict:
        self._check_host_allowed(request.host)
        return await asyncio.to_thread(self._detect_sync, request)

    # -- register ---------------------------------------------------------
    async def register_node(self, request: NodeRegisterRequest, owner_id: str) -> SSHNode:
        self._check_host_allowed(request.host)
        secret = request.password if request.auth_type == "password" else request.private_key
        if not secret:
            raise ValueError("A password or private_key is required to register a node")

        hk_type = request.host_key_type
        hk_b64 = request.host_key_b64
        detected_cpu = request.detected_cpu
        detected_memory_gb = request.detected_memory_gb
        detected_gpu = request.detected_gpu
        os_info = request.os_info
        caps = {k: getattr(request, k) for k in _CAP_KEYS}

        # If the caller didn't carry the detected capabilities (e.g. a scripted
        # register), discover them now so every stored node has a full record.
        if request.os_type is None:
            connect_req = NodeConnectRequest(
                host=request.host, port=request.port, username=request.username,
                auth_type=request.auth_type, password=request.password,
                private_key=request.private_key,
            )
            detected = await asyncio.to_thread(self._detect_sync, connect_req)
            if detected.get("status") == "success":
                detected_cpu = detected.get("detected_cpu", detected_cpu)
                detected_memory_gb = detected.get("detected_memory_gb", detected_memory_gb)
                detected_gpu = detected.get("detected_gpu", detected_gpu)
                os_info = detected.get("os_info", os_info)
                caps = {k: detected.get(k, caps.get(k)) for k in _CAP_KEYS}
                hk_type = hk_type or detected.get("host_key_type")
                hk_b64 = hk_b64 or detected.get("host_key_b64")

        # Never persist an allocation larger than what the node actually has.
        allocated_cpu = request.allocated_cpu
        if detected_cpu:
            allocated_cpu = min(allocated_cpu, detected_cpu)
        allocated_memory_gb = request.allocated_memory_gb
        if detected_memory_gb:
            allocated_memory_gb = min(allocated_memory_gb, detected_memory_gb)

        node = SSHNode(
            owner_id=owner_id,
            name=request.name,
            host=request.host,
            port=request.port,
            username=request.username,
            auth_type=request.auth_type,
            secret_encrypted=encrypt_secret(secret),
            host_key_type=hk_type,
            host_key_b64=hk_b64,
            detected_cpu=detected_cpu,
            detected_memory_gb=detected_memory_gb,
            detected_gpu=detected_gpu,
            os_info=os_info,
            os_type=caps.get("os_type"),
            gpu_vendor=caps.get("gpu_vendor"),
            gpu_backend=caps.get("gpu_backend"),
            gpu_model=caps.get("gpu_model"),
            gpu_count=int(caps.get("gpu_count") or 0),
            gpu_memory_gb=caps.get("gpu_memory_gb"),
            python_version=caps.get("python_version"),
            java_version=caps.get("java_version"),
            spark_version=caps.get("spark_version"),
            ray_version=caps.get("ray_version"),
            execution_backend=caps.get("execution_backend") or "ssh",
            resource_enforcement=caps.get("resource_enforcement") or "best_effort",
            capabilities_json=json.dumps(caps),
            allocated_cpu=max(1, allocated_cpu),
            allocated_memory_gb=max(0.0, allocated_memory_gb),
            health_status="online",
            last_health_check=datetime.now(timezone.utc),
            status="online",
        )
        self._db.add(node)
        await self._db.commit()
        await self._db.refresh(node)
        logger.info("ssh_node_registered", node_id=str(node.id), host=node.host, os=node.os_type)
        return node

    # -- queries (owner-scoped) -------------------------------------------
    async def list_nodes(self, owner_id: str, skip: int = 0, limit: int = 100):
        total = await self._db.scalar(
            select(func.count()).select_from(SSHNode).where(SSHNode.owner_id == owner_id)
        )
        result = await self._db.execute(
            select(SSHNode)
            .where(SSHNode.owner_id == owner_id)
            .order_by(SSHNode.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all()), total or 0

    async def get_node(self, node_id: uuid.UUID, owner_id: str) -> Optional[SSHNode]:
        result = await self._db.execute(
            select(SSHNode).where(SSHNode.id == node_id, SSHNode.owner_id == owner_id)
        )
        return result.scalar_one_or_none()

    async def delete_node(self, node_id: uuid.UUID, owner_id: str) -> bool:
        node = await self.get_node(node_id, owner_id)
        if not node:
            return False
        await self._db.delete(node)
        await self._db.commit()
        logger.info("ssh_node_deleted", node_id=str(node_id))
        return True

    # -- runtime + health (master-driven, no agent) ----------------------
    def _runtime_sync(self, node: SSHNode) -> dict:
        transport = self._transport_for(node)
        try:
            transport.connect()
            os_type = node.os_type or detect_os(transport, timeout=self._settings.connect_timeout)
            caps = detect_capabilities(transport, select_adapter(os_type))
            ready = lambda v: "ready" if v else "missing"
            return {
                "os_type": os_type,
                "python": ready(caps.python_version),
                "java": ready(caps.java_version),
                "spark": ready(caps.spark_version),
                "ray": ready(caps.ray_version),
                "versions": {
                    "python": caps.python_version,
                    "java": caps.java_version,
                    "spark": caps.spark_version,
                    "ray": caps.ray_version,
                },
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}
        finally:
            transport.close()

    async def detect_runtime(self, node_id: uuid.UUID, owner_id: str) -> Optional[dict]:
        node = await self.get_node(node_id, owner_id)
        if not node:
            return None
        return await asyncio.to_thread(self._runtime_sync, node)

    def _health_sync(self, node: SSHNode) -> bool:
        transport = self._transport_for(node)
        try:
            return transport.test_connection().get("status") == "success"
        except Exception:
            return False
        finally:
            transport.close()

    async def health_check(self, node_id: uuid.UUID, owner_id: str) -> Optional[SSHNode]:
        node = await self.get_node(node_id, owner_id)
        if not node:
            return None
        ok = await asyncio.to_thread(self._health_sync, node)
        node.health_status = "online" if ok else "unreachable"
        node.last_health_check = datetime.now(timezone.utc)
        await self._db.commit()
        await self._db.refresh(node)
        logger.info("ssh_node_health", node_id=str(node_id), health=node.health_status)
        return node


def build_executor(node: SSHNode) -> SSHExecutor:
    """Construct an :class:`SSHExecutor` for a stored node (decrypts in-memory)."""
    settings = get_settings()
    secret = decrypt_secret(node.secret_encrypted)
    return SSHExecutor(
        host=node.host,
        port=node.port,
        username=node.username,
        password=secret if node.auth_type == "password" else None,
        private_key=secret if node.auth_type == "private_key" else None,
        host_key_type=node.host_key_type,
        host_key_b64=node.host_key_b64,
        connect_timeout=settings.connect_timeout,
        os_type=detect_adapter(node.os_info).os_type,
    )
