"""Optional storage for a refresh token; access tokens stay in memory."""

from __future__ import annotations

import asyncio
from typing import Protocol


class TokenStore(Protocol):
    async def load_refresh_token(self) -> str | None: ...

    async def save_refresh_token(self, token: str) -> None: ...

    async def clear_refresh_token(self) -> None: ...


class InMemoryTokenStore:
    def __init__(self, refresh_token: str | None = None) -> None:
        self._refresh_token = refresh_token

    async def load_refresh_token(self) -> str | None:
        return self._refresh_token

    async def save_refresh_token(self, token: str) -> None:
        self._refresh_token = token

    async def clear_refresh_token(self) -> None:
        self._refresh_token = None


class KeyringTokenStore:
    """Store only the refresh token in the operating system's credential store."""

    _SECURE_BACKENDS = {
        ("keyring.backends.macOS", "Keyring"),
        ("keyring.backends.Windows", "WinVaultKeyring"),
        ("keyring.backends.SecretService", "Keyring"),
        ("keyring.backends.kwallet", "DBusKeyring"),
        ("keyring.backends.kwallet", "DBusKeyringKWallet4"),
    }

    def __init__(self, service: str, username: str) -> None:
        if not service or not username:
            raise ValueError("service and username are required")
        try:
            import keyring
        except ImportError:
            raise RuntimeError("Install the 'keyring' extra to use KeyringTokenStore") from None
        try:
            backend = keyring.get_keyring()
        except Exception:
            raise RuntimeError("A secure operating-system keyring backend is required") from None
        backend_id = (type(backend).__module__, type(backend).__name__)
        if backend_id not in self._SECURE_BACKENDS:
            raise RuntimeError("A secure operating-system keyring backend is required")
        self._backend = backend
        self._service = service
        self._username = username

    async def load_refresh_token(self) -> str | None:
        return await asyncio.to_thread(
            self._backend.get_password, self._service, self._username
        )

    async def save_refresh_token(self, token: str) -> None:
        await asyncio.to_thread(
            self._backend.set_password, self._service, self._username, token
        )

    async def clear_refresh_token(self) -> None:
        await asyncio.to_thread(
            self._backend.delete_password, self._service, self._username
        )
