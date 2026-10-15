"""Explicitly enabled entrance buttons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ._vendor.gcom_client import GcomError, MutationOutcomeUnknown
from .const import CONF_OPEN_ENABLED, DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.coordinator
    known: set[str] = set()

    def add_new_entrances():
        entities = []
        for group in coordinator.data.entrances:
            for entrance in group.entrances:
                if entrance.id not in known:
                    known.add(entrance.id)
                    entities.append(GcomEntranceButton(runtime, entry.entry_id, group.id, group.name, entrance.id, entrance.name))
        if entities:
            async_add_entities(entities)

    add_new_entrances()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entrances))


class GcomEntranceButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "open_entrance"

    def __init__(self, runtime, entry_id, group_id, group_name, entrance_id, entrance_name):
        super().__init__(runtime.coordinator)
        self._runtime = runtime
        self._entry_id = entry_id
        self._group_id = group_id
        self._entrance_id = entrance_id
        self._attr_unique_id = f"{entry_id}_entrance_{entrance_id}"
        self._attr_name = entrance_name or "Entrance"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry_id}_group_{group_id}")},
            "name": group_name or "10G.com entrances",
            "manufacturer": "10G.com",
        }

    @property
    def available(self):
        return self._runtime.options.get(CONF_OPEN_ENABLED, False) and super().available and any(
            entrance.id == self._entrance_id
            for group in self.coordinator.data.entrances
            for entrance in group.entrances
        )

    def _handle_coordinator_update(self):
        for group in self.coordinator.data.entrances:
            for entrance in group.entrances:
                if entrance.id != self._entrance_id:
                    continue
                self._attr_name = entrance.name or "Entrance"
                if group.id == self._group_id:
                    name = group.name or "10G.com entrances"
                    if self._attr_device_info["name"] != name:
                        self._attr_device_info["name"] = name
                        registry = dr.async_get(self.hass)
                        device = registry.async_get_device_by_identifier(
                            (DOMAIN, f"{self._entry_id}_group_{group.id}"), self._entry_id
                        )
                        if device is not None and device.name != name:
                            registry.async_update_device(device.id, name=name)
                super()._handle_coordinator_update()
                return
        super()._handle_coordinator_update()

    async def async_press(self):
        if not self._runtime.options.get(CONF_OPEN_ENABLED, False):
            raise HomeAssistantError("Entrance opening is disabled")
        if not self.available:
            raise HomeAssistantError("Entrance is unavailable")
        try:
            await self._runtime.client.open_entrance(self._entrance_id, confirmed=True)
        except MutationOutcomeUnknown as exc:
            raise HomeAssistantError("Command result unknown; check the entrance before trying again") from exc
        except GcomError as exc:
            raise HomeAssistantError("10G.com could not open the entrance") from exc
