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

COMPENSATE_DURATION_S = 1.0
DESCEND_DURATION_S = 5.0
TOUCHDOWN_DURATION_S = 2.0
RECOVERY_DURATION_S = COMPENSATE_DURATION_S + DESCEND_DURATION_S + TOUCHDOWN_DURATION_S
DESCEND_FRACTION = 0.45
TICK_INTERVAL_S = 0.1

MOTOR_CMD_ALIASES = tuple(f"m{i}_cmd" for i in range(1, NUM_MOTORS + 1))
MOTOR_CURRENT_ALIASES = tuple(f"m{i}_current" for i in range(1, NUM_MOTORS + 1))
MOTOR_TACH_ALIASES = tuple(f"m{i}_tach" for i in range(1, NUM_MOTORS + 1))

MOTOR_CMD_PHYSICAL = ("DAC0", "DAC1", "TDAC0", "TDAC1", "TDAC2", "TDAC3", "TDAC4", "TDAC5")
MOTOR_CURRENT_PHYSICAL = tuple(f"AIN{i}" for i in range(8))
MOTOR_TACH_PHYSICAL = tuple(f"AIN{i}" for i in range(8, 16))

MOTOR_ANGLES_DEG = {i: (i - 1) * 45 for i in range(1, NUM_MOTORS + 1)}


class FlightMode(str, Enum):
    OFF = "off"
    RUNNING = "running"
    RECOVERY_LANDING = "recovery_landing"


class RecoveryPhase(str, Enum):
    NONE = "none"
    COMPENSATE = "compensate"
    DESCEND = "descend"
    TOUCHDOWN = "touchdown"
    COMPLETE = "complete"


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
    recovery_phase: RecoveryPhase = RecoveryPhase.NONE
    recovery_progress: float = 0.0
    stalled_motor: int | None = None
    events: list[FaultEvent] = field(default_factory=list)


def compensation_weights(failed_motor: int) -> dict[int, float]:
    """Per-motor thrust multipliers for healthy motors after a single failure."""
    failed_angle = MOTOR_ANGLES_DEG[failed_motor]
    raw: dict[int, float] = {}

    for motor in range(1, NUM_MOTORS + 1):
        if motor == failed_motor:
            continue
        diff = abs(MOTOR_ANGLES_DEG[motor] - failed_angle)
        diff = min(diff, 360 - diff)

        if diff >= 135:
            base = 1.22 if diff >= 170 else 1.05
        elif diff >= 45:
            base = 1.08
        else:
            base = 1.15 if motor % 2 == 0 else 1.10

        raw[motor] = base

    total = sum(raw.values())
    scale = NUM_MOTORS / total
    return {motor: weight * scale for motor, weight in raw.items()}


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
        self._recovery_base_cmds: dict[int, float] = {}
        self._compensation_weights: dict[int, float] = {}
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

    def _recovery_elapsed_s(self) -> float:
        assert self._recovery_started_ns is not None
        return (time.time_ns() - self._recovery_started_ns) * 1e-9

    def _recovery_phase_and_progress(self, elapsed_s: float) -> tuple[RecoveryPhase, float]:
        if elapsed_s >= RECOVERY_DURATION_S:
            return RecoveryPhase.COMPLETE, 1.0
        if elapsed_s < COMPENSATE_DURATION_S:
            return RecoveryPhase.COMPENSATE, elapsed_s / COMPENSATE_DURATION_S
        if elapsed_s < COMPENSATE_DURATION_S + DESCEND_DURATION_S:
            phase_elapsed = elapsed_s - COMPENSATE_DURATION_S
            return RecoveryPhase.DESCEND, phase_elapsed / DESCEND_DURATION_S
        phase_elapsed = elapsed_s - COMPENSATE_DURATION_S - DESCEND_DURATION_S
        return RecoveryPhase.TOUCHDOWN, phase_elapsed / TOUCHDOWN_DURATION_S

    def _compensate_voltage(self, motor: int) -> float:
        base = self._recovery_base_cmds.get(motor, 0.0)
        weight = self._compensation_weights.get(motor, 1.0)
        return min(CMD_VOLTAGE_MAX, base * weight)

    def _healthy_motor_voltage(self, motor: int, phase: RecoveryPhase, phase_progress: float) -> float:
        compensate_v = self._compensate_voltage(motor)
        if phase == RecoveryPhase.COMPENSATE:
            return compensate_v
        if phase == RecoveryPhase.DESCEND:
            descend_end = compensate_v * DESCEND_FRACTION
            return compensate_v + (descend_end - compensate_v) * phase_progress
        if phase == RecoveryPhase.TOUCHDOWN:
            descend_end = compensate_v * DESCEND_FRACTION
            return descend_end * (1.0 - phase_progress)
        return 0.0

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
        elapsed_s = self._recovery_elapsed_s()
        phase, phase_progress = self._recovery_phase_and_progress(elapsed_s)

        for motor in range(1, NUM_MOTORS + 1):
            if motor == self._stalled_motor:
                self._write_motor_command(motor, 0.0)
            elif phase == RecoveryPhase.COMPLETE:
                self._write_motor_command(motor, 0.0)
            else:
                voltage = self._healthy_motor_voltage(motor, phase, phase_progress)
                self._write_motor_command(motor, voltage)

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
                    "asymmetric recovery landing active"
                ),
            )
        )
        self._stalled_motor = motor_idx
        self._mode = FlightMode.RECOVERY_LANDING
        self._recovery_started_ns = now
        hover_v = self._throttle_to_voltage(self._throttle_pct)
        self._recovery_base_cmds = {
            m.index: hover_v for m in self._motors if m.index != motor_idx
        }
        self._compensation_weights = compensation_weights(motor_idx)
        logger.warning("Stall on motor %d (%.1f A). Entering recovery landing.", motor_idx, peak_current_a)

    def _snapshot_recovery(self) -> tuple[RecoveryPhase, float]:
        if self._mode != FlightMode.RECOVERY_LANDING or self._recovery_started_ns is None:
            return RecoveryPhase.NONE, 0.0
        elapsed_s = self._recovery_elapsed_s()
        return self._recovery_phase_and_progress(elapsed_s)

    def snapshot(self) -> BenchSnapshot:
        with self._lock:
            recovery_phase, recovery_progress = self._snapshot_recovery()
            return BenchSnapshot(
                mode=self._mode,
                throttle_pct=self._throttle_pct,
                recovery_phase=recovery_phase,
                recovery_progress=recovery_progress,
                stalled_motor=self._stalled_motor,
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
            self._recovery_base_cmds.clear()
            self._compensation_weights.clear()
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
            self._recovery_base_cmds.clear()
            self._compensation_weights.clear()
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
        if not 1 <= motor <= NUM_MOTORS:
            raise ValueError(f"motor must be 1..{NUM_MOTORS}, got {motor}")
        with self._lock:
            if self._mode != FlightMode.RUNNING:
                return
            peak_current_a = max(self._motors[motor - 1].current_a, STALL_CURRENT_A)
        self._enter_recovery(motor, peak_current_a, time.time_ns())
