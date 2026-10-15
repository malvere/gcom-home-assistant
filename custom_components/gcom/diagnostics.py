"""Diagnostics expose counts only, never tokens or access codes."""

from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass, entry):
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is None or runtime.coordinator.data is None:
        return {"connected": False}
    data = runtime.coordinator.data
    return {
        "connected": runtime.coordinator.last_update_success,
        "device_count": len(data.devices),
        "entrance_group_count": len(data.entrances),
        "entrance_count": sum(len(group.entrances) for group in data.entrances),
        "facility_count": len(data.facilities),
    }
