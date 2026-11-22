"""SaferaSenseClient extended with what the bathroom-ventilation logic needs.

The upstream client parses only part of SENSOR_REPORT. Byte 57 (Motor 1)
and byte 58 (Motor 2) carry the live motor outputs. On a hood installed
as "external motor + central ventilation" (ES), Motor 2 drives the
roof-mounted fan and Motor 1 drives the damper. This subclass keeps the
last raw report and remembers when a fan-step command was sent, so a
jump the hood made itself can be told apart from one HA asked for.
"""

from __future__ import annotations

import time

from bleak.backends.characteristic import BleakGATTCharacteristic
from safera_sense_ble import DeviceCommand, SaferaSenseClient

# Commands that change the fan step (byte 56).
_STEP_COMMANDS = {
    int(DeviceCommand.SET_HOOD_MOTOR_SPEED_STEP),
    int(DeviceCommand.SET_HOOD_MOTOR_AUTO_MODE),
}


class TrackingSaferaClient(SaferaSenseClient):
    """Client that exposes the raw report and recent step commands."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.last_raw: bytes | None = None
        self.last_step_command: float = 0.0

    def _notification_handler(
        self, characteristic: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        # Store first: the parent parses and calls the report callback,
        # which reads last_raw.
        self.last_raw = bytes(data)
        super()._notification_handler(characteristic, data)

    async def send_command(self, command: DeviceCommand | int, param: int) -> None:
        if int(command) in _STEP_COMMANDS:
            self.last_step_command = time.monotonic()
        await super().send_command(command, param)

    def raw_byte(self, offset: int) -> int | None:
        raw = self.last_raw
        return raw[offset] if raw is not None and len(raw) > offset else None
