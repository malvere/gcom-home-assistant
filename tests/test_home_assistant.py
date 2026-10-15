"""Exercise Home Assistant's real config entry and flow machinery with fake API data."""

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gcom._vendor.gcom_client import Device, Entrance, EntranceGroup, Facility, GcomError
from custom_components.gcom.const import (
    CONF_BASE_URL,
    CONF_OPEN_ENABLED,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_URL,
    DOMAIN,
)


DATA = {
    CONF_BASE_URL: "https://api.example.invalid/",
    CONF_TOKEN_URL: "https://auth.example.invalid/token",
    CONF_CLIENT_ID: "client",
    CONF_CLIENT_SECRET: "secret",
    CONF_REFRESH_TOKEN: "initial",
}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_setup_dynamic_entrances_and_unload(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, options={CONF_OPEN_ENABLED: True})
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.get_devices.return_value = [Device("9", "Intercom")]
    client.get_entrances.return_value = [EntranceGroup("1", "Building", (
        Entrance("42", "Подъезд №1"), Entrance("43", "Двор"),
    ))]
    client.get_facilities.return_value = [Facility("2", "Apartment")]
    with patch("custom_components.gcom.GcomClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        front_id = registry.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_entrance_42")
        yard_id = registry.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_entrance_43")
        assert front_id and yard_id and front_id != yard_id
        assert "Подъезд №1" in registry.async_get(front_id).original_name
        assert "Двор" in registry.async_get(yard_id).original_name
        await hass.services.async_call("button", "press", {"entity_id": front_id}, blocking=True)
        client.open_entrance.assert_awaited_once_with("42", confirmed=True)

        client.get_entrances.return_value = [EntranceGroup("1", "Building", (
            Entrance("42", "Новый подъезд"), Entrance("44", "Калитка"),
        ))]
        await hass.data[DOMAIN][entry.entry_id].coordinator.async_refresh()
        await hass.async_block_till_done()
        assert registry.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_entrance_42") == front_id
        assert "Новый подъезд" in registry.async_get(front_id).original_name
        assert hass.states.get(yard_id).state == "unavailable"
        assert registry.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_entrance_44")
        await hass.services.async_call("button", "press", {"entity_id": yard_id}, blocking=True)
        client.open_entrance.assert_awaited_once()

        client.get_entrances.return_value = [EntranceGroup("1", "Building", (
            Entrance("42", "Новый подъезд"), Entrance("43", "Двор"),
        ))]
        await hass.data[DOMAIN][entry.entry_id].coordinator.async_refresh()
        await hass.async_block_till_done()
        assert registry.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_entrance_43") == yard_id
        assert hass.states.get(yard_id).state != "unavailable"

        client.get_entrances.side_effect = GcomError("unavailable")
        await hass.data[DOMAIN][entry.entry_id].coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(front_id).state == "unavailable"
        client.open_entrance.assert_awaited_once()

        assert await hass.config_entries.async_unload(entry.entry_id)
        assert entry.entry_id not in hass.data[DOMAIN]
        client.close.assert_awaited_once()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_entrances_discovered_when_opening_disabled_and_group_renamed(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.get_devices.return_value = []
    client.get_entrances.return_value = [EntranceGroup("1", "Здание", (
        Entrance("42", "Подъезд №1"), Entrance("43", "Двор"),
    ))]
    client.get_facilities.return_value = []
    with patch("custom_components.gcom.GcomClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        front_id = registry.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_entrance_42")
        yard_id = registry.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_entrance_43")
        assert front_id and yard_id
        assert hass.states.get(front_id).state == "unavailable"
        assert hass.states.get(yard_id).state == "unavailable"
        await hass.services.async_call("button", "press", {"entity_id": front_id}, blocking=True)
        client.open_entrance.assert_not_awaited()

        devices = dr.async_get(hass)
        device = devices.async_get_device_by_identifier(
            (DOMAIN, f"{entry.entry_id}_group_1"), entry.entry_id
        )
        assert device and device.name == "Здание"
        client.get_entrances.return_value = [EntranceGroup("1", "Дом", (
            Entrance("42", "Подъезд №1"), Entrance("43", "Двор"),
        ))]
        await hass.data[DOMAIN][entry.entry_id].coordinator.async_refresh()
        await hass.async_block_till_done()
        assert devices.async_get(device.id).name == "Дом"
        assert registry.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_entrance_42") == front_id


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_config_flow_persists_rotated_refresh_token(hass):
    async def rotate():
        await store.save_refresh_token("rotated")

    with patch("custom_components.gcom.config_flow.GcomClient") as factory:
        client = factory.return_value
        factory.side_effect = lambda **kwargs: _capture_store(kwargs, client)
        store = None

        def _capture_store(kwargs, fake):
            nonlocal store
            store = kwargs["token_store"]
            return fake

        client.authenticate.side_effect = rotate
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=DATA)
    assert result["type"] == "create_entry"
    assert result["data"][CONF_REFRESH_TOKEN] == "rotated"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_duplicate_aborts_before_token_grant(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="gcom_account")
    entry.add_to_hass(hass)
    with patch("custom_components.gcom.config_flow.GcomClient") as client_factory:
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "abort"
    assert result["reason"] == "single_instance_allowed"
    client_factory.assert_not_called()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_reauth_updates_token_and_reloads_once(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="gcom_account")
    entry.add_to_hass(hass)
    with (
        patch("custom_components.gcom.config_flow.GcomClient") as client_factory,
        patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock) as reload_entry,
    ):
        client_factory.return_value.authenticate = AsyncMock()
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reauth", "entry_id": entry.entry_id},
            data=entry.data,
        )
        assert result["type"] == "form"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_REFRESH_TOKEN: "replacement"}
        )
    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_REFRESH_TOKEN] == "replacement"
    client_factory.return_value.authenticate.assert_awaited_once()
    reload_entry.assert_awaited_once_with(entry.entry_id)
