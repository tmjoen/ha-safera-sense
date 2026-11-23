"""Switch platform: bathroom ventilation through the roof fan.

On a hood installed as "external motor + central ventilation" the roof
fan also serves the bathroom. Driving Motor 2 (the roof fan) with a raw
command while the fan step stays at 0 runs the roof fan with the
kitchen damper shut, so all the suction comes from the bathroom, like
the hood's own bathroom mode (hold +).

The switch refuses to start while the hood is in use, and turning it off
only stops ventilation that it started. How long it runs is left to an
automation.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from safera_sense_ble import DeviceCommand

from . import SaferaSenseConfigEntry
from .const import (
    BATHROOM_ROOF_FAN_SPEED,
    MODE_BATHROOM_HA,
    MODE_OFF,
    ROOF_FAN_BASE_SPEED,
)
from .coordinator import SaferaSenseCoordinator
from .entity import SaferaSenseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SaferaSenseConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the switch platform."""
    async_add_entities([SaferaBathroomVentilationSwitch(entry.runtime_data)])


class SaferaBathroomVentilationSwitch(SaferaSenseEntity, SwitchEntity):
    """Roof fan on, kitchen damper shut."""

    _attr_translation_key = "bathroom_ventilation"
    _attr_icon = "mdi:shower-head"

    def __init__(self, coordinator: SaferaSenseCoordinator) -> None:
        super().__init__(coordinator, "bathroom_ventilation")

    @property
    def is_on(self) -> bool | None:
        mode = self.coordinator.ventilation_mode
        return None if mode is None else mode == MODE_BATHROOM_HA

    async def async_turn_on(self, **kwargs: Any) -> None:
        mode = self.coordinator.ventilation_mode
        if mode == MODE_BATHROOM_HA:
            return
        if mode != MODE_OFF:
            raise HomeAssistantError(
                f"The hood is in use ({mode}); not overriding it"
            )
        await self.coordinator.client.send_command(
            DeviceCommand.SET_HOOD_MOTOR2_SPEED_RAW, BATHROOM_ROOF_FAN_SPEED
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        # Only stop what we started: if the hood is in use, the hood owns
        # Motor 2 and overriding it would cut the kitchen extraction.
        if self.coordinator.ventilation_mode != MODE_BATHROOM_HA:
            return
        # Back to base ventilation, not 0: a raw value sticks until the
        # hood is next used, and 0 would leave the building unventilated.
        await self.coordinator.client.send_command(
            DeviceCommand.SET_HOOD_MOTOR2_SPEED_RAW, ROOF_FAN_BASE_SPEED
        )
