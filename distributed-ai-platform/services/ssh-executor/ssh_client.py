"""Master-initiated SSH client for borrowed provider nodes.

The master connects to a provider that runs only ``sshd`` — there is no agent on
the far side. This module:

* verifies the host key on every connection (TOFU pin captured on first contact,
  rejected on later mismatch) instead of blindly trusting it;
* supports RSA, Ed25519 and ECDSA private keys as well as passwords;
* auto-detects the node's CPU/memory/GPU/OS so the operator can choose an
  allocation to borrow;
* runs each task as an *ephemeral* script (unique temp path, wiped in ``finally``)
  that is niced and pinned to the allocated cores so the provider is never
  disturbed, with the task's input streamed in over stdin — nothing the master
  sends is persisted on the provider.
"""
from __future__ import annotations

import base64
import hashlib
import io
import shlex
import uuid
from typing import Any, Dict, Optional

import paramiko
import structlog

logger = structlog.get_logger(__name__)

# Private key classes tried in order when loading an operator-supplied key.
_KEY_CLASSES = (
    paramiko.Ed25519Key,
    paramiko.ECDSAKey,
    paramiko.RSAKey,
)


def _host_key_name(host: str, port: int) -> str:
    """Reproduce paramiko's internal known-hosts key name for a host:port."""
    return host if port == 22 else f"[{host}]:{port}"


def fingerprint(host_key_type: str, host_key_b64: str) -> str:
    """Return an OpenSSH-style ``SHA256:...`` fingerprint for a pinned key."""
    raw = base64.b64decode(host_key_b64)
    digest = hashlib.sha256(raw).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


class HostKeyMismatch(Exception):
    """Raised when a node presents a host key other than the pinned one."""


class SSHExecutor:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: Optional[str] = None,
        private_key: Optional[str] = None,
        host_key_type: Optional[str] = None,
        host_key_b64: Optional[str] = None,
        connect_timeout: int = 10,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.private_key = private_key
        self.host_key_type = host_key_type
        self.host_key_b64 = host_key_b64
        self.connect_timeout = connect_timeout

    # -- connection -------------------------------------------------------
    def _load_pkey(self) -> paramiko.PKey:
        last_err: Optional[Exception] = None
        for key_cls in _KEY_CLASSES:
            try:
                return key_cls.from_private_key(io.StringIO(self.private_key))
            except paramiko.SSHException as exc:  # wrong type / passphrase
                last_err = exc
        raise ValueError(f"Unsupported or invalid private key: {last_err}")

    def _get_client(self) -> paramiko.SSHClient:
        client = paramiko.SSHClient()

        if self.host_key_b64 and self.host_key_type:
            # Pinned: load the trusted key and reject anything else. paramiko
            # verifies during the handshake, before credentials are sent.
            pinned = paramiko.PKey.from_type_string(
                self.host_key_type, base64.b64decode(self.host_key_b64)
            )
            client.get_host_keys().add(
                _host_key_name(self.host, self.port), self.host_key_type, pinned
            )
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            # First contact (TOFU): accept, then the caller pins what we saw.
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: Dict[str, Any] = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
            "timeout": self.connect_timeout,
            "banner_timeout": self.connect_timeout,
            "auth_timeout": self.connect_timeout,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if self.private_key:
            connect_kwargs["pkey"] = self._load_pkey()
        elif self.password:
            connect_kwargs["password"] = self.password
        else:
            raise ValueError("Either password or private_key must be provided")

        try:
            client.connect(**connect_kwargs)
        except paramiko.SSHException as exc:
            if "not found in known_hosts" in str(exc) or "Host key" in str(exc):
                raise HostKeyMismatch(
                    "Host key does not match the pinned key for this node"
                ) from exc
            raise
        return client

    def _capture_host_key(self, client: paramiko.SSHClient) -> Dict[str, str]:
        remote = client.get_transport().get_remote_server_key()
        key_type = remote.get_name()
        key_b64 = remote.get_base64()
        return {
            "host_key_type": key_type,
            "host_key_b64": key_b64,
            "fingerprint": fingerprint(key_type, key_b64),
        }

    # -- probes -----------------------------------------------------------
    def test_connection(self) -> Dict[str, Any]:
        client = None
        try:
            client = self._get_client()
            _, stdout, _ = client.exec_command("echo connection_successful", timeout=self.connect_timeout)
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                return {"status": "error", "message": "Command execution failed"}
            result = {"status": "success", "message": "Successfully connected via SSH"}
            result.update(self._capture_host_key(client))
            return result
        except Exception as exc:
            logger.error("ssh_test_failed", host=self.host, error=str(exc))
            return {"status": "error", "message": str(exc)}
        finally:
            if client:
                client.close()

    def detect_resources(self) -> Dict[str, Any]:
        """Auto-detect the node's shareable hardware over SSH.

        Mirrors the field shape of the worker agent's resource detector so the
        operator sees CPU cores, total memory (GB), GPU count and OS string.
        """
        client = None
        try:
            client = self._get_client()

            def run(cmd: str) -> str:
                _, out, _ = client.exec_command(cmd, timeout=self.connect_timeout)
                return out.read().decode(errors="replace").strip()

            cpu_raw = run("nproc --all 2>/dev/null || grep -c ^processor /proc/cpuinfo")
            mem_kb_raw = run("awk '/^MemTotal:/{print $2}' /proc/meminfo")
            gpu_raw = run("nvidia-smi -L 2>/dev/null | wc -l")
            os_info = run("(. /etc/os-release 2>/dev/null && echo \"$PRETTY_NAME\") || uname -sr")

            cpu = int(cpu_raw or 0)
            memory_gb = round(int(mem_kb_raw or 0) / (1024 * 1024), 2)
            gpu = int(gpu_raw or 0)

            result = {
                "status": "success",
                "detected_cpu": cpu,
                "detected_memory_gb": memory_gb,
                "detected_gpu": gpu,
                "os_info": os_info or None,
            }
            result.update(self._capture_host_key(client))
            return result
        except Exception as exc:
            logger.error("ssh_detect_failed", host=self.host, error=str(exc))
            return {"status": "error", "message": str(exc)}
        finally:
            if client:
                client.close()

    # -- execution --------------------------------------------------------
    def run_task(
        self,
        code: str,
        input_data: str = "",
        allocated_cpu: int = 1,
        nice: int = 19,
        timeout_seconds: int = 120,
        max_output_bytes: int = 1_000_000,
    ) -> Dict[str, Any]:
        """Run ``code`` on the node ephemerally, confined to allocated cores.

        The script is written to a unique ``/tmp`` path, executed under
        ``nice``/``taskset``/``timeout`` with ``input_data`` streamed over
        stdin, and removed in ``finally`` whatever happens — no master data is
        left on the provider.
        """
        client = None
        remote_path = f"/tmp/task_{uuid.uuid4().hex}.py"
        try:
            client = self._get_client()
            sftp = client.open_sftp()
            try:
                with sftp.file(remote_path, "w") as fh:
                    fh.write(code)
            finally:
                sftp.close()

            cores = ",".join(str(i) for i in range(max(1, allocated_cpu)))
            quoted = shlex.quote(remote_path)
            confine = f"nice -n {nice} timeout {timeout_seconds}s python3 {quoted}"
            # Pin to the borrowed cores when taskset exists; fall back to just
            # nice/timeout otherwise. An if/else runs exactly one branch, so a
            # task that legitimately exits non-zero is never re-run unpinned.
            cmd = (
                f"if command -v taskset >/dev/null 2>&1; then "
                f"taskset -c {cores} {confine}; else {confine}; fi"
            )

            stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout_seconds + 15)
            if input_data:
                stdin.write(input_data)
            stdin.channel.shutdown_write()

            out = stdout.read(max_output_bytes).decode(errors="replace")
            err = stderr.read(max_output_bytes).decode(errors="replace")
            exit_status = stdout.channel.recv_exit_status()

            status = "success" if exit_status == 0 else "error"
            if exit_status == 124:  # timeout(1) exit code
                err = (err + "\n[task timed out]").strip()
            return {
                "status": status,
                "exit_code": exit_status,
                "stdout": out,
                "stderr": err,
            }
        except Exception as exc:
            logger.error("ssh_task_failed", host=self.host, error=str(exc))
            return {"status": "error", "exit_code": None, "stdout": "", "stderr": str(exc)}
        finally:
            if client:
                try:
                    cleanup = client.open_sftp()
                    try:
                        cleanup.remove(remote_path)
                    finally:
                        cleanup.close()
                except Exception:
                    pass
                client.close()
