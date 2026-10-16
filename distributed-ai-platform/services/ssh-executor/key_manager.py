"""Managed SSH keys and the signed onboarding token (Phase 2).

Everything about keys happens on the master:

* :func:`generate_keypair` makes a fresh Ed25519 key with ``cryptography``
  (private half as an OpenSSH PEM, which paramiko loads directly; public half
  as an OpenSSH ``ssh-ed25519 AAAA...`` line).
* :func:`authorized_key_line` builds the exact line we install on a worker:
  ``"<managed_key_options> <public key> <prefix>:<id>"``. The comment lets us
  find and remove our line again without touching the user's own keys.
* :func:`fetch_host_key` reads a node's host key over a bare SSH handshake.
  No authentication is attempted, so no credential ever leaves the master.
* :func:`issue_onboarding_token` / :func:`verify_onboarding_token` carry the
  host key the user confirmed from the connect step to the provision step.
  The token is signed with the shared ``jwt_secret`` but has its own ``aud`` and
  ``typ`` claims, so it can never be used as an access token (and an access
  token can never be used as an onboarding token).
"""
from __future__ import annotations

import base64
import re
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import jwt
import paramiko
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from config import get_settings
from ssh_transport import fingerprint

ONBOARDING_AUDIENCE = "dp-platform:node-onboarding"
ONBOARDING_TYPE = "node_onboarding"
ONBOARDING_ISSUER = "ssh-executor"

# "<prefix>:<id>": prefix from settings, id is a UUID. Kept strict because the
# comment ends up inside remote shell / PowerShell scripts.
_COMMENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}:[A-Za-z0-9-]{1,64}$")
# Options such as `restrict` or `restrict,from="10.0.0.0/8"`. No quotes other
# than double quotes, no backslashes, no whitespace.
_OPTIONS_RE = re.compile(r'^[A-Za-z0-9,=".:/@_*+-]*$')
_PUBKEY_RE = re.compile(r"^(ssh-ed25519|ecdsa-sha2-nistp(256|384|521)|ssh-rsa) [A-Za-z0-9+/]+={0,3}$")


class InvalidOnboardingToken(ValueError):
    """The onboarding token is malformed, tampered with, expired or not ours."""


class HostKeyFetchError(Exception):
    """The node could not be reached or did not complete an SSH handshake."""


# --- key pairs -------------------------------------------------------------
@dataclass(frozen=True)
class ManagedKey:
    private_key_pem: str  # OpenSSH PEM ("-----BEGIN OPENSSH PRIVATE KEY-----")
    public_key: str  # "ssh-ed25519 AAAA..." (no comment)
    fingerprint: str  # "SHA256:..."

    @property
    def key_type(self) -> str:
        return self.public_key.split()[0]

    @property
    def key_blob(self) -> str:
        return self.public_key.split()[1]


def generate_keypair() -> ManagedKey:
    """Generate a fresh Ed25519 key pair on the master."""
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("ascii")
    return ManagedKey(private_key_pem=pem, public_key=public, fingerprint=public_key_fingerprint(public))


def public_key_fingerprint(public_key: str) -> str:
    """OpenSSH ``SHA256:...`` fingerprint of an ``<type> <base64>`` public key."""
    parts = public_key.split()
    if len(parts) < 2:
        raise ValueError("Not an OpenSSH public key")
    return fingerprint(parts[0], parts[1])


def key_comment(key_id: uuid.UUID | str) -> str:
    """Comment written on the authorized_keys line: ``<prefix>:<id>``."""
    comment = f"{get_settings().managed_key_comment_prefix}:{key_id}"
    if not _COMMENT_RE.match(comment):
        raise ValueError("managed_key_comment_prefix contains unsupported characters")
    return comment


def validate_comment(comment: str) -> str:
    if not _COMMENT_RE.match(comment or ""):
        raise ValueError(f"Invalid managed key comment: {comment!r}")
    return comment


def authorized_key_line(public_key: str, comment: str) -> str:
    """``"<options> <public key> <comment>"`` (options omitted when empty)."""
    public_key = public_key.strip()
    if not _PUBKEY_RE.match(public_key):
        raise ValueError("Unsupported public key format")
    validate_comment(comment)
    options = (get_settings().managed_key_options or "").strip()
    if not _OPTIONS_RE.match(options):
        raise ValueError("managed_key_options contains unsupported characters")
    return f"{options} {public_key} {comment}" if options else f"{public_key} {comment}"


# --- host key (no authentication) -------------------------------------------
def fetch_host_key(host: str, port: int, timeout: Optional[int] = None) -> dict:
    """Read the node's SSH host key with a bare handshake; never authenticates.

    Returns ``{"host_key_type", "host_key_b64", "fingerprint"}``.
    """
    timeout = timeout or get_settings().connect_timeout
    sock = None
    transport = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        transport = paramiko.Transport(sock)
        transport.banner_timeout = timeout
        transport.handshake_timeout = timeout
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
        key_type = key.get_name()
        key_b64 = key.get_base64()
        return {
            "host_key_type": key_type,
            "host_key_b64": key_b64,
            "fingerprint": fingerprint(key_type, key_b64),
        }
    except (OSError, paramiko.SSHException, EOFError) as exc:
        raise HostKeyFetchError(f"Could not reach an SSH server at {host}:{port}: {exc}") from exc
    finally:
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


# --- onboarding token ---------------------------------------------------------
@dataclass(frozen=True)
class OnboardingClaims:
    owner_id: str
    host: str
    port: int
    username: str
    host_key_type: str
    host_key_b64: str
    expires_at: datetime

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.host_key_type, self.host_key_b64)


def issue_onboarding_token(
    *,
    owner_id: str,
    host: str,
    port: int,
    username: str,
    host_key_type: str,
    host_key_b64: str,
) -> str:
    settings = get_settings()
    now = int(time.time())
    payload = {
        "iss": ONBOARDING_ISSUER,
        "aud": ONBOARDING_AUDIENCE,
        "typ": ONBOARDING_TYPE,
        "sub": owner_id,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + settings.onboarding_token_ttl_seconds,
        "host": host,
        "port": int(port),
        "username": username,
        "hkt": host_key_type,
        "hkb": host_key_b64,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_onboarding_token(token: str, owner_id: str) -> OnboardingClaims:
    """Validate signature, audience, type, expiry and owner; return the claims."""
    settings = get_settings()
    try:
        data = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=ONBOARDING_AUDIENCE,
            issuer=ONBOARDING_ISSUER,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidOnboardingToken("Onboarding token expired; connect to the node again") from exc
    except jwt.PyJWTError as exc:
        raise InvalidOnboardingToken("Invalid onboarding token") from exc

    if data.get("typ") != ONBOARDING_TYPE:
        raise InvalidOnboardingToken("Invalid onboarding token")
    if data.get("sub") != owner_id:
        raise InvalidOnboardingToken("Onboarding token belongs to another user")
    try:
        claims = OnboardingClaims(
            owner_id=str(data["sub"]),
            host=str(data["host"]),
            port=int(data["port"]),
            username=str(data["username"]),
            host_key_type=str(data["hkt"]),
            host_key_b64=str(data["hkb"]),
            expires_at=datetime.fromtimestamp(int(data["exp"]), tz=timezone.utc),
        )
        base64.b64decode(claims.host_key_b64, validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidOnboardingToken("Invalid onboarding token") from exc
    return claims
