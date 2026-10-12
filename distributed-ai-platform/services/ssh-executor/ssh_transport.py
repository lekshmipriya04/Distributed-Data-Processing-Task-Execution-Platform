"""SSHTransport — the SSH/SFTP-only transport layer (Phase 1).

This is a thin wrapper over paramiko that knows *only* how to talk SSH: open a
connection (verifying/pinning the host key), run commands, move files, and
clean up. It deliberately knows nothing about Linux/macOS/Windows, Spark or Ray
— the OS adapters build command strings and :class:`task_executor.TaskExecutor`
orchestrates; this class just carries bytes.

It is additive: the existing ``ssh_client.SSHExecutor`` continues to power the
current batch/training path unchanged. New code (TaskExecutor, later the
RuntimeManager) builds on SSHTransport.

Host-key handling mirrors ``ssh_client.py`` exactly: trust-on-first-use — accept
and capture on first contact, pin and reject mismatches afterwards (paramiko
verifies during the handshake, before any credential is sent).
"""
from __future__ import annotations

import base64
import hashlib
import io
from typing import Any, Callable, Dict, Optional

import paramiko
import structlog

logger = structlog.get_logger(__name__)

# Private key classes tried in order when loading an operator-supplied key.
_KEY_CLASSES = (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey)


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


class SSHTransport:
    """One SSH connection to one node. Open with :meth:`connect` (or use as a
    context manager); it holds the paramiko client until :meth:`close`."""

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
        self._client: Optional[paramiko.SSHClient] = None

    # -- lifecycle --------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        return self._client is not None

    def _load_pkey(self) -> paramiko.PKey:
        last_err: Optional[Exception] = None
        for key_cls in _KEY_CLASSES:
            try:
                return key_cls.from_private_key(io.StringIO(self.private_key))
            except paramiko.SSHException as exc:  # wrong type / passphrase
                last_err = exc
        raise ValueError(f"Unsupported or invalid private key: {last_err}")

    def connect(self) -> Dict[str, str]:
        """Open the connection; return the captured host-key info so a
        first-contact caller can pin it."""
        client = paramiko.SSHClient()
        if self.host_key_b64 and self.host_key_type:
            pinned = paramiko.PKey.from_type_string(
                self.host_key_type, base64.b64decode(self.host_key_b64)
            )
            client.get_host_keys().add(
                _host_key_name(self.host, self.port), self.host_key_type, pinned
            )
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
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
        self._client = client
        return self.capture_host_key()

    def close(self) -> None:
        if self._client:
            try:
                self._client.close()
            finally:
                self._client = None

    def __enter__(self) -> "SSHTransport":
        if not self.is_connected:
            self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _require_client(self) -> paramiko.SSHClient:
        if self._client is None:
            raise RuntimeError("SSHTransport is not connected; call connect() first")
        return self._client

    # -- host key ---------------------------------------------------------
    def capture_host_key(self) -> Dict[str, str]:
        remote = self._require_client().get_transport().get_remote_server_key()
        key_type = remote.get_name()
        key_b64 = remote.get_base64()
        return {
            "host_key_type": key_type,
            "host_key_b64": key_b64,
            "fingerprint": fingerprint(key_type, key_b64),
        }

    # -- command execution ------------------------------------------------
    def exec(
        self,
        command: str,
        input_data: str = "",
        timeout: Optional[int] = None,
        max_output_bytes: int = 1_000_000,
    ) -> Dict[str, Any]:
        """Run ``command``; optionally stream ``input_data`` to its stdin.

        Returns ``{"exit_code", "stdout", "stderr"}``.
        """
        client = self._require_client()
        chan_timeout = timeout if timeout is not None else self.connect_timeout
        stdin, stdout, stderr = client.exec_command(command, timeout=chan_timeout)
        if input_data:
            try:
                stdin.write(input_data)
            except OSError:
                pass
        stdin.channel.shutdown_write()
        out = stdout.read(max_output_bytes).decode(errors="replace")
        err = stderr.read(max_output_bytes).decode(errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        return {"exit_code": exit_code, "stdout": out, "stderr": err}

    def exec_stream(
        self,
        command: str,
        input_data: str = "",
        timeout: Optional[int] = None,
        on_line: Optional[Callable[[str], None]] = None,
        max_output_bytes: int = 1_000_000,
    ) -> Dict[str, Any]:
        """Like :meth:`exec`, but invoke ``on_line`` per stdout line as it
        arrives (used later for live log streaming)."""
        client = self._require_client()
        chan_timeout = timeout if timeout is not None else self.connect_timeout
        stdin, stdout, stderr = client.exec_command(command, timeout=chan_timeout)
        if input_data:
            try:
                stdin.write(input_data)
            except OSError:
                pass
        stdin.channel.shutdown_write()
        collected: list[str] = []
        total = 0
        for raw in iter(stdout.readline, ""):
            total += len(raw)
            if total > max_output_bytes:
                break
            collected.append(raw)
            if on_line:
                on_line(raw.rstrip("\n"))
        err = stderr.read(max_output_bytes).decode(errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        return {"exit_code": exit_code, "stdout": "".join(collected), "stderr": err}

    # -- file transfer ----------------------------------------------------
    def put_text(self, remote_path: str, content: str) -> None:
        sftp = self._require_client().open_sftp()
        try:
            with sftp.file(remote_path, "w") as fh:
                fh.write(content)
        finally:
            sftp.close()

    def upload(self, local_path: str, remote_path: str) -> None:
        sftp = self._require_client().open_sftp()
        try:
            sftp.put(local_path, remote_path)
        finally:
            sftp.close()

    def download(self, remote_path: str) -> bytes:
        sftp = self._require_client().open_sftp()
        try:
            with sftp.file(remote_path, "r") as fh:
                return fh.read()
        finally:
            sftp.close()

    def remove(self, remote_path: str) -> None:
        """Remove a single remote file over SFTP; never raises."""
        try:
            sftp = self._require_client().open_sftp()
            try:
                sftp.remove(remote_path)
            finally:
                sftp.close()
        except Exception:
            pass

    # -- probe ------------------------------------------------------------
    def test_connection(self) -> Dict[str, Any]:
        own = not self.is_connected
        try:
            if own:
                self.connect()
            res = self.exec("echo connection_successful", timeout=self.connect_timeout)
            if res["exit_code"] != 0:
                return {"status": "error", "message": "Command execution failed"}
            result = {"status": "success", "message": "Successfully connected via SSH"}
            result.update(self.capture_host_key())
            return result
        except Exception as exc:
            logger.error("ssh_transport_test_failed", host=self.host, error=str(exc))
            return {"status": "error", "message": str(exc)}
        finally:
            if own:
                self.close()
