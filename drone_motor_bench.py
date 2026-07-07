"""Simulated quad-motor drone bench for instro demo.

Models per-motor power rails on a HIL test bench: throttle commands, stall
detection, isolating a faulted motor, and autonomous recovery landing on the
remaining motors.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum

from instro.lib import Command, Instrument, Measurement
from instro.lib.instrument import publish_command, publish_measurement

logger = logging.getLogger(__name__)

NUM_MOTORS = 4
STALL_CURRENT_A = 6.0
RECOVERY_DURATION_S = 6.0
MOTOR_RESPONSE_RATE = 0.25  # fraction of target reached per daemon tick


class FlightMode(str, Enum):
    OFF = "off"
    RUNNING = "running"
    RECOVERY_LANDING = "recovery_landing"


@dataclass
class MotorState:
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
    motors: list[MotorState]
    events: list[FaultEvent] = field(default_factory=list)


class DroneMotorBench(Instrument):
    """Four-motor bench instrument with stall detection and recovery landing."""

    def __init__(self, name: str = "drone_bench", **kwargs):
        super().__init__(name, **kwargs)
        self.background_interval = 0.1
        self.add_background_daemon_function(self._tick)

        self._lock = threading.Lock()
        self._mode = FlightMode.OFF
        self._throttle_pct = 0.0
        self._motors = [MotorState(index=i + 1) for i in range(NUM_MOTORS)]
        self._events: list[FaultEvent] = []
        self._recovery_started_ns: int | None = None
        self._recovery_initial_speeds: dict[int, float] = {}
        self._stalled_motor: int | None = None
        self._inject_stall_motor: int | None = None

    def snapshot(self) -> BenchSnapshot:
        with self._lock:
            return BenchSnapshot(
                mode=self._mode,
                throttle_pct=self._throttle_pct,
                motors=[
                    MotorState(
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

    @publish_command
    def power_on(self, **kwargs) -> Command:
        with self._lock:
            self._mode = FlightMode.RUNNING
            self._throttle_pct = 0.0
            self._reset_motors_locked()
        return self._package_command("power.cmd", "on", time.time_ns(), **kwargs)

    @publish_command
    def power_off(self, **kwargs) -> Command:
        with self._lock:
            self._mode = FlightMode.OFF
            self._throttle_pct = 0.0
            self._recovery_started_ns = None
            self._stalled_motor = None
            self._inject_stall_motor = None
            self._reset_motors_locked()
        return self._package_command("power.cmd", "off", time.time_ns(), **kwargs)

    @publish_command
    def set_throttle(self, throttle_pct: float, **kwargs) -> Command:
        throttle_pct = max(0.0, min(100.0, throttle_pct))
        with self._lock:
            if self._mode == FlightMode.RUNNING:
                self._throttle_pct = throttle_pct
        return self._package_command("throttle.cmd", throttle_pct, time.time_ns(), **kwargs)

    @publish_command
    def inject_stall(self, motor: int, **kwargs) -> Command:
        """Simulate a mechanical prop stall on the given motor (1-indexed)."""
        if not 1 <= motor <= NUM_MOTORS:
            raise ValueError(f"motor must be 1..{NUM_MOTORS}, got {motor}")
        with self._lock:
            if self._mode == FlightMode.RUNNING:
                self._inject_stall_motor = motor
        return self._package_command(f"m{motor}.stall_inject.cmd", 1.0, time.time_ns(), **kwargs)

    def _reset_motors_locked(self) -> None:
        for motor in self._motors:
            motor.enabled = True
            motor.speed_pct = 0.0
            motor.current_a = 0.0
            motor.stalled = False

    def _motor(self, index: int) -> MotorState:
        return self._motors[index - 1]

    def _tick(self, **kwargs) -> list[Measurement]:
        measurements: list[Measurement] = []
        now = time.time_ns()

        with self._lock:
            if self._mode == FlightMode.OFF:
                for motor in self._motors:
                    motor.speed_pct = 0.0
                    motor.current_a = 0.0
            elif self._mode == FlightMode.RUNNING:
                self._simulate_running(now)
            elif self._mode == FlightMode.RECOVERY_LANDING:
                self._simulate_recovery_landing(now)

            measurements = self._publish_motor_measurements(now, **kwargs)

        for measurement in measurements:
            self.publish(measurement)
        return measurements

    def _simulate_running(self, now: int) -> None:
        if self._inject_stall_motor is not None:
            motor_idx = self._inject_stall_motor
            motor = self._motor(motor_idx)
            motor.current_a = 12.0
            motor.speed_pct = max(0.0, motor.speed_pct - 15.0)
            self._inject_stall_motor = None
            self._handle_stall(motor_idx, motor.current_a, now)
            return

        target = self._throttle_pct
        for motor in self._motors:
            if not motor.enabled:
                motor.speed_pct = 0.0
                motor.current_a = 0.0
                continue
            motor.speed_pct += (target - motor.speed_pct) * MOTOR_RESPONSE_RATE
            motor.current_a = 0.15 + 0.045 * motor.speed_pct

            if motor.current_a >= STALL_CURRENT_A:
                self._handle_stall(motor.index, motor.current_a, now)
                return

    def _handle_stall(self, motor_idx: int, peak_current_a: float, now: int) -> None:
        if self._mode != FlightMode.RUNNING:
            return

        motor = self._motor(motor_idx)
        motor.enabled = False
        motor.stalled = True
        motor.speed_pct = 0.0
        motor.current_a = 0.0

        event = FaultEvent(
            timestamp_ns=now,
            motor=motor_idx,
            peak_current_a=peak_current_a,
            message=f"Motor {motor_idx} stall detected — channel isolated, recovery landing active",
        )
        self._events.append(event)
        self._stalled_motor = motor_idx
        self._mode = FlightMode.RECOVERY_LANDING
        self._recovery_started_ns = now
        self._recovery_initial_speeds = {
            m.index: m.speed_pct for m in self._motors if m.enabled and not m.stalled
        }
        logger.warning("Stall on motor %d (%.1f A). Entering recovery landing.", motor_idx, peak_current_a)

        fault_measurement = Measurement(
            channel_data={
                f"{self.name}.fault.motor": [float(motor_idx)],
                f"{self.name}.fault.peak_current_a": [peak_current_a],
            },
            timestamps=[now],
            tags={**self.default_tags, "event": "motor_stall"},
        )
        self.publish(fault_measurement)

    def _simulate_recovery_landing(self, now: int) -> None:
        assert self._recovery_started_ns is not None

        elapsed_s = (now - self._recovery_started_ns) * 1e-9
        progress = min(1.0, elapsed_s / RECOVERY_DURATION_S)
        decay = 1.0 - progress

        for motor in self._motors:
            if motor.stalled or not motor.enabled:
                motor.speed_pct = 0.0
                motor.current_a = 0.0
                continue

            initial = self._recovery_initial_speeds.get(motor.index, self._throttle_pct)
            motor.speed_pct = initial * decay
            motor.current_a = 0.15 + 0.045 * motor.speed_pct

    def _publish_motor_measurements(self, now: int, **kwargs) -> list[Measurement]:
        measurements: list[Measurement] = []
        for motor in self._motors:
            measurements.append(
                self._package_measurement(f"m{motor.index}.speed_pct", motor.speed_pct, now, **kwargs)
            )
            measurements.append(
                self._package_measurement(f"m{motor.index}.current_a", motor.current_a, now, **kwargs)
            )
        measurements.append(
            self._package_measurement("mode", float(list(FlightMode).index(self._mode)), now, **kwargs)
        )
        return measurements
