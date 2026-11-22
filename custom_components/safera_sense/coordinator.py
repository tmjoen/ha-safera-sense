"""Data update coordinator for the Safera Sense integration."""

from __future__ import annotations

from datetime import timedelta
import logging
import time

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from safera_sense_ble import (
    CookingEvent,
    DeviceInfo,
    SensorReport,
    WifiStatus,
)

from .client import TrackingSaferaClient
from .const import (
    DEFAULT_UPDATE_INTERVAL,
    MODE_BATHROOM_HA,
    MODE_BATHROOM_HOOD,
    MODE_KITCHEN,
    MODE_OFF,
    REPORT_MOTOR1,
    REPORT_MOTOR2,
    ROOF_FAN_IDLE_MAX,
    STEP_COMMAND_GRACE,
)

_LOGGER = logging.getLogger(__name__)

# Fields whose changes always propagate to HA immediately, bypassing the
# update-interval throttle: safety, cooking activity and device controls.
EVENTFUL_FIELDS = (
    "device_state",
    "alarm_status",
    "alarm_level",
    "sensor_errors",
    "pcu_errors",
    "activity_type",
    "connected_accessories",
    "fan_speed_raw",
    "hood_flags",
    "light_raw",
    "grease_filter",
)


def is_eventful_change(old: SensorReport | None, new: SensorReport) -> bool:
    """Return True when an immediately-relevant field changed."""
    if old is None:
        return True
    return any(getattr(old, field) != getattr(new, field) for field in EVENTFUL_FIELDS)


class SaferaSenseCoordinator(DataUpdateCoordinator[SensorReport]):
    """Maintains the BLE connection and distributes sensor reports.

    BLE notifications arrive about once per second. Eventful changes
    (alarms, cooking, fan/light/filter state) are forwarded to HA
    immediately; environmental drift is forwarded at most once per
    `update_interval` seconds to limit state churn.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        ble_device,
        update_interval: int = DEFAULT_UPDATE_INTERVAL,
    ) -> None:
        self.ble_device = ble_device
        self.client = TrackingSaferaClient(ble_device)
        self.device_info: DeviceInfo | None = None
        self.wifi_status: WifiStatus | None = None
        self._shutdown = False
        self._throttle_seconds = update_interval
        self._last_push = 0.0
        # Smart Cooking: the most recent event-log entry (newest first),
        # used to derive the current cooking phase.
        self.last_cooking_event: CookingEvent | None = None
        # Ventilation state derived from the raw report (see const.py).
        self.motor1_raw: int | None = None
        self.motor2_raw: int | None = None
        self.ventilation_mode: str | None = None
        self._prev_step: int | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"Safera Sense {ble_device.address}",
            # The poll is a connection watchdog; notifications reset this
            # timer on every push, so it only fires when the stream stalls.
            update_interval=timedelta(seconds=update_interval + 60),
        )

    @property
    def address(self) -> str:
        return self.client.address

    async def _async_setup(self) -> None:
        """Called once before the first refresh."""
        await self._async_ensure_connected()

    async def _async_ensure_connected(self) -> None:
        """(Re)connect, read static info and subscribe to notifications."""
        if self.client.is_connected:
            return

        # Ask HA's Bluetooth stack for a fresh BLEDevice: after a
        # disconnect the device may now be reachable via a different
        # adapter or proxy.
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is not None:
            self.client.set_ble_device(ble_device)

        await self.client.connect(disconnected_callback=self._handle_disconnect)

        if self.device_info is None:
            try:
                self.device_info = await self.client.fetch_device_info()
                _LOGGER.debug("Device info: %s", self.device_info)
            except Exception:  # noqa: BLE001
                _LOGGER.warning("Could not read device information", exc_info=True)

        try:
            self.wifi_status = await self.client.fetch_wifi_status()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Could not read Wi-Fi status", exc_info=True)

        await self.client.subscribe_sensor_reports(self._handle_report)

        # Read the current Smart Cooking timeline, then subscribe for
        # updates so the cooking phase stays current.
        try:
            self._update_last_cooking_event(await self.client.fetch_event_log())
            await self.client.subscribe_event_log(self._handle_event_log)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Could not set up cooking event log", exc_info=True)

        if _LOGGER.isEnabledFor(logging.DEBUG):
            # Protocol investigation: dump all readable characteristics
            # once per connection to help decode remaining unknown fields.
            try:
                for name, hexdump in (
                    await self.client.dump_characteristics()
                ).items():
                    _LOGGER.debug("CHAR DUMP %s = %s", name, hexdump)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Characteristic dump failed", exc_info=True)

    def _update_ventilation(self, report: SensorReport) -> bool:
        """Derive motor outputs and ventilation mode; True if they changed.

        The hood never reports its bathroom mode. It looks exactly like
        level 4 (step 120, same motor outputs), with the damper shut. The
        only tell is how it gets there: the bathroom mode jumps from step
        0 straight to 120 in one report, while + presses walk through
        30/60/90 about two seconds apart. A jump HA asked for is excluded
        by STEP_COMMAND_GRACE.
        """
        before = (self.motor1_raw, self.motor2_raw, self.ventilation_mode)
        self.motor1_raw = self.client.raw_byte(REPORT_MOTOR1)
        self.motor2_raw = self.client.raw_byte(REPORT_MOTOR2)
        step = report.fan_speed_raw
        prev = self._prev_step
        if step is None:
            mode = None
        elif step == 0:
            mode = (
                MODE_BATHROOM_HA
                if (self.motor2_raw or 0) > ROOF_FAN_IDLE_MAX
                else MODE_OFF
            )
        elif step == 120 and self.ventilation_mode == MODE_BATHROOM_HOOD:
            mode = MODE_BATHROOM_HOOD
        elif (
            step == 120
            and prev == 0
            and not report.fan_auto
            and time.monotonic() - self.client.last_step_command
            > STEP_COMMAND_GRACE
        ):
            mode = MODE_BATHROOM_HOOD
        else:
            mode = MODE_KITCHEN
        self._prev_step = step
        self.ventilation_mode = mode
        return before != (self.motor1_raw, self.motor2_raw, self.ventilation_mode)

    def _handle_report(self, report: SensorReport) -> None:
        """Handle a pushed sensor report (called from the event loop)."""
        now = time.monotonic()
        ventilation_changed = self._update_ventilation(report)
        if (
            ventilation_changed
            or is_eventful_change(self.data, report)
            or now - self._last_push >= self._throttle_seconds
        ):
            self._last_push = now
            self.async_set_updated_data(report)

    def _update_last_cooking_event(self, events: list[CookingEvent]) -> None:
        """Store the most recent event (the list is newest-first)."""
        self.last_cooking_event = events[0] if events else None

    @callback
    def _handle_event_log(self, events: list[CookingEvent]) -> None:
        """Handle a Smart Cooking event-log notification."""
        self._update_last_cooking_event(events)
        # Refresh entities that derive the cooking phase from this.
        self.async_update_listeners()

    def _handle_disconnect(self, _client) -> None:
        """Handle an unexpected disconnect; schedule a reconnect."""
        if self._shutdown:
            return
        _LOGGER.debug("%s disconnected; scheduling reconnect", self.address)
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self.async_request_refresh())
        )

    async def _async_update_data(self) -> SensorReport:
        """Polling fallback: reconnect if needed and read a snapshot."""
        try:
            await self._async_ensure_connected()
            return await self.client.fetch_sensor_report()
        except Exception as err:
            raise UpdateFailed(f"Cannot reach device: {err}") from err

    async def async_shutdown(self) -> None:
        """Disconnect cleanly on unload/stop."""
        self._shutdown = True
        await super().async_shutdown()
        try:
            await self.client.disconnect()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Error during disconnect", exc_info=True)
