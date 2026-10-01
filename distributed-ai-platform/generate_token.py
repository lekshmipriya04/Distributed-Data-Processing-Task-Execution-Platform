#!/usr/bin/env python3
"""Mint a JWT for local development / testing.

The platform has no /login endpoint (see PRD §9.11); tokens must be minted
out-of-band with the same JWT_SECRET the services validate against. The frontend
Settings page expects a token produced by this script.

The signing secret is resolved in this order:
    1. --secret CLI argument
    2. JWT_SECRET environment variable
    3. JWT_SECRET in ./config/.env
    4. the framework default ("change_me_in_production")

Usage:
    python generate_token.py                       # 60-min token for "dev-user"
    python generate_token.py --sub alice --hours 8 # custom subject / lifetime
    python generate_token.py --secret my-secret    # override the secret

Requires PyJWT (pip install pyjwt).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

try:
    import jwt  # PyJWT
except ImportError:
    sys.exit("PyJWT is not installed. Run: pip install pyjwt")

DEFAULT_SECRET = "change_me_in_production"
ALGORITHM = "HS256"
ENV_FILE = Path(__file__).resolve().parent / "config" / ".env"


def _secret_from_env_file() -> str | None:
    """Read JWT_SECRET from config/.env without needing python-dotenv."""
    if not ENV_FILE.is_file():
        return None
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "JWT_SECRET":
            return value.strip().strip('"').strip("'")
    return None


def resolve_secret(cli_secret: str | None) -> str:
    import os

    if cli_secret:
        return cli_secret
    if os.environ.get("JWT_SECRET"):
        return os.environ["JWT_SECRET"]
    from_file = _secret_from_env_file()
    if from_file:
        return from_file
    return DEFAULT_SECRET


def main() -> None:
    parser = argparse.ArgumentParser(description="Mint a dev JWT for the platform.")
    parser.add_argument("--sub", default="dev-user", help="token subject (default: dev-user)")
    parser.add_argument("--hours", type=float, default=1.0, help="lifetime in hours (default: 1)")
    parser.add_argument("--secret", default=None, help="override the signing secret")
    args = parser.parse_args()

    secret = resolve_secret(args.secret)
    now = int(time.time())
    # decode_access_token() requires sub, exp and iat to be present.
    payload = {
        "sub": args.sub,
        "iat": now,
        "exp": now + int(args.hours * 3600),
    }
    token = jwt.encode(payload, secret, algorithm=ALGORITHM)

    print(token)
    print(
        f"\n# subject={args.sub}  expires in {args.hours}h  "
        f"secret={'(default)' if secret == DEFAULT_SECRET else '(from env/.env/CLI)'}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
