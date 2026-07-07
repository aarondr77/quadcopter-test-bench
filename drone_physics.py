"""Quad-motor drone plant model for the simulated LabJack bench."""

from __future__ import annotations

import threading
from dataclasses import dataclass

NUM_MOTORS = 4
STALL_CURRENT_A = 6.0
MOTOR_RESPONSE_RATE = 0.25
CMD_VOLTAGE_MAX = 5.0
CURRENT_SENSE_V_PER_A = 0.1  # 100 mV/A shunt amplifier output


@dataclass
class MotorPlantState:
    index: int
    enabled: bool = True
    stalled: bool = False
    speed_pct: float = 0.0
    current_a: float = 0.0
    command_v: float = 0.0


class DronePhysics:
    """Physical plant: motor command voltages in, current/tach sense voltages out."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._motors = [MotorPlantState(index=i + 1) for i in range(NUM_MOTORS)]
        self._pending_stall: int | None = None

    def reset(self) -> None:
        with self._lock:
            self._pending_stall = None
            for motor in self._motors:
                motor.enabled = True
                motor.stalled = False
                motor.speed_pct = 0.0
                motor.current_a = 0.0
                motor.command_v = 0.0

    def inject_stall(self, motor: int) -> None:
        if not 1 <= motor <= NUM_MOTORS:
            raise ValueError(f"motor must be 1..{NUM_MOTORS}, got {motor}")
        with self._lock:
            self._pending_stall = motor

    def set_command_voltage(self, motor: int, voltage: float) -> None:
        motor_state = self._motors[motor - 1]
        with self._lock:
            if motor_state.enabled and not motor_state.stalled:
                motor_state.command_v = max(0.0, min(CMD_VOLTAGE_MAX, voltage))

    def isolate_motor(self, motor: int) -> None:
        motor_state = self._motors[motor - 1]
        with self._lock:
            motor_state.enabled = False
            motor_state.stalled = True
            motor_state.command_v = 0.0
            motor_state.speed_pct = 0.0
            motor_state.current_a = 0.0

    def step(self) -> list[MotorPlantState]:
        with self._lock:
            stalled_this_step: set[int] = set()
            if self._pending_stall is not None:
                motor_idx = self._pending_stall
                self._pending_stall = None
                motor = self._motors[motor_idx - 1]
                motor.current_a = 12.0
                motor.speed_pct = max(0.0, motor.speed_pct - 15.0)
                stalled_this_step.add(motor_idx)

            for motor in self._motors:
                if not motor.enabled or motor.stalled:
                    motor.speed_pct = 0.0
                    motor.current_a = 0.0
                    motor.command_v = 0.0
                    continue

                if motor.index in stalled_this_step:
                    continue

                target_pct = (motor.command_v / CMD_VOLTAGE_MAX) * 100.0
                motor.speed_pct += (target_pct - motor.speed_pct) * MOTOR_RESPONSE_RATE
                motor.current_a = 0.15 + 0.045 * motor.speed_pct

            return [
                MotorPlantState(
                    index=m.index,
                    enabled=m.enabled,
                    stalled=m.stalled,
                    speed_pct=m.speed_pct,
                    current_a=m.current_a,
                    command_v=m.command_v,
                )
                for m in self._motors
            ]

    def current_sense_voltage(self, motor: int) -> float:
        return self._motors[motor - 1].current_a * CURRENT_SENSE_V_PER_A

    def tach_voltage(self, motor: int) -> float:
        return (self._motors[motor - 1].speed_pct / 100.0) * CMD_VOLTAGE_MAX

    def peak_current(self, motor: int) -> float:
        return self._motors[motor - 1].current_a
