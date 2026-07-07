"""Drone motor bench demo — stall detection and autonomous recovery landing."""

from __future__ import annotations

import time

import altair as alt
import pandas as pd
import streamlit as st

from bench_supervisor import BenchSupervisor, FlightMode
from quadcopter_schematic import render_quadcopter_schematic

MOTOR_MAX_RPM = 10_000.0

MOTOR_COLORS = {
    1: "#1f77b4",
    2: "#ff7f0e",
    3: "#2ca02c",
    4: "#d62728",
}


def speed_pct_to_rpm(speed_pct: float) -> float:
    return speed_pct / 100.0 * MOTOR_MAX_RPM


@st.cache_resource
def get_bench() -> BenchSupervisor:
    return BenchSupervisor(name="drone_bench")


def inject_control_styles() -> None:
    st.markdown(
        """
        <style>
        .control-card-title {
            font-size: 0.95rem;
            font-weight: 600;
            color: #2d3748;
            text-align: center;
            margin-bottom: 0.75rem;
        }
        div[data-testid="stHorizontalBlock"] div[data-testid="stVerticalBlockBorderWrapper"] {
            background: #ffffff;
            min-height: 9rem;
        }
        div[data-testid="stHorizontalBlock"] div[data-testid="column"] .stButton > button {
            min-height: 3.25rem;
            font-size: 1.05rem;
            font-weight: 600;
            border-radius: 10px;
        }
        div[data-testid="stHorizontalBlock"] div[data-testid="column"] .stNumberInput input {
            min-height: 3.25rem;
            font-size: 1.05rem;
            font-weight: 600;
            border-radius: 10px;
        }
        div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(3)
        div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stButton"] > button {
            aspect-ratio: 1;
            min-height: 4rem;
            padding: 0.5rem;
        }
        .motor-metric-card {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            padding: 0.85rem 1rem;
            margin-bottom: 1.25rem;
        }
        .motor-metric-title {
            font-size: 0.85rem;
            font-weight: 600;
            color: #4a5568;
            margin-bottom: 0.15rem;
        }
        .motor-metric-rpm {
            font-size: 1.5rem;
            font-weight: 700;
            line-height: 1.2;
        }
        .motor-metric-sub {
            font-size: 0.8rem;
            color: #718096;
            margin-top: 0.25rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def recovery_landing_complete(motors: list) -> bool:
    return all(m.speed_pct <= 0.01 for m in motors if not m.stalled)


def render_motor_rpm_chart(motors: list) -> None:
    motor_labels = [f"M{m.index}" for m in motors]
    rpm_values = [speed_pct_to_rpm(m.speed_pct) for m in motors]
    max_rpm = max(rpm_values, default=0.0)
    if max_rpm > MOTOR_MAX_RPM * 0.8:
        y_max = MOTOR_MAX_RPM
    else:
        y_max = min(MOTOR_MAX_RPM, max(1_000.0, max_rpm * 1.2))

    chart_df = pd.DataFrame({"Motor": motor_labels, "RPM": rpm_values})
    chart = (
        alt.Chart(chart_df)
        .mark_bar()
        .encode(
            x=alt.X("Motor:N", sort=motor_labels, title=None),
            y=alt.Y("RPM:Q", scale=alt.Scale(domain=[0, y_max]), title="RPM"),
            color=alt.Color(
                "Motor:N",
                scale=alt.Scale(domain=motor_labels, range=[MOTOR_COLORS[m.index] for m in motors]),
                legend=None,
            ),
            tooltip=["Motor", alt.Tooltip("RPM", format=",.0f")],
        )
        .properties(height=320)
    )
    st.altair_chart(chart, width="stretch")


def render_event_log(events: list) -> None:
    if not events:
        return
    for event in reversed(events):
        ts = time.strftime("%H:%M:%S", time.localtime(event.timestamp_ns / 1e9))
        st.markdown(
            f"`{ts}` **Motor {event.motor} stall** — peak **{event.peak_current_a:.1f} A**. "
            f"{event.message}"
        )


def render_motor_metric(motor_idx: int, speed_rpm: float, current_a: float, stalled: bool, enabled: bool) -> None:
    color = MOTOR_COLORS.get(motor_idx, "#4a5568")
    status = "Running"
    if stalled:
        status = "Stalled / isolated"
    elif not enabled:
        status = "Off"

    st.markdown(
        f"""
        <div class="motor-metric-card">
            <div class="motor-metric-title">Motor {motor_idx}</div>
            <div class="motor-metric-rpm" style="color: {color};">{speed_rpm:,.0f} RPM</div>
            <div class="motor-metric-sub">{status} · {current_a:.2f} A</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_controls(snap, bench: BenchSupervisor) -> None:
    st.markdown("### Controls")

    power_col, throttle_col, fault_col = st.columns(3)

    with power_col:
        with st.container(border=True):
            st.markdown('<div class="control-card-title">Power On / Off</div>', unsafe_allow_html=True)
            if snap.mode == FlightMode.OFF:
                if st.button("Power On", type="primary", use_container_width=True, key="power_on"):
                    bench.power_on()
                    st.session_state.pop("throttle_input", None)
                    st.rerun()
            else:
                if st.button("Power Off", use_container_width=True, key="power_off"):
                    bench.power_off()
                    st.session_state.pop("throttle_input", None)
                    st.rerun()
                if snap.mode == FlightMode.RECOVERY_LANDING and recovery_landing_complete(snap.motors):
                    if st.button("Start New Preflight", type="primary", use_container_width=True, key="new_preflight"):
                        bench.power_off()
                        bench.power_on()
                        st.session_state.pop("throttle_input", None)
                        st.rerun()

    with throttle_col:
        with st.container(border=True):
            st.markdown('<div class="control-card-title">Motor Throttle %</div>', unsafe_allow_html=True)
            if snap.mode == FlightMode.RUNNING:
                if "throttle_input" not in st.session_state:
                    st.session_state.throttle_input = int(snap.throttle_pct)
                st.number_input(
                    "Throttle",
                    min_value=0,
                    max_value=100,
                    key="throttle_input",
                    label_visibility="collapsed",
                    on_change=lambda: get_bench().set_throttle(float(st.session_state.throttle_input)),
                )
            else:
                st.number_input(
                    "Throttle",
                    min_value=0,
                    max_value=100,
                    value=int(snap.throttle_pct),
                    disabled=True,
                    label_visibility="collapsed",
                )

    with fault_col:
        with st.container(border=True):
            st.markdown('<div class="control-card-title">Fault Injection</div>', unsafe_allow_html=True)
            fault_disabled = snap.mode != FlightMode.RUNNING
            top_row = st.columns(2)
            bottom_row = st.columns(2)
            for col, motor_idx in zip(top_row, (1, 2)):
                with col:
                    if st.button(
                        f"M{motor_idx}",
                        disabled=fault_disabled,
                        use_container_width=True,
                        key=f"inject_stall_m{motor_idx}",
                    ):
                        bench.inject_stall(motor_idx)
                        st.rerun()
            for col, motor_idx in zip(bottom_row, (3, 4)):
                with col:
                    if st.button(
                        f"M{motor_idx}",
                        disabled=fault_disabled,
                        use_container_width=True,
                        key=f"inject_stall_m{motor_idx}",
                    ):
                        bench.inject_stall(motor_idx)
                        st.rerun()


def render_motor_data(snap) -> None:
    if snap.mode == FlightMode.OFF:
        st.info("Power on the bench to stream motor speed data.")
        return

    if snap.mode == FlightMode.RECOVERY_LANDING:
        if recovery_landing_complete(snap.motors):
            st.success(
                "Safe landing complete — all motors stopped. "
                "Use **Start New Preflight** to run another test."
            )
        else:
            st.warning(
                "Motor stall detected — fault logged, stalled channel isolated, "
                "recovery landing in progress (ramping motors to zero)"
            )
    elif snap.mode == FlightMode.RUNNING:
        if snap.throttle_pct <= 0:
            st.info("Increase throttle to spin the motors.")
        else:
            st.success(f"Systems running — throttle {snap.throttle_pct:.0f}%")

    chart_col, schematic_col = st.columns([3, 2])
    with chart_col:
        render_motor_rpm_chart(snap.motors)
    with schematic_col:
        render_quadcopter_schematic(snap.motors, snap.mode)

    max_rpm = max((speed_pct_to_rpm(m.speed_pct) for m in snap.motors), default=0.0)
    if max_rpm > MOTOR_MAX_RPM * 0.8:
        scale_note = f"0 to {MOTOR_MAX_RPM:,.0f} RPM full scale"
    else:
        scale_note = f"Auto-scaled for visibility (full scale 0 to {MOTOR_MAX_RPM:,.0f} RPM)"
    st.caption(f"Live tachometer readings — {scale_note}")

    metric_cols = st.columns(4)
    for col, motor in zip(metric_cols, snap.motors):
        with col:
            render_motor_metric(
                motor.index,
                speed_pct_to_rpm(motor.speed_pct),
                motor.current_a,
                motor.stalled,
                motor.enabled,
            )


@st.fragment
def bench_controls() -> None:
    """Interactive controls — isolated so they never trigger full-app reruns."""
    bench = get_bench()
    snap = bench.snapshot()
    render_controls(snap, bench)

    if snap.events:
        with st.expander("Event log", expanded=False):
            render_event_log(snap.events)


@st.fragment(run_every=0.15)
def live_motor_data() -> None:
    """Read-only telemetry with auto-refresh (no widgets — safe with run_every)."""
    snap = get_bench().snapshot()
    render_motor_data(snap)


def main() -> None:
    st.set_page_config(page_title="Drone Motor Bench", page_icon="🛸", layout="wide")
    inject_control_styles()

    st.title("Drone Motor Bench")
    st.caption(
        "Preflight power check via InstroDAQ — stall detection and autonomous recovery landing "
        "(simulated LabJack T7 + LJTick-DAC)"
    )

    bench_controls()

    st.divider()
    st.subheader("Motor data")
    live_motor_data()

    st.divider()
    st.subheader("Nominal Instro")
    with st.expander("How this uses instro"):
        st.code(
            """from instro.daq import InstroDAQ
from simulated_labjack import SimulatedLabJackT7
# from instro.daq.drivers.labjack import LabJackTSeriesDriver  # real bench

driver = SimulatedLabJackT7(device_id="470010000")
# driver = LabJackTSeriesDriver(device_id="470010000")  # swap for hardware

daq = InstroDAQ(name="drone_bench", driver=driver)
daq.open()
daq.configure_analog_channel(Direction.OUTPUT, "DAC0", alias="m1_cmd", range_min=0, range_max=5)
daq.write_analog_value("m1_cmd", 3.5)
measurement = daq.read_analog()  # reads m1_current, m1_tach, ...""",
            language="python",
        )


if __name__ == "__main__":
    main()
