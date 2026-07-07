"""Bench supervisor: real InstroDAQ calls for stall detection and recovery landing."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum

from instro.daq import InstroDAQ
from instro.daq.scaling.scaling import LinearScaler
from instro.daq.types import Direction

from drone_physics import CMD_VOLTAGE_MAX, NUM_MOTORS, STALL_CURRENT_A
from simulated_labjack import SimulatedLabJackT7

logger = logging.getLogger(__name__)

RECOVERY_DURATION_S = 6.0
TICK_INTERVAL_S = 0.1

MOTOR_CMD_ALIASES = ("m1_cmd", "m2_cmd", "m3_cmd", "m4_cmd")
MOTOR_CURRENT_ALIASES = ("m1_current", "m2_current", "m3_current", "m4_current")
MOTOR_TACH_ALIASES = ("m1_tach", "m2_tach", "m3_tach", "m4_tach")

MOTOR_CMD_PHYSICAL = ("DAC0", "DAC1", "TDAC0", "TDAC1")
MOTOR_CURRENT_PHYSICAL = tuple(f"AIN{i}" for i in range(4))
MOTOR_TACH_PHYSICAL = tuple(f"AIN{i}" for i in range(4, 8))


class FlightMode(str, Enum):
    OFF = "off"
    RUNNING = "running"
    RECOVERY_LANDING = "recovery_landing"


@dataclass
class MotorSnapshot:
    index: int
    enabled: bool = True
    speed_pct: float = 0.0
    current_a: float = 0.0
    stalled: bool = False


@dataclass
class FaultEvent:
    timestamp_ns: int
    motor: int
    peak_current_a: float
    message: str


@dataclass
class BenchSnapshot:
    mode: FlightMode
    throttle_pct: float
    motors: list[MotorSnapshot]
    events: list[FaultEvent] = field(default_factory=list)


class BenchSupervisor:
    """Polls InstroDAQ, detects stalls from current readings, runs recovery landing."""

    def __init__(self, name: str = "drone_bench") -> None:
        self._driver = SimulatedLabJackT7(device_id="470010000")
        self._daq = InstroDAQ(name=name, driver=self._driver)
        self._lock = threading.Lock()
        self._mode = FlightMode.OFF
        self._throttle_pct = 0.0
        self._motors = [MotorSnapshot(index=i + 1) for i in range(NUM_MOTORS)]
        self._events: list[FaultEvent] = []
        self._recovery_started_ns: int | None = None
        self._recovery_initial_cmds: dict[int, float] = {}
        self._stalled_motor: int | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._configured = False
        self._daq_open = False

    @property
    def daq(self) -> InstroDAQ:
        return self._daq

    def _configure_channels(self) -> None:
        if self._configured:
            return

        for alias, physical in zip(MOTOR_CMD_ALIASES, MOTOR_CMD_PHYSICAL):
            self._daq.configure_analog_channel(
                direction=Direction.OUTPUT,
                physical_channel=physical,
                alias=alias,
                range_min=0.0,
                range_max=CMD_VOLTAGE_MAX,
            )

        for alias, physical in zip(MOTOR_CURRENT_ALIASES, MOTOR_CURRENT_PHYSICAL):
            self._daq.configure_analog_channel(
                direction=Direction.INPUT,
                physical_channel=physical,
                alias=alias,
                range_min=0.0,
                range_max=1.5,
                scaler=LinearScaler(gain=10.0, offset=0.0, units="A"),
            )

        for alias, physical in zip(MOTOR_TACH_ALIASES, MOTOR_TACH_PHYSICAL):
            self._daq.configure_analog_channel(
                direction=Direction.INPUT,
                physical_channel=physical,
                alias=alias,
                range_min=0.0,
                range_max=CMD_VOLTAGE_MAX,
                scaler=LinearScaler(gain=20.0, offset=0.0, units="%"),
            )

        self._configured = True

    def _start_loop(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="bench_supervisor")
        self._thread.start()

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("Bench supervisor tick failed")
            self._stop.wait(TICK_INTERVAL_S)

    def _throttle_to_voltage(self, throttle_pct: float) -> float:
        return (throttle_pct / 100.0) * CMD_VOLTAGE_MAX

    def _write_motor_command(self, motor: int, voltage: float) -> None:
        alias = MOTOR_CMD_ALIASES[motor - 1]
        self._daq.write_analog_value(alias, voltage)

    def _tick(self) -> None:
        with self._lock:
            mode = self._mode
            throttle = self._throttle_pct

        if mode == FlightMode.OFF:
            return

        if mode == FlightMode.RUNNING:
            cmd_v = self._throttle_to_voltage(throttle)
            for motor in range(1, NUM_MOTORS + 1):
                self._write_motor_command(motor, cmd_v)
        elif mode == FlightMode.RECOVERY_LANDING:
            self._apply_recovery_commands()

        measurement = self._daq.read_analog()
        self._update_state_from_measurement(measurement)

        if mode == FlightMode.RUNNING:
            self._check_for_stall()

    def _apply_recovery_commands(self) -> None:
        assert self._recovery_started_ns is not None
        elapsed_s = (time.time_ns() - self._recovery_started_ns) * 1e-9
        progress = min(1.0, elapsed_s / RECOVERY_DURATION_S)
        decay = 1.0 - progress

        for motor in range(1, NUM_MOTORS + 1):
            if motor == self._stalled_motor:
                self._write_motor_command(motor, 0.0)
            else:
                initial_v = self._recovery_initial_cmds.get(motor, 0.0)
                self._write_motor_command(motor, initial_v * decay)

    def _channel_value(self, measurement, alias: str) -> float:
        key = f"{self._daq.name}.{alias}"
        return measurement.channel_data[key][-1]

    def _update_state_from_measurement(self, measurement) -> None:
        with self._lock:
            for i, motor in enumerate(self._motors, start=1):
                current_alias = MOTOR_CURRENT_ALIASES[i - 1]
                tach_alias = MOTOR_TACH_ALIASES[i - 1]
                motor.current_a = self._channel_value(measurement, current_alias)
                motor.speed_pct = self._channel_value(measurement, tach_alias)
                if motor.index == self._stalled_motor:
                    motor.stalled = True
                    motor.enabled = False

    def _check_for_stall(self) -> None:
        with self._lock:
            if self._mode != FlightMode.RUNNING:
                return
            for motor in self._motors:
                if motor.current_a >= STALL_CURRENT_A:
                    self._enter_recovery(motor.index, motor.current_a, time.time_ns())
                    return

    def _enter_recovery(self, motor_idx: int, peak_current_a: float, now: int) -> None:
        self._driver.plant.isolate_motor(motor_idx)
        self._write_motor_command(motor_idx, 0.0)

        self._events.append(
            FaultEvent(
                timestamp_ns=now,
                motor=motor_idx,
                peak_current_a=peak_current_a,
                message=(
                    f"Motor {motor_idx} stall detected — channel isolated, "
                    "recovery landing active"
                ),
            )
        )
        self._stalled_motor = motor_idx
        self._mode = FlightMode.RECOVERY_LANDING
        self._recovery_started_ns = now
        self._recovery_initial_cmds = {
            m.index: self._throttle_to_voltage(self._throttle_pct)
            for m in self._motors
            if m.index != motor_idx
        }
        logger.warning("Stall on motor %d (%.1f A). Entering recovery landing.", motor_idx, peak_current_a)

    def snapshot(self) -> BenchSnapshot:
        with self._lock:
            return BenchSnapshot(
                mode=self._mode,
                throttle_pct=self._throttle_pct,
                motors=[
                    MotorSnapshot(
                        index=m.index,
                        enabled=m.enabled,
                        speed_pct=m.speed_pct,
                        current_a=m.current_a,
                        stalled=m.stalled,
                    )
                    for m in self._motors
                ],
                events=list(self._events),
            )

    def power_on(self) -> None:
        if not self._daq_open:
            self._daq.open()
            self._daq_open = True
        self._configure_channels()
        self._driver.plant.reset()
        with self._lock:
            self._mode = FlightMode.RUNNING
            self._throttle_pct = 0.0
            self._events.clear()
            self._recovery_started_ns = None
            self._stalled_motor = None
            self._recovery_initial_cmds.clear()
            for motor in self._motors:
                motor.enabled = True
                motor.stalled = False
                motor.speed_pct = 0.0
                motor.current_a = 0.0
        self._start_loop()

    def power_off(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._driver.plant.reset()
        with self._lock:
            self._mode = FlightMode.OFF
            self._throttle_pct = 0.0
            self._recovery_started_ns = None
            self._stalled_motor = None
            self._recovery_initial_cmds.clear()
            for motor in self._motors:
                motor.enabled = True
                motor.stalled = False
                motor.speed_pct = 0.0
                motor.current_a = 0.0

    def set_throttle(self, throttle_pct: float) -> None:
        with self._lock:
            if self._mode == FlightMode.RUNNING:
                self._throttle_pct = max(0.0, min(100.0, throttle_pct))

    def inject_stall(self, motor: int) -> None:
        with self._lock:
            if self._mode != FlightMode.RUNNING:
                return
        self._driver.inject_stall(motor)
