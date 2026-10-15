"""Focused tests with only fabricated account data and no network access."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.gcom import EntryTokenStore, _async_options_updated
from custom_components.gcom._vendor.gcom_client import (
    AccessCode, AuthenticationError, GcomError, MutationOutcomeUnknown,
)
from custom_components.gcom.button import GcomEntranceButton
from custom_components.gcom.const import CONF_CODES_ENABLED, CONF_OPEN_ENABLED, CONF_REFRESH_TOKEN
from custom_components.gcom.coordinator import GcomCoordinator
from custom_components.gcom.diagnostics import async_get_config_entry_diagnostics
from custom_components.gcom.websocket_api import ws_create_code, ws_list_codes


def test_refresh_rotation_updates_entry_without_reload():
    entry = SimpleNamespace(data={CONF_REFRESH_TOKEN: "first"})
    hass = MagicMock()

    def update_entry(_, *, data):
        entry.data = data

    hass.config_entries.async_update_entry.side_effect = update_entry
    store = EntryTokenStore(hass, entry)
    asyncio.run(store.save_refresh_token("second"))
    assert entry.data[CONF_REFRESH_TOKEN] == "second"
    asyncio.run(store.save_refresh_token("second"))
    hass.config_entries.async_update_entry.assert_called_once()


def test_options_reload_only_after_option_change():
    async def run():
        entry = SimpleNamespace(entry_id="entry", options={"poll_minutes": 10})
        hass = MagicMock()
        hass.data = {"gcom": {"entry": SimpleNamespace(options={"poll_minutes": 10})}}
        hass.config_entries.async_reload = AsyncMock()
        await _async_options_updated(hass, entry)
        hass.config_entries.async_reload.assert_not_awaited()
        entry.options = {"poll_minutes": 15}
        await _async_options_updated(hass, entry)
        hass.config_entries.async_reload.assert_awaited_once_with("entry")

    asyncio.run(run())


def _ws_fixture(*, admin=True, codes_enabled=True):
    client = SimpleNamespace(
        get_access_codes=AsyncMock(return_value=[AccessCode("1", "SECRET-CODE", "2030-01-01", "2")]),
        create_access_code=AsyncMock(return_value=AccessCode("3", "NEW-SECRET", "2030-01-01", "2")),
    )
    entry = SimpleNamespace(domain="gcom", options={CONF_CODES_ENABLED: codes_enabled})
    hass = MagicMock()
    hass.data = {"gcom": {"entry": SimpleNamespace(client=client)}}
    hass.config_entries.async_get_entry.return_value = entry
    connection = MagicMock()
    connection.user.is_admin = admin
    return hass, connection, client


def test_codes_are_admin_only_and_option_gated():
    async def run():
        msg = {"id": 1, "entry_id": "entry", "facility_id": "2"}
        hass, connection, client = _ws_fixture(admin=False)
        await ws_list_codes.__wrapped__(hass, connection, msg)
        connection.send_error.assert_called_once()
        client.get_access_codes.assert_not_awaited()

        hass, connection, client = _ws_fixture(codes_enabled=False)
        await ws_list_codes.__wrapped__(hass, connection, msg)
        connection.send_error.assert_called_once()
        client.get_access_codes.assert_not_awaited()

        hass, connection, client = _ws_fixture()
        await ws_list_codes.__wrapped__(hass, connection, msg)
        client.get_access_codes.assert_awaited_once_with("2")
        assert connection.send_result.call_args.args[1][0]["code"] == "SECRET-CODE"

    asyncio.run(run())


def test_unknown_creation_result_is_not_retried_or_leaked():
    async def run():
        hass, connection, client = _ws_fixture()
        client.create_access_code.side_effect = MutationOutcomeUnknown("sensitive value")
        msg = {
            "id": 1, "entry_id": "entry", "facility_id": "2", "confirmed": True,
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        }
        await ws_create_code.__wrapped__(hass, connection, msg)
        client.create_access_code.assert_awaited_once()
        assert client.create_access_code.call_args.kwargs["confirmed"] is True
        assert connection.send_error.call_args.args[1] == "outcome_unknown"
        assert "sensitive value" not in str(connection.send_error.call_args)

    asyncio.run(run())


def test_create_requires_timezone_aware_future_expiry():
    async def run():
        hass, connection, client = _ws_fixture()
        await ws_create_code.__wrapped__(hass, connection, {
            "id": 1, "entry_id": "entry", "facility_id": "2", "confirmed": True,
            "expires_at": "2030-01-01T12:00:00",
        })
        assert connection.send_error.call_args.args[1] == "invalid_expiry"
        client.create_access_code.assert_not_awaited()

    asyncio.run(run())


def test_diagnostics_only_counts():
    async def run():
        data = SimpleNamespace(
            devices=[SimpleNamespace(name="PRIVATE")],
            entrances=[SimpleNamespace(entrances=[SimpleNamespace(name="PRIVATE")])],
            facilities=[SimpleNamespace(name="PRIVATE")],
        )
        runtime = SimpleNamespace(coordinator=SimpleNamespace(data=data, last_update_success=True))
        hass = SimpleNamespace(data={"gcom": {"entry": runtime}})
        entry = SimpleNamespace(entry_id="entry", data={CONF_REFRESH_TOKEN: "SECRET"})
        result = await async_get_config_entry_diagnostics(hass, entry)
        assert result["entrance_count"] == 1
        assert "SECRET" not in str(result)
        assert "PRIVATE" not in str(result)

    asyncio.run(run())


def test_button_uses_confirmed_entrance_once():
    async def run():
        runtime = SimpleNamespace(
            options={CONF_OPEN_ENABLED: True},
            client=SimpleNamespace(open_entrance=AsyncMock()),
            coordinator=SimpleNamespace(
                last_update_success=True,
                data=SimpleNamespace(entrances=[SimpleNamespace(entrances=[SimpleNamespace(id="42")])]),
            ),
        )
        button = object.__new__(GcomEntranceButton)
        button._runtime = runtime
        button.coordinator = runtime.coordinator
        button._entrance_id = "42"
        await button.async_press()
        runtime.client.open_entrance.assert_awaited_once_with("42", confirmed=True)

    asyncio.run(run())


def test_removed_or_unavailable_entrance_cannot_open():
    async def run():
        runtime = SimpleNamespace(
            options={CONF_OPEN_ENABLED: True},
            client=SimpleNamespace(open_entrance=AsyncMock()),
            coordinator=SimpleNamespace(last_update_success=True, data=SimpleNamespace(entrances=[])),
        )
        button = object.__new__(GcomEntranceButton)
        button._runtime = runtime
        button.coordinator = runtime.coordinator
        button._entrance_id = "42"
        with pytest.raises(HomeAssistantError):
            await button.async_press()
        runtime.coordinator.last_update_success = False
        with pytest.raises(HomeAssistantError):
            await button.async_press()
        runtime.client.open_entrance.assert_not_awaited()

    asyncio.run(run())


def test_disabled_button_cannot_open():
    async def run():
        runtime = SimpleNamespace(options={CONF_OPEN_ENABLED: False}, client=SimpleNamespace(open_entrance=AsyncMock()))
        button = object.__new__(GcomEntranceButton)
        button._runtime = runtime
        button._entrance_id = "42"
        with pytest.raises(HomeAssistantError):
            await button.async_press()
        runtime.client.open_entrance.assert_not_awaited()

    asyncio.run(run())


def test_coordinator_distinguishes_auth_from_transient_error():
    async def run():
        client = SimpleNamespace(get_devices=AsyncMock(side_effect=AuthenticationError()))
        with pytest.raises(ConfigEntryAuthFailed):
            await GcomCoordinator._async_update_data(SimpleNamespace(client=client))
        client.get_devices.side_effect = GcomError()
        with pytest.raises(UpdateFailed):
            await GcomCoordinator._async_update_data(SimpleNamespace(client=client))

    asyncio.run(run())
