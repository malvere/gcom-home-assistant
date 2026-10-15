"""Private, admin-only access-code API. Never publish codes as entity state."""

from __future__ import annotations

from datetime import datetime, timezone

import voluptuous as vol

from homeassistant.components import websocket_api

from ._vendor.gcom_client import AuthenticationError, GcomError, MutationOutcomeUnknown
from .const import CONF_CODES_ENABLED, DOMAIN

_ID = vol.Match(r"[1-9][0-9]{0,18}")


def _runtime(hass, connection, msg):
    if not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Administrator access required")
        return None
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    runtime = hass.data.get(DOMAIN, {}).get(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN or runtime is None:
        connection.send_error(msg["id"], "not_found", "10G.com entry unavailable")
        return None
    if not entry.options.get(CONF_CODES_ENABLED, False):
        connection.send_error(msg["id"], "disabled", "Access codes are disabled")
        return None
    return runtime


def _error(connection, msg, exc):
    if isinstance(exc, MutationOutcomeUnknown):
        connection.send_error(msg["id"], "outcome_unknown", "Creation result unknown; refresh the list before trying again")
    elif isinstance(exc, AuthenticationError):
        connection.send_error(msg["id"], "auth_failed", "10G.com authorization expired")
    else:
        connection.send_error(msg["id"], "request_failed", "10G.com request failed")


@websocket_api.websocket_command({vol.Required("type"): "gcom/entries"})
@websocket_api.async_response
async def ws_entries(hass, connection, msg):
    if not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Administrator access required")
        return
    connection.send_result(msg["id"], [
        {"id": entry.entry_id, "title": entry.title, "codes_enabled": bool(entry.options.get(CONF_CODES_ENABLED, False))}
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id in hass.data.get(DOMAIN, {})
    ])


@websocket_api.websocket_command({
    vol.Required("type"): "gcom/facilities",
    vol.Required("entry_id"): str,
})
@websocket_api.async_response
async def ws_facilities(hass, connection, msg):
    runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    connection.send_result(msg["id"], [
        {"id": facility.id, "name": facility.name}
        for facility in runtime.coordinator.data.facilities
    ])


@websocket_api.websocket_command({
    vol.Required("type"): "gcom/access_codes/list",
    vol.Required("entry_id"): str,
    vol.Required("facility_id"): _ID,
})
@websocket_api.async_response
async def ws_list_codes(hass, connection, msg):
    runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    try:
        codes = await runtime.client.get_access_codes(msg["facility_id"])
    except GcomError as exc:
        _error(connection, msg, exc)
        return
    connection.send_result(msg["id"], [
        {"id": code.id, "code": code.code, "expired": code.expired}
        for code in codes
    ])


@websocket_api.websocket_command({
    vol.Required("type"): "gcom/access_codes/create",
    vol.Required("entry_id"): str,
    vol.Required("facility_id"): _ID,
    vol.Required("expires_at"): str,
    vol.Required("confirmed"): True,
})
@websocket_api.async_response
async def ws_create_code(hass, connection, msg):
    runtime = _runtime(hass, connection, msg)
    if runtime is None:
        return
    try:
        expires = datetime.fromisoformat(msg["expires_at"].replace("Z", "+00:00"))
        if expires.tzinfo is None or expires.utcoffset() is None or expires <= datetime.now(timezone.utc):
            raise ValueError
    except (ValueError, OverflowError):
        connection.send_error(msg["id"], "invalid_expiry", "A future date with timezone is required")
        return
    try:
        code = await runtime.client.create_access_code(msg["facility_id"], expires, confirmed=True)
    except (GcomError, ValueError) as exc:
        _error(connection, msg, exc)
        return
    connection.send_result(msg["id"], {"id": code.id, "code": code.code, "expired": code.expired})


def async_register_commands(hass):
    for command in (ws_entries, ws_facilities, ws_list_codes, ws_create_code):
        websocket_api.async_register_command(hass, command)
