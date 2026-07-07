"""Simulated LabJack T7 + LJTick-DAC driver for instro InstroDAQ demos."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping

from instro.daq.daq import DAQDriverBase
from instro.daq.types import (
    AnalogChannel,
    DAQChannel,
    Direction,
    HWTimingConfig,
    Logic,
)
from instro.lib.types import Measurement

from drone_physics import CMD_VOLTAGE_MAX, DronePhysics, NUM_MOTORS

BUILTIN_AO = frozenset({"DAC0", "DAC1"})
TICK_DAC_AO = frozenset({"TDAC0", "TDAC1"})
VALID_AO = BUILTIN_AO | TICK_DAC_AO
VALID_AI = frozenset(f"AIN{i}" for i in range(8))

MOTOR_CMD_CHANNELS = ("DAC0", "DAC1", "TDAC0", "TDAC1")
CURRENT_SENSE_CHANNELS = tuple(f"AIN{i}" for i in range(4))
TACH_CHANNELS = tuple(f"AIN{i}" for i in range(4, 8))


@dataclass(frozen=True)
class AnalogReadResponse:
    values: dict[str, float]
    dt: None = None


class SimulatedLabJackT7(DAQDriverBase):
    """LabJack T7 with one LJTick-DAC — same driver interface as production code."""

    def __init__(self, device_id: str = "470010000") -> None:
        super().__init__()
        self.device_id = device_id
        self._plant = DronePhysics()
        self._is_open = False
        self._hw_timing: HWTimingConfig | None = None

    @property
    def plant(self) -> DronePhysics:
        return self._plant

    def inject_stall(self, motor: int) -> None:
        self._plant.inject_stall(motor)

    def open(self) -> None:
        self._is_open = True

    def close(self) -> None:
        self._is_open = False

    def configure_ai_channel(self, channel: AnalogChannel) -> None:
        if channel.physical_channel not in VALID_AI:
            raise ValueError(
                f"Unknown AI channel '{channel.physical_channel}'. "
                f"Valid channels: {sorted(VALID_AI)}"
            )
        self._ai_channels[channel.alias] = channel

    def configure_ao_channel(self, channel: AnalogChannel) -> None:
        if channel.physical_channel not in VALID_AO:
            raise ValueError(
                f"Unknown AO channel '{channel.physical_channel}'. "
                f"Valid channels: {sorted(VALID_AO)}"
            )
        self._ao_channels[channel.alias] = channel

    def configure_ai_hw_timing(self, hw_timing_config: HWTimingConfig) -> None:
        self._hw_timing = hw_timing_config

    def configure_di_line_channel(
        self,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
    ) -> None:
        raise NotImplementedError("Digital I/O is not used in this demo")

    def configure_do_line_channel(
        self,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
    ) -> None:
        raise NotImplementedError("Digital I/O is not used in this demo")

    def start(self, **kwargs) -> None:
        return

    def stop(self, **kwargs) -> None:
        return

    def read_analog(self) -> AnalogReadResponse:
        self._plant.step()
        values: dict[str, float] = {}
        for motor in range(1, NUM_MOTORS + 1):
            values[CURRENT_SENSE_CHANNELS[motor - 1]] = self._plant.current_sense_voltage(motor)
            values[TACH_CHANNELS[motor - 1]] = self._plant.tach_voltage(motor)
        return AnalogReadResponse(values=values)

    def fetch_analog(self) -> AnalogReadResponse:
        raise RuntimeError("Hardware-timed acquisition is not supported by SimulatedLabJackT7")

    def write_analog_value(self, channel: AnalogChannel, value: float) -> None:
        clamped = max(0.0, min(CMD_VOLTAGE_MAX, value))
        try:
            motor_idx = MOTOR_CMD_CHANNELS.index(channel.physical_channel) + 1
        except ValueError as exc:
            raise ValueError(f"Channel {channel.physical_channel} is not a motor command output") from exc
        self._plant.set_command_voltage(motor_idx, clamped)

    def write_digital_line(self, channel, data: int) -> None:
        raise NotImplementedError("Digital I/O is not used in this demo")

    def read_digital_line(self, channel) -> int:
        raise NotImplementedError("Digital I/O is not used in this demo")

    def write_digital_port(self, channel, data: int) -> None:
        raise NotImplementedError("Digital I/O is not used in this demo")

    def read_digital_port(self, channel) -> int:
        raise NotImplementedError("Digital I/O is not used in this demo")

    def _read_to_measurements(
        self,
        response: AnalogReadResponse,
        channel_list: Mapping[str, DAQChannel],
        daq_name: str,
        default_tags: dict[str, str],
        **kwargs,
    ) -> list[Measurement]:
        timestamp = time.time_ns()
        channel_data: dict[str, list[float]] = {}

        for alias, ch in channel_list.items():
            if isinstance(ch, AnalogChannel) and ch.direction == Direction.INPUT:
                voltage = response.values.get(ch.physical_channel, 0.0)
                channel_data[f"{daq_name}.{alias}"] = [voltage]

        if not channel_data:
            return []

        return [
            Measurement(
                channel_data=channel_data,
                timestamps=[timestamp],
                tags={**default_tags, **kwargs},
            )
        ]
