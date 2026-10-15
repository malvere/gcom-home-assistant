"""Poll nonsecret account data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from ._vendor.gcom_client import AuthenticationError, GcomClient, GcomError
from ._vendor.gcom_client.models import Device, EntranceGroup, Facility
from .const import DEFAULT_POLL_MINUTES, CONF_POLL_MINUTES

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountData:
    """Public account metadata. No access codes or credentials."""

    devices: list[Device]
    entrances: list[EntranceGroup]
    facilities: list[Facility]


class GcomCoordinator(DataUpdateCoordinator[AccountData]):
    def __init__(self, hass: HomeAssistant, entry, client: GcomClient, options: dict) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="10G.com",
            update_interval=timedelta(minutes=options.get(CONF_POLL_MINUTES, DEFAULT_POLL_MINUTES)),
        )
        self.client = client

    async def _async_update_data(self) -> AccountData:
        try:
            return AccountData(
                devices=await self.client.get_devices(),
                entrances=await self.client.get_entrances(),
                facilities=await self.client.get_facilities(),
            )
        except AuthenticationError as exc:
            raise ConfigEntryAuthFailed("10G.com authorization expired") from exc
        except GcomError as exc:
            raise UpdateFailed("10G.com data update failed") from exc
