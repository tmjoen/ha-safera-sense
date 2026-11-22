"""The Safera Sense integration.

Local BLE integration for Safera Sense cooking sensors, as built into
Røros Hetta (and other) cooker hoods. Protocol reverse engineered by
https://github.com/magicus/safera-ble and
https://github.com/havardgulldahl/rorossense-ble
"""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady

from safera_sense_ble import const as ble_const

from .const import CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
from .coordinator import SaferaSenseCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.FAN,
    Platform.LIGHT,
    Platform.SENSOR,
    Platform.SWITCH,
]

type SaferaSenseConfigEntry = ConfigEntry[SaferaSenseCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: SaferaSenseConfigEntry
) -> bool:
    """Set up Safera Sense from a config entry."""
    address: str = entry.unique_id or entry.data["address"]

    ble_device = bluetooth.async_ble_device_from_address(
        hass, address, connectable=True
    )
    if ble_device is None:
        raise ConfigEntryNotReady(
            f"Safera Sense device {address} is not present; make sure a "
            "Bluetooth adapter or proxy is in range"
        )

    coordinator = SaferaSenseCoordinator(
        hass,
        ble_device,
        update_interval=entry.options.get(
            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
        ),
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass, coordinator)
    return True


# Every readable characteristic, for reverse-engineering state that the
# sensor report does not carry (e.g. the hood's bathroom-ventilation mode).
_DUMP_CHARS = (
    "CHAR_SENSOR_REPORT",
    "CHAR_DEVICE_COMMAND",
    "CHAR_CLOUD_WIFI_STATUS",
    "CHAR_EVENT_LOG",
    "CHAR_DAY_STATISTICS",
    "CHAR_LOG",
    "CHAR_VOC",
    "CHAR_READ_SETTINGS",
    "CHAR_READ_CONFIG_BANK",
    "CHAR_GDT_DATA",
    "CHAR_DCV_SENSOR_REPORT",
)


def _async_register_services(
    hass: HomeAssistant, coordinator: SaferaSenseCoordinator
) -> None:
    """Register raw-command and dump services (single-device use)."""
    if hass.services.has_service("safera_sense", "send_command"):
        return

    async def _send_command(call: ServiceCall) -> None:
        await coordinator.client.send_command(
            int(call.data["command"]), int(call.data["param"])
        )

    async def _dump(call: ServiceCall) -> dict:
        client = coordinator.client
        gatt = client._ensure_client()  # noqa: SLF001
        out: dict[str, str] = {}
        for name in _DUMP_CHARS:
            uuid = getattr(ble_const, name, None)
            if uuid is None:
                continue
            try:
                raw = await client._authed(gatt.read_gatt_char, uuid)  # noqa: SLF001
                out[name] = bytes(raw).hex()
            except Exception as err:  # noqa: BLE001
                out[name] = f"ERR {type(err).__name__}: {err}"
        return out

    hass.services.async_register("safera_sense", "send_command", _send_command)
    hass.services.async_register(
        "safera_sense", "dump", _dump, supports_response=SupportsResponse.ONLY
    )


async def _async_options_updated(
    hass: HomeAssistant, entry: SaferaSenseConfigEntry
) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: SaferaSenseConfigEntry
) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    ):
        await entry.runtime_data.async_shutdown()
    return unload_ok
