"""Drone motor bench demo — stall detection and autonomous recovery landing."""

from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from bench_supervisor import BenchSnapshot, BenchSupervisor, FlightMode

MOTOR_MAX_RPM = 10_000.0
SPEED_HISTORY_LIMIT = 300

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
        .motor-metric-card {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            padding: 0.85rem 1rem;
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


def reset_speed_history() -> None:
    st.session_state.speed_history = []
    st.session_state.session_start = time.time()


def append_speed_sample(snap: BenchSnapshot) -> None:
    if "speed_history" not in st.session_state:
        reset_speed_history()

    elapsed = time.time() - st.session_state.session_start
    point: dict[str, float] = {"Time (s)": round(elapsed, 2)}
    for motor in snap.motors:
        point[f"M{motor.index}"] = speed_pct_to_rpm(motor.speed_pct)

    st.session_state.speed_history.append(point)
    if len(st.session_state.speed_history) > SPEED_HISTORY_LIMIT:
        st.session_state.speed_history = st.session_state.speed_history[-SPEED_HISTORY_LIMIT:]


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


def render_controls(snap: BenchSnapshot, bench: BenchSupervisor) -> None:
    st.markdown("### Controls")

    power_col, throttle_col, fault_col = st.columns(3)

    with power_col:
        with st.container(border=True):
            st.markdown('<div class="control-card-title">Power On / Off</div>', unsafe_allow_html=True)
            if snap.mode == FlightMode.OFF:
                if st.button("Power On", type="primary", use_container_width=True, key="power_on"):
                    reset_speed_history()
                    bench.power_on()
                    st.rerun()
            else:
                if st.button("Power Off", use_container_width=True, key="power_off"):
                    reset_speed_history()
                    bench.power_off()
                    st.rerun()

    with throttle_col:
        with st.container(border=True):
            st.markdown('<div class="control-card-title">Motor Throttle %</div>', unsafe_allow_html=True)
            if snap.mode == FlightMode.RUNNING:
                st.number_input(
                    "Throttle",
                    min_value=0,
                    max_value=100,
                    value=int(snap.throttle_pct),
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
            if st.button(
                "Inject Stall on Motor 1",
                disabled=snap.mode != FlightMode.RUNNING,
                use_container_width=True,
                key="inject_stall",
            ):
                bench.inject_stall(1)
                st.rerun()


@st.fragment(run_every=0.15)
def live_motor_data() -> None:
    snap = get_bench().snapshot()

    if snap.mode == FlightMode.OFF:
        st.info("Power on the bench to stream motor speed data.")
        return

    append_speed_sample(snap)

    st.subheader("Motor speed (RPM)")
    history_df = pd.DataFrame(st.session_state.speed_history).set_index("Time (s)")
    st.line_chart(history_df, height=380, use_container_width=True)

    st.caption(f"Live tachometer readings — 0 to {MOTOR_MAX_RPM:,.0f} RPM full scale")

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


def main() -> None:
    st.set_page_config(page_title="Drone Motor Bench", page_icon="🛸", layout="wide")
    inject_control_styles()

    st.title("Drone Motor Bench")
    st.caption(
        "Preflight power check via InstroDAQ — stall detection and autonomous recovery landing "
        "(simulated LabJack T7 + LJTick-DAC)"
    )

    bench = get_bench()
    snap = bench.snapshot()

    render_controls(snap, bench)

    st.divider()
    st.subheader("Motor data")
    live_motor_data()

    if snap.events:
        with st.expander("Event log", expanded=False):
            render_event_log(snap.events)

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
