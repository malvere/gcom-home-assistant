"""10G.com Home Assistant integration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ._vendor.gcom_client import AuthenticationError, GcomClient, GcomError
from .const import (
    CONF_BASE_URL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_URL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import GcomCoordinator
from .websocket_api import async_register_commands


@dataclass
class GcomRuntime:
    client: GcomClient
    coordinator: GcomCoordinator
    options: dict


class EntryTokenStore:
    """Persist refresh rotation in HA's config entry storage."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

    async def load_refresh_token(self) -> str | None:
        return self.entry.data.get(CONF_REFRESH_TOKEN)

    async def save_refresh_token(self, token: str) -> None:
        if token != self.entry.data.get(CONF_REFRESH_TOKEN):
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, CONF_REFRESH_TOKEN: token}
            )

    async def clear_refresh_token(self) -> None:
        self.hass.config_entries.async_update_entry(
            self.entry, data={key: value for key, value in self.entry.data.items() if key != CONF_REFRESH_TOKEN}
        )


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    async_register_commands(hass)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(
            "/gcom/gcom-access-codes.js",
            str(Path(__file__).parent / "frontend" / "gcom-access-codes.js"),
            cache_headers=True,
        )]
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = GcomClient(
        base_url=entry.data[CONF_BASE_URL],
        token_url=entry.data[CONF_TOKEN_URL],
        client_id=entry.data[CONF_CLIENT_ID],
        client_secret=entry.data[CONF_CLIENT_SECRET],
        token_store=EntryTokenStore(hass, entry),
        session=async_get_clientsession(hass),
    )
    try:
        await client.authenticate()
        coordinator = GcomCoordinator(hass, entry, client, dict(entry.options))
        await coordinator.async_config_entry_first_refresh()
    except AuthenticationError as exc:
        raise ConfigEntryAuthFailed("10G.com authorization expired") from exc
    except GcomError as exc:
        raise ConfigEntryNotReady("10G.com connection failed") from exc
    hass.data[DOMAIN][entry.entry_id] = GcomRuntime(client, coordinator, dict(entry.options))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # Token rotation updates entry.data; only options changes require a reload.
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is None:
        return
    if runtime.options != dict(entry.options):
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime = hass.data[DOMAIN].pop(entry.entry_id)
        await runtime.client.close()
    return unloaded
