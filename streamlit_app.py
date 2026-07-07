"""Drone motor bench demo — stall detection and autonomous recovery landing."""

from __future__ import annotations

import time

import streamlit as st

from drone_motor_bench import DroneMotorBench, FlightMode

MODE_LABELS = {
    FlightMode.OFF: "OFF",
    FlightMode.RUNNING: "RUNNING",
    FlightMode.RECOVERY_LANDING: "RECOVERY LANDING",
}


@st.cache_resource
def get_bench() -> DroneMotorBench:
    bench = DroneMotorBench(name="drone_bench")
    bench.open()
    bench.start()
    return bench


def render_motor_panel(motor_idx: int, speed_pct: float, current_a: float, stalled: bool, enabled: bool) -> None:
    label = f"Motor {motor_idx}"
    if stalled:
        label += " — STALLED / ISOLATED"
    elif not enabled:
        label += " — OFF"

    st.markdown(f"**{label}**")
    st.progress(int(speed_pct), text=f"{speed_pct:.0f}% speed")
    st.caption(f"Current: {current_a:.2f} A")


def render_event_log(events: list) -> None:
    if not events:
        return
    st.subheader("Event log")
    for event in reversed(events):
        ts = time.strftime("%H:%M:%S", time.localtime(event.timestamp_ns / 1e9))
        st.markdown(
            f"`{ts}` **Motor {event.motor} stall** — peak **{event.peak_current_a:.1f} A**. "
            f"{event.message}"
        )


def main() -> None:
    st.set_page_config(page_title="Drone Motor Bench", page_icon="🛸", layout="wide")

    st.title("Drone Motor Bench")
    st.caption("Preflight power check — stall detection and autonomous recovery landing (simulated)")

    bench = get_bench()
    snap = bench.snapshot()

    col_controls, col_live = st.columns([1, 2])

    with col_controls:
        st.subheader("Controls")

        if snap.mode == FlightMode.OFF:
            if st.button("Power on", type="primary", use_container_width=True):
                bench.power_on()
                st.rerun()
        else:
            if st.button("Power off", use_container_width=True):
                bench.power_off()
                st.rerun()

        if snap.mode == FlightMode.RUNNING:
            st.slider(
                "Throttle (%)",
                min_value=0,
                max_value=100,
                value=int(snap.throttle_pct),
                key="throttle_slider",
                on_change=lambda: get_bench().set_throttle(float(st.session_state.throttle_slider)),
            )
        else:
            st.slider(
                "Throttle (%)",
                min_value=0,
                max_value=100,
                value=int(snap.throttle_pct),
                disabled=True,
            )

        st.divider()
        st.subheader("Fault injection")
        if st.button(
            "Inject stall on Motor 1",
            disabled=snap.mode != FlightMode.RUNNING,
            use_container_width=True,
        ):
            bench.inject_stall(1)
            st.rerun()

        st.caption(
            "Simulates a prop stall on M1. The bench logs the fault, cuts power to that "
            "motor, and ramps the others down in recovery landing mode."
        )

        st.divider()
        st.subheader("Status")
        st.metric("Flight mode", MODE_LABELS[snap.mode])
        render_event_log(snap.events)

    with col_live:
        if snap.mode == FlightMode.OFF:
            st.info("Power on the bench to begin the preflight check.")
        else:
            live_motor_panel()


@st.fragment(run_every=0.15)
def live_motor_panel() -> None:
    snap = get_bench().snapshot()

    if snap.mode == FlightMode.RECOVERY_LANDING:
        st.error("Motor stall detected — fault logged, stalled channel isolated, recovery landing active")
    elif snap.mode == FlightMode.RUNNING:
        st.success(f"Systems running — throttle {snap.throttle_pct:.0f}%")

    st.subheader("Motor speeds")

    motor_cols = st.columns(4)
    for col, motor in zip(motor_cols, snap.motors):
        with col:
            render_motor_panel(
                motor.index,
                motor.speed_pct,
                motor.current_a,
                motor.stalled,
                motor.enabled,
            )

    st.bar_chart({f"M{m.index}": m.speed_pct for m in snap.motors}, height=240)


if __name__ == "__main__":
    main()
