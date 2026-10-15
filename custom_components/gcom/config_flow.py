"""Refresh-token enrollment and reauthentication."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from ._vendor.gcom_client import AuthenticationError, GcomClient, GcomError, InMemoryTokenStore
from .const import (
    CONF_BASE_URL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_CODES_ENABLED,
    CONF_OPEN_ENABLED,
    CONF_POLL_MINUTES,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_URL,
    DEFAULT_POLL_MINUTES,
    DOMAIN,
)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
_CREDENTIALS = vol.Schema({
    vol.Required(CONF_BASE_URL): str,
    vol.Required(CONF_TOKEN_URL): str,
    vol.Required(CONF_CLIENT_ID): str,
    vol.Required(CONF_CLIENT_SECRET): _PASSWORD,
    vol.Required(CONF_REFRESH_TOKEN): _PASSWORD,
})


async def _validate(hass, values: dict) -> dict:
    """Validate URLs and grant, returning the latest rotated refresh token."""
    store = InMemoryTokenStore(values[CONF_REFRESH_TOKEN])
    client = GcomClient(
        base_url=values[CONF_BASE_URL],
        token_url=values[CONF_TOKEN_URL],
        client_id=values[CONF_CLIENT_ID],
        client_secret=values[CONF_CLIENT_SECRET],
        session=async_get_clientsession(hass),
        token_store=store,
    )
    await client.authenticate()
    return {**values, CONF_REFRESH_TOKEN: await store.load_refresh_token()}


class GcomConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            await self.async_set_unique_id("gcom_account")
            self._abort_if_unique_id_configured()
            try:
                data = await _validate(self.hass, user_input)
            except ValueError:
                return self.async_show_form(step_id="user", data_schema=_CREDENTIALS, errors={"base": "invalid_url"})
            except AuthenticationError:
                return self.async_show_form(step_id="user", data_schema=_CREDENTIALS, errors={"base": "invalid_auth"})
            except GcomError:
                return self.async_show_form(step_id="user", data_schema=_CREDENTIALS, errors={"base": "cannot_connect"})
            return self.async_create_entry(title="10G.com", data=data)
        return self.async_show_form(step_id="user", data_schema=_CREDENTIALS)

    async def async_step_reauth(self, entry_data):
        self._reauth_data = entry_data
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        schema = vol.Schema({vol.Required(CONF_REFRESH_TOKEN): _PASSWORD})
        if user_input is not None:
            entry = self._get_reauth_entry()
            try:
                data = await _validate(self.hass, {**entry.data, **user_input})
            except ValueError:
                return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors={"base": "invalid_url"})
            except AuthenticationError:
                return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors={"base": "invalid_auth"})
            except GcomError:
                return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors={"base": "cannot_connect"})
            result = self.async_update_and_abort(entry, data_updates=data)
            await self.hass.config_entries.async_reload(entry.entry_id)
            return result
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema)

    @staticmethod
    def async_get_options_flow(config_entry):
        return GcomOptionsFlow(config_entry)


class GcomOptionsFlow(OptionsFlow):
    def __init__(self, config_entry):
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self._entry.options
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required(CONF_OPEN_ENABLED, default=options.get(CONF_OPEN_ENABLED, False)): bool,
            vol.Required(CONF_CODES_ENABLED, default=options.get(CONF_CODES_ENABLED, False)): bool,
            vol.Required(CONF_POLL_MINUTES, default=options.get(CONF_POLL_MINUTES, DEFAULT_POLL_MINUTES)): vol.All(vol.Coerce(int), vol.Range(min=5, max=15)),
        }))
