"""Persistence + SSH orchestration for provider nodes.

Blocking paramiko work is pushed onto worker threads via ``asyncio.to_thread``
so the async event loop is never stalled. Credentials are encrypted before they
touch the database and decrypted only in-memory when a connection is opened.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Optional

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from crypto import decrypt_secret, encrypt_secret
from models import SSHNode
from schemas import NodeConnectRequest, NodeRegisterRequest
from ssh_client import SSHExecutor

logger = structlog.get_logger(__name__)


class NodeService:
    def __init__(self, db_session: AsyncSession):
        self._db = db_session
        self._settings = get_settings()

    def _check_host_allowed(self, host: str) -> None:
        allowed = self._settings.allowed_hosts
        if allowed and host not in allowed:
            raise ValueError(f"Host {host} is not in the provider allowlist")

    # -- connect + detect (no persistence) --------------------------------
    async def connect_and_detect(self, request: NodeConnectRequest) -> dict:
        self._check_host_allowed(request.host)
        executor = SSHExecutor(
            host=request.host,
            port=request.port,
            username=request.username,
            password=request.password if request.auth_type == "password" else None,
            private_key=request.private_key if request.auth_type == "private_key" else None,
            connect_timeout=self._settings.connect_timeout,
        )
        return await asyncio.to_thread(executor.detect_resources)

    # -- register ---------------------------------------------------------
    async def register_node(self, request: NodeRegisterRequest, owner_id: str) -> SSHNode:
        self._check_host_allowed(request.host)
        secret = request.password if request.auth_type == "password" else request.private_key
        if not secret:
            raise ValueError("A password or private_key is required to register a node")

        # Never persist an allocation larger than what the node actually has.
        allocated_cpu = request.allocated_cpu
        if request.detected_cpu:
            allocated_cpu = min(allocated_cpu, request.detected_cpu)
        allocated_memory_gb = request.allocated_memory_gb
        if request.detected_memory_gb:
            allocated_memory_gb = min(allocated_memory_gb, request.detected_memory_gb)

        node = SSHNode(
            owner_id=owner_id,
            name=request.name,
            host=request.host,
            port=request.port,
            username=request.username,
            auth_type=request.auth_type,
            secret_encrypted=encrypt_secret(secret),
            host_key_type=request.host_key_type,
            host_key_b64=request.host_key_b64,
            detected_cpu=request.detected_cpu,
            detected_memory_gb=request.detected_memory_gb,
            detected_gpu=request.detected_gpu,
            os_info=request.os_info,
            allocated_cpu=max(1, allocated_cpu),
            allocated_memory_gb=max(0.0, allocated_memory_gb),
            status="online",
        )
        self._db.add(node)
        await self._db.commit()
        await self._db.refresh(node)
        logger.info("ssh_node_registered", node_id=str(node.id), host=node.host)
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
    )
