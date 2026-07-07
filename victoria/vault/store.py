"""Encrypted credentials vault.

Design rule: secret VALUES only ever travel one way — in via set(), and out
only at the transport edge (MCP env/headers) via resolve(). They are never
returned to the model, never logged, and there is no API that hands a plaintext
value back to a caller for display. From Victoria's side the vault is
effectively write-only: you can add, list names, delete, and *use* secrets, but
not read them.

At rest, secrets are stored as a single Fernet-encrypted JSON blob. The master
key is sourced (in order) from:
  1. the VICTORIA_VAULT_KEY environment variable (handy for tests/CI),
  2. the macOS Keychain (default on this Mac),
  3. a 0600 key file under ~/.victoria/ (portable fallback),
generating and persisting a new key if none exists yet.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet

from victoria.config import settings

logger = logging.getLogger(__name__)

# Reference form used in mcp.json etc.: ${vault:NAME}
VAULT_REF_RE = re.compile(r"\$\{vault:([A-Za-z0-9_.-]+)\}", re.IGNORECASE)


def _keychain_get(service: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-a", os.getenv("USER", "victoria"),
             "-s", service, "-w"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None
    except Exception:
        return None


def _keychain_set(service: str, value: str) -> bool:
    try:
        out = subprocess.run(
            ["security", "add-generic-password", "-U", "-a", os.getenv("USER", "victoria"),
             "-s", service, "-w", value],
            capture_output=True, text=True, timeout=10,
        )
        return out.returncode == 0
    except Exception:
        return False


def _load_or_create_key() -> bytes:
    # 1) explicit env override
    env_key = os.getenv("VICTORIA_VAULT_KEY")
    if env_key:
        return env_key.encode()

    service = settings.vault_keychain_service
    # 2) macOS Keychain
    existing = _keychain_get(service)
    if existing:
        return existing.encode()

    # 3) key file fallback
    key_file = Path.home() / ".victoria" / "vault.key"
    if key_file.exists():
        return key_file.read_bytes().strip()

    # Generate a fresh key and persist it to the best available backend.
    key = Fernet.generate_key()
    if _keychain_set(service, key.decode()):
        logger.info("Vault master key created in the macOS Keychain (service=%s)", service)
    else:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(key)
        os.chmod(key_file, 0o600)
        logger.info("Vault master key created at %s (0600)", key_file)
    return key


class SecretsVault:
    def __init__(self, path: Optional[str] = None, key: Optional[bytes] = None):
        self.path = Path(path or settings.vault_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(key or _load_or_create_key())

    # -- storage ---------------------------------------------------------- #
    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self._fernet.decrypt(self.path.read_bytes()).decode())
        except Exception:
            logger.exception("Vault decrypt failed — returning empty (wrong key?)")
            return {}

    def _write(self, data: dict) -> None:
        blob = self._fernet.encrypt(json.dumps(data).encode())
        self.path.write_bytes(blob)
        os.chmod(self.path, 0o600)

    # -- write-side API (safe to expose) ---------------------------------- #
    def set(self, name: str, value: str) -> None:
        name = name.strip()
        if not name or not value:
            raise ValueError("name and value are required")
        data = self._read()
        data[name] = value
        self._write(data)
        logger.info("Vault: stored secret %r (value not logged)", name)

    def delete(self, name: str) -> bool:
        data = self._read()
        if name in data:
            del data[name]
            self._write(data)
            logger.info("Vault: deleted secret %r", name)
            return True
        return False

    def names(self) -> list[str]:
        """List secret NAMES only — never values."""
        return sorted(self._read().keys())

    def exists(self, name: str) -> bool:
        return name in self._read()

    # -- resolution (transport edge only) --------------------------------- #
    def _get(self, name: str) -> Optional[str]:
        """Internal: fetch a plaintext value. Callers must never expose it."""
        return self._read().get(name)

    def resolve(self, value):
        """Recursively replace ${vault:NAME} references in strings / dicts / lists.

        Used only when handing config to a transport (MCP env/headers). The
        result must never be logged or returned to the model.
        """
        if isinstance(value, str):
            data = self._read()
            return VAULT_REF_RE.sub(lambda m: data.get(m.group(1), m.group(0)), value)
        if isinstance(value, dict):
            return {k: self.resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve(v) for v in value]
        return value


# Module-level singleton. Access via get_vault() so it stays patchable/lazy.
vault = SecretsVault()


def get_vault() -> "SecretsVault":
    return vault
