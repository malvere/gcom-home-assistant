"""Async transport for the API paths observed in the Android application."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any, Mapping

import aiohttp
from yarl import URL

from .errors import (
    AuthenticationError,
    GcomError,
    HttpError,
    MutationNotAllowed,
    MutationOutcomeUnknown,
    ProtocolError,
    ScopeError,
)
from .models import AccessCode, Device, Entrance, EntranceGroup, Facility, Tokens
from .tokens import InMemoryTokenStore, TokenStore


def _public_url(value: str, *, name: str) -> URL:
    try:
        url = URL(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} is not a valid URL") from None
    if not url.is_absolute() or not url.host or url.user or url.password or url.query or url.fragment:
        raise ValueError(f"{name} must be an absolute URL without credentials or query")
    if url.scheme != "https" and not (
        url.scheme == "http" and url.host in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError(f"{name} must use HTTPS (HTTP is allowed for loopback tests)")
    return url


def _origin(url: URL) -> tuple[str, str | None, int | None]:
    return url.scheme, url.host, url.port


def _id(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ProtocolError("Gcom response has an invalid ID")
    if isinstance(value, int):
        if not 0 < value <= 2**63 - 1:
            raise ProtocolError("Gcom response has an invalid ID")
        return str(value)
    raw = str(value)
    if re.fullmatch(r"[0-9]{1,19}", raw) is None:
        raise ProtocolError("Gcom response has an invalid ID")
    number = int(raw)
    if not 0 < number <= 2**63 - 1:
        raise ProtocolError("Gcom response has an invalid ID")
    return str(number)


def _text(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _optional_text(value: Any) -> str | None:
    return str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else None


def _objects(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("data", "results", "items"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ProtocolError("Gcom response has an unexpected list shape")
    return payload


class GcomClient:
    """Access an account's devices, entrances, facilities, and access codes.

    ``allow_mutations`` is a deliberate, explicit opt-in. Mutations also require
    that the target ID appears in a fresh list for this authenticated user.
    """

    def __init__(
        self,
        *,
        base_url: str,
        token_url: str,
        client_id: str,
        client_secret: str,
        session: aiohttp.ClientSession | None = None,
        token_store: TokenStore | None = None,
        tokens: Tokens | None = None,
        allow_mutations: bool = False,
        timeout: float = 10.0,
        max_read_attempts: int = 3,
        retry_delay: float = 0.2,
    ) -> None:
        self._base_url = _public_url(base_url, name="base_url")
        self._token_url = _public_url(token_url, name="token_url")
        if not self._base_url.path.endswith("/"):
            self._base_url = self._base_url.with_path(self._base_url.path + "/")
        if not client_id or not client_secret:
            raise ValueError("client_id and client_secret are required")
        if timeout <= 0 or max_read_attempts < 1 or max_read_attempts > 3 or retry_delay < 0:
            raise ValueError("invalid timeout or retry configuration")
        self._client_id = client_id
        self._client_secret = client_secret
        self._session = session
        self._owns_session = session is None
        self._store = token_store or InMemoryTokenStore()
        self._tokens = tokens
        self._allow_mutations = allow_mutations
        self._timeout = timeout
        self._max_read_attempts = max_read_attempts
        self._retry_delay = retry_delay
        self._refresh_lock = asyncio.Lock()

    async def __aenter__(self) -> GcomClient:
        await self._get_session()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._timeout))
        return self._session

    async def authenticate(
        self,
        *,
        username: str | None = None,
        password: str | None = None,
        tokens: Tokens | None = None,
    ) -> Tokens:
        """Use supplied session tokens, password grant, or a stored refresh token.

        Username and password are accepted only in memory and are never stored.
        """
        if tokens is not None:
            self._tokens = tokens
            if tokens.refresh_token:
                await self._save_refresh_token(tokens.refresh_token)
            return tokens
        if (username is None) != (password is None):
            raise ValueError("username and password must be supplied together")
        if username is not None:
            return await self._token_request(
                {"grant_type": "password", "username": username, "password": password,
                 "scope": "openid"},
                previous_refresh=None,
            )
        refresh = self._tokens.refresh_token if self._tokens else None
        if refresh is None:
            try:
                refresh = await self._store.load_refresh_token()
            except Exception:
                raise AuthenticationError("Refresh token storage failed") from None
        if refresh is None:
            raise AuthenticationError("No refresh token is available")
        return await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh},
            previous_refresh=refresh,
        )

    async def _token_request(
        self, fields: dict[str, str], *, previous_refresh: str | None
    ) -> Tokens:
        session = await self._get_session()
        form = {"client_id": self._client_id, "client_secret": self._client_secret, **fields}
        try:
            async with session.post(
                self._token_url, data=form, allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as response:
                if response.status != 200:
                    raise AuthenticationError("Gcom token request failed")
                payload = await response.json(content_type=None)
        except AuthenticationError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, UnicodeError):
            raise AuthenticationError("Gcom token request failed") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
            raise AuthenticationError("Gcom token response is invalid")
        refresh = payload.get("refresh_token", previous_refresh)
        if refresh is not None and not isinstance(refresh, str):
            raise AuthenticationError("Gcom token response is invalid")
        token_type = payload.get("token_type", "Bearer")
        if not isinstance(token_type, str) or token_type.lower() != "bearer":
            raise AuthenticationError("Gcom token type is unsupported")
        expires_in = payload.get("expires_in")
        try:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
                if expires_in is not None else None
            )
        except (TypeError, ValueError, OverflowError):
            raise AuthenticationError("Gcom token lifetime is invalid") from None
        result = Tokens(payload["access_token"], refresh, "Bearer", expires_at)
        if refresh:
            await self._save_refresh_token(refresh)
        self._tokens = result
        return result

    async def _save_refresh_token(self, token: str) -> None:
        try:
            await self._store.save_refresh_token(token)
        except Exception:
            raise AuthenticationError("Refresh token storage failed") from None

    async def _access_token(self) -> str:
        if self._tokens is None or self._tokens.is_expired:
            async with self._refresh_lock:
                if self._tokens is None or self._tokens.is_expired:
                    await self.authenticate()
        assert self._tokens is not None
        return self._tokens.access_token

    async def _refresh_after_401(self, stale_token: str) -> None:
        async with self._refresh_lock:
            if self._tokens is not None and self._tokens.access_token != stale_token:
                return
            await self.authenticate()

    async def _send(
        self,
        method: str,
        path: str,
        *,
        token: str,
        params: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        mutation: bool,
        ack_only: bool = False,
    ) -> tuple[int, Any]:
        url = self._base_url.join(URL(path))
        if params:
            url = url.update_query(params)
        original_url = url
        # A fresh connector prevents aiohttp's retry of a reused connection for
        # the mutating GET. Do not alter a caller-owned Home Assistant session.
        if mutation:
            connector = aiohttp.TCPConnector(force_close=True)
            session = aiohttp.ClientSession(
                connector=connector, timeout=aiohttp.ClientTimeout(total=self._timeout)
            )
            if hasattr(session, "_retry_connection"):
                session._retry_connection = False  # aiohttp may retry a reused GET connection
        else:
            session = await self._get_session()
        try:
            redirects = 0
            while True:
                async with session.request(
                    method, url, params=None, json=body,
                    headers={"Authorization": f"Bearer {token}"},
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        if mutation:
                            raise ProtocolError("Gcom mutation returned a redirect")
                        location = response.headers.get("Location")
                        if not location or redirects >= 3:
                            raise ProtocolError("Gcom read redirect is invalid")
                        try:
                            next_url = url.join(URL(location))
                        except (TypeError, ValueError):
                            raise ProtocolError("Gcom read redirect is invalid") from None
                        if _origin(next_url) != _origin(self._base_url):
                            raise ProtocolError("Gcom read redirect left the API origin")
                        if (
                            next_url.user is not None or next_url.password is not None
                            or next_url.fragment
                            or next_url.path.rstrip("/") != original_url.path.rstrip("/")
                            or next_url.query_string != original_url.query_string
                        ):
                            raise ProtocolError("Gcom read redirect changed the endpoint")
                        url = next_url
                        redirects += 1
                        continue
                    if response.status < 200 or response.status >= 300:
                        return response.status, None
                    if response.status == 204:
                        if mutation and not ack_only:
                            raise MutationOutcomeUnknown("Gcom mutation response was incomplete")
                        return response.status, None
                    try:
                        raw = await response.read()
                        return response.status, json.loads(raw)
                    except (ValueError, UnicodeError):
                        if mutation:
                            if ack_only:
                                return response.status, None
                            raise MutationOutcomeUnknown("Gcom mutation response was incomplete") from None
                        raise ProtocolError("Gcom response is not valid JSON") from None
        finally:
            if mutation:
                await session.close()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        mutation: bool = False,
        ack_only: bool = False,
    ) -> Any:
        token = await self._access_token()
        attempts = 1 if mutation else self._max_read_attempts
        refreshed = False
        for attempt in range(attempts):
            try:
                status, payload = await self._send(
                    method, path, token=token, params=params, body=body,
                    mutation=mutation, ack_only=ack_only,
                )
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                if mutation:
                    raise MutationOutcomeUnknown("Gcom mutation outcome is unknown") from None
                if attempt + 1 >= attempts:
                    raise GcomError("Gcom read request failed") from None
                await asyncio.sleep(self._retry_delay * (attempt + 1))
                continue
            if status == 401:
                if mutation:
                    raise AuthenticationError("Gcom mutation was not authorized")
                if refreshed or attempt + 1 >= attempts:
                    raise AuthenticationError("Gcom read was not authorized")
                await self._refresh_after_401(token)
                token = await self._access_token()
                refreshed = True
                continue
            if status in (429, 502, 503, 504) and not mutation and attempt + 1 < attempts:
                await asyncio.sleep(self._retry_delay * (attempt + 1))
                continue
            if not 200 <= status < 300:
                raise HttpError(status)
            if isinstance(payload, dict) and payload.get("success") is False:
                raise ProtocolError("Gcom API reported an unsuccessful operation")
            return payload
        raise GcomError("Gcom read request failed")

    async def get_devices(self) -> list[Device]:
        payload = await self._request("GET", "user/devices/")
        result: list[Device] = []
        for item in _objects(payload):
            raw_entrances = item.get("entrances_id", [])
            if raw_entrances is None:
                raw_entrances = []
            if not isinstance(raw_entrances, list):
                raise ProtocolError("Gcom device entrances have an invalid shape")
            result.append(Device(
                id=_id(item.get("id")), name=_text(item.get("name")),
                type=_optional_text(item.get("type")),
                description=_optional_text(item.get("description")),
                entrance_ids=tuple(_id(value) for value in raw_entrances),
            ))
        return result

    async def get_entrances(self) -> list[EntranceGroup]:
        payload = await self._request("GET", "user/entrances/")
        result: list[EntranceGroup] = []
        for item in _objects(payload):
            group = item.get("group")
            if not isinstance(group, dict):
                raise ProtocolError("Gcom entrance group has an invalid shape")
            entrances = item.get("entrances", [])
            if not isinstance(entrances, list) or not all(isinstance(e, dict) for e in entrances):
                raise ProtocolError("Gcom entrances have an invalid shape")
            result.append(EntranceGroup(
                id=_id(group.get("id")), name=_text(group.get("name")),
                entrances=tuple(
                    Entrance(id=_id(e.get("id")), name=_text(e.get("name")))
                    for e in entrances
                ),
            ))
        return result

    async def get_facilities(self) -> list[Facility]:
        payload = await self._request("GET", "user-facilities")
        return [Facility(
            id=_id(item.get("id")), name=_text(item.get("name")),
            building_id=_optional_text(item.get("building_id", item.get("buildingId"))),
            floor=_optional_text(item.get("floor")),
            extension=_optional_text(item.get("extension")),
        ) for item in _objects(payload)]

    async def get_access_codes(self, facility_id: str | int) -> list[AccessCode]:
        facility = _id(facility_id)
        if facility not in {item.id for item in await self.get_facilities()}:
            raise ScopeError("Facility is absent from the current user's list")
        payload = await self._request(
            "GET", "user-facilities/codes", params={"facility_id": facility}
        )
        return [self._access_code(item, facility) for item in _objects(payload)]

    @staticmethod
    def _access_code(item: Mapping[str, Any], facility_id: str) -> AccessCode:
        code = item.get("code")
        if not isinstance(code, str):
            raise ProtocolError("Gcom access code has an invalid shape")
        response_facility = _id(item["facility_id"]) if item.get("facility_id") is not None else facility_id
        if response_facility != facility_id:
            raise ProtocolError("Gcom access code belongs to another facility")
        return AccessCode(
            id=_id(item.get("id")), code=code,
            expired=_optional_text(item.get("expired")),
            facility_id=response_facility,
        )

    def _authorize_mutation(self, confirmed: bool) -> None:
        if not (self._allow_mutations or confirmed):
            raise MutationNotAllowed("Gcom mutation requires explicit opt-in")

    async def open_door(self, device_id: str | int, *, confirmed: bool = False) -> bool:
        """Return True only when the API acknowledges the command.

        The response does not prove that the physical door opened.
        """
        self._authorize_mutation(confirmed)
        target = _id(device_id)
        if target not in {device.id for device in await self.get_devices()}:
            raise ScopeError("Device is absent from the current user's list")
        await self._request(
            "POST", "open-entrance", params={"device_id": target},
            mutation=True, ack_only=True,
        )
        return True

    async def open_entrance(self, entrance_id: str | int, *, confirmed: bool = False) -> bool:
        """Send the mutating GET; True means API acknowledgment, not physical proof."""
        self._authorize_mutation(confirmed)
        target = _id(entrance_id)
        groups = await self.get_entrances()
        if target not in {entrance.id for group in groups for entrance in group.entrances}:
            raise ScopeError("Entrance is absent from the current user's list")
        await self._request(
            "GET", "open-entrance", params={"entrance_ids": target},
            mutation=True, ack_only=True,
        )
        return True

    async def create_access_code(
        self, facility_id: str | int, expires_at: datetime, *, confirmed: bool = False
    ) -> AccessCode:
        self._authorize_mutation(confirmed)
        target = _id(facility_id)
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("expires_at must be timezone aware")
        if target not in {facility.id for facility in await self.get_facilities()}:
            raise ScopeError("Facility is absent from the current user's list")
        utc_expiry = expires_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = await self._request(
            "POST", "user-facilities/codes",
            body={"facility_id": int(target), "expired": utc_expiry}, mutation=True,
        )
        if isinstance(payload, dict) and "code" in payload:
            try:
                return self._access_code(payload, target)
            except ProtocolError:
                raise MutationOutcomeUnknown("Gcom mutation response was incomplete") from None
        raise MutationOutcomeUnknown("Gcom mutation response was incomplete")
