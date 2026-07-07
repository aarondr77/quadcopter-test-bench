"""Octocopter motor bench demo — stall detection and autonomous recovery landing."""

from __future__ import annotations

import time
from collections import deque

import altair as alt
import pandas as pd
import streamlit as st

from bench_supervisor import BenchSupervisor, FlightMode, RecoveryPhase
from drone_physics import NUM_MOTORS
from octocopter_schematic import MOTOR_COLORS, render_octocopter_schematic

MOTOR_MAX_RPM = 10_000.0
TREND_WINDOW_S = 20.0
TREND_MAX_SAMPLES = 200


def speed_pct_to_rpm(speed_pct: float) -> float:
    return speed_pct / 100.0 * MOTOR_MAX_RPM


@st.cache_resource
def get_bench() -> BenchSupervisor:
    return BenchSupervisor(name="drone_bench")


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        .mission-stepper {
            display: flex;
            gap: 0.35rem;
            flex-wrap: wrap;
            margin: 0.5rem 0 1rem 0;
        }
        .mission-step {
            font-size: 0.78rem;
            font-weight: 600;
            padding: 0.35rem 0.65rem;
            border-radius: 999px;
            border: 1px solid #e2e8f0;
            color: #718096;
            background: #f7fafc;
        }
        .mission-step.active {
            color: #1a365d;
            border-color: #3182ce;
            background: #ebf8ff;
        }
        .mission-step.done {
            color: #276749;
            border-color: #68d391;
            background: #f0fff4;
        }
        .status-metric {
            font-size: 0.85rem;
            color: #4a5568;
            margin-bottom: 0.35rem;
        }
        .status-metric strong {
            color: #2d3748;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def recovery_landing_complete(snap) -> bool:
    if snap.mode != FlightMode.RECOVERY_LANDING:
        return False
    return snap.recovery_phase == RecoveryPhase.COMPLETE


def active_motor_count(motors: list) -> int:
    return sum(1 for m in motors if m.enabled and not m.stalled and m.speed_pct > 0.01)


def mission_step_index(snap) -> int:
    if snap.mode == FlightMode.OFF:
        return 0
    if snap.mode == FlightMode.RUNNING and snap.throttle_pct <= 0:
        return 1
    if snap.mode == FlightMode.RUNNING:
        return 2
    if snap.mode == FlightMode.RECOVERY_LANDING and not recovery_landing_complete(snap):
        return 3
    if recovery_landing_complete(snap):
        return 4
    return 0


def render_mission_stepper(snap) -> None:
    steps = ["Bench off", "Preflight", "Hover check", "Fault response", "Landed"]
    active = mission_step_index(snap)
    pills = []
    for idx, label in enumerate(steps):
        css_class = "mission-step"
        if idx < active:
            css_class += " done"
        elif idx == active:
            css_class += " active"
        pills.append(f'<span class="{css_class}">{label}</span>')
    st.markdown(f'<div class="mission-stepper">{"".join(pills)}</div>', unsafe_allow_html=True)


def render_story_banner(snap) -> None:
    if snap.mode == FlightMode.OFF:
        st.info("Bench powered off — click **Power On** to begin preflight.")
        return

    if snap.mode == FlightMode.RUNNING:
        if snap.throttle_pct <= 0:
            st.info("Preflight ready — increase throttle to spin up all 8 motors.")
        else:
            st.success(f"All 8 motors nominal at {snap.throttle_pct:.0f}% collective.")
        return

    if snap.mode == FlightMode.RECOVERY_LANDING:
        stalled = snap.stalled_motor or "?"
        if recovery_landing_complete(snap):
            st.success("Touchdown — safe landing complete. Use **Start New Preflight** to run again.")
            return
        if snap.recovery_phase == RecoveryPhase.COMPENSATE:
            st.warning(
                f"Motor {stalled} stall — channel isolated, "
                "redistributing thrust asymmetrically across 7 motors."
            )
        elif snap.recovery_phase == RecoveryPhase.DESCEND:
            st.warning("Controlled descent in progress — motors remain powered.")
        elif snap.recovery_phase == RecoveryPhase.TOUCHDOWN:
            st.warning("Touchdown sequence — cutting motor power.")
        else:
            st.warning(f"Motor {stalled} stall — recovery landing in progress.")


def append_rpm_history(snap) -> None:
    if "rpm_history" not in st.session_state:
        st.session_state.rpm_history = deque(maxlen=TREND_MAX_SAMPLES)

    now = time.time()
    row: dict = {"time": now}
    for motor in snap.motors:
        row[f"M{motor.index}"] = speed_pct_to_rpm(motor.speed_pct)
    st.session_state.rpm_history.append(row)

    cutoff = now - TREND_WINDOW_S
    while st.session_state.rpm_history and st.session_state.rpm_history[0]["time"] < cutoff:
        st.session_state.rpm_history.popleft()


def render_rpm_trend_chart(snap) -> None:
    history = st.session_state.get("rpm_history", deque())
    if not history:
        st.caption("RPM trend will appear once telemetry is streaming.")
        return

    df = pd.DataFrame(list(history))
    if df.empty:
        return

    t0 = df["time"].iloc[0]
    df["elapsed_s"] = df["time"] - t0

    motor_cols = [f"M{i}" for i in range(1, NUM_MOTORS + 1)]
    long_df = df.melt(id_vars=["elapsed_s"], value_vars=motor_cols, var_name="Motor", value_name="RPM")
    stalled_label = f"M{snap.stalled_motor}" if snap.stalled_motor else None

    color_range = [MOTOR_COLORS[i] for i in range(1, NUM_MOTORS + 1)]
    motor_domain = motor_cols

    max_rpm = float(long_df["RPM"].max()) if not long_df.empty else 0.0
    if max_rpm > MOTOR_MAX_RPM * 0.8:
        y_max = MOTOR_MAX_RPM
    else:
        y_max = min(MOTOR_MAX_RPM, max(1_000.0, max_rpm * 1.2))

    base = alt.Chart(long_df).encode(
        x=alt.X("elapsed_s:Q", title="Seconds"),
        y=alt.Y("RPM:Q", title="RPM", scale=alt.Scale(domain=[0, y_max], zero=True)),
        color=alt.Color("Motor:N", scale=alt.Scale(domain=motor_domain, range=color_range), legend=alt.Legend(title="Motor")),
        tooltip=["Motor", alt.Tooltip("RPM", format=",.0f"), alt.Tooltip("elapsed_s", format=".1f")],
    )

    lines = base.mark_line(strokeWidth=1.5, opacity=0.85)
    if stalled_label:
        stalled_df = long_df[long_df["Motor"] == stalled_label]
        if not stalled_df.empty:
            stalled_line = (
                alt.Chart(stalled_df)
                .mark_line(strokeWidth=3.5, color="#e53e3e")
                .encode(
                    x="elapsed_s:Q",
                    y=alt.Y("RPM:Q", scale=alt.Scale(domain=[0, y_max], zero=True)),
                )
            )
            chart = (lines + stalled_line).properties(height=280)
        else:
            chart = lines.properties(height=280)
    else:
        chart = lines.properties(height=280)

    st.altair_chart(chart, width="stretch")


def render_motor_detail_table(motors: list) -> None:
    rows = []
    for motor in motors:
        status = "Running"
        if motor.stalled:
            status = "Stalled / isolated"
        elif not motor.enabled:
            status = "Off"
        elif motor.speed_pct <= 0.01:
            status = "Idle"
        rows.append(
            {
                "Motor": f"M{motor.index}",
                "RPM": f"{speed_pct_to_rpm(motor.speed_pct):,.0f}",
                "Current (A)": f"{motor.current_a:.2f}",
                "Status": status,
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def render_event_log(events: list) -> None:
    if not events:
        st.caption("No faults recorded.")
        return
    for event in reversed(events):
        ts = time.strftime("%H:%M:%S", time.localtime(event.timestamp_ns / 1e9))
        st.markdown(
            f"`{ts}` **Motor {event.motor} stall** — peak **{event.peak_current_a:.1f} A**. "
            f"{event.message}"
        )


def render_controls_column(snap, bench: BenchSupervisor) -> None:
    st.markdown("#### Controls")

    if snap.mode == FlightMode.OFF:
        if st.button("Power On", type="primary", key="power_on"):
            bench.power_on()
            st.session_state.pop("throttle_input", None)
            st.session_state.rpm_history = deque(maxlen=TREND_MAX_SAMPLES)
            st.rerun()
    else:
        if st.button("Power Off", key="power_off"):
            bench.power_off()
            st.session_state.pop("throttle_input", None)
            st.session_state.rpm_history = deque(maxlen=TREND_MAX_SAMPLES)
            st.rerun()
        if recovery_landing_complete(snap):
            if st.button("Start New Preflight", type="primary", key="new_preflight"):
                bench.power_off()
                bench.power_on()
                st.session_state.pop("throttle_input", None)
                st.session_state.rpm_history = deque(maxlen=TREND_MAX_SAMPLES)
                st.rerun()

    st.markdown("#### Throttle")
    if snap.mode == FlightMode.RUNNING:
        if "throttle_input" not in st.session_state:
            st.session_state.throttle_input = int(snap.throttle_pct)
        st.number_input(
            "Throttle (%)",
            min_value=0,
            max_value=100,
            key="throttle_input",
            label_visibility="collapsed",
            on_change=lambda: get_bench().set_throttle(float(st.session_state.throttle_input)),
        )
    else:
        st.number_input(
            "Throttle (%)",
            min_value=0,
            max_value=100,
            value=int(snap.throttle_pct),
            disabled=True,
            label_visibility="collapsed",
        )

    st.markdown("#### Fault injection")
    motor_options = list(range(1, NUM_MOTORS + 1))
    if "fault_motor" not in st.session_state:
        st.session_state.fault_motor = 1
    st.selectbox(
        "Motor",
        motor_options,
        format_func=lambda m: f"Motor {m}",
        key="fault_motor",
        label_visibility="collapsed",
    )
    fault_disabled = snap.mode != FlightMode.RUNNING
    if st.button("Inject stall", disabled=fault_disabled, key="inject_stall"):
        bench.inject_stall(st.session_state.fault_motor)
        st.rerun()


def render_status_column(snap) -> None:
    st.markdown("#### Status")
    if snap.mode == FlightMode.OFF:
        st.markdown('<p class="status-metric">Flight mode: <strong>OFF</strong></p>', unsafe_allow_html=True)
    elif snap.mode == FlightMode.RUNNING:
        st.markdown('<p class="status-metric">Flight mode: <strong>RUNNING</strong></p>', unsafe_allow_html=True)
    else:
        st.markdown('<p class="status-metric">Flight mode: <strong>RECOVERY LANDING</strong></p>', unsafe_allow_html=True)

    active = active_motor_count(snap.motors)
    st.markdown(
        f'<p class="status-metric">Active motors: <strong>{active} / {NUM_MOTORS}</strong></p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<p class="status-metric">Collective throttle: <strong>{snap.throttle_pct:.0f}%</strong></p>',
        unsafe_allow_html=True,
    )

    if snap.mode == FlightMode.RECOVERY_LANDING and snap.recovery_phase != RecoveryPhase.NONE:
        phase_label = snap.recovery_phase.value.replace("_", " ").title()
        st.markdown(
            f'<p class="status-metric">Recovery phase: <strong>{phase_label}</strong></p>',
            unsafe_allow_html=True,
        )


@st.fragment
def bench_controls() -> None:
    snap = get_bench().snapshot()
    render_controls_column(snap, get_bench())


@st.fragment(run_every=0.15)
def live_schematic() -> None:
    snap = get_bench().snapshot()
    selected = st.session_state.get("fault_motor")
    st.markdown("#### Octocopter")
    render_octocopter_schematic(
        snap.motors,
        snap.mode,
        recovery_phase=snap.recovery_phase,
        selected_motor=selected,
    )


@st.fragment(run_every=0.15)
def live_telemetry() -> None:
    snap = get_bench().snapshot()

    if snap.mode != FlightMode.OFF:
        append_rpm_history(snap)

    render_mission_stepper(snap)
    render_status_column(snap)
    render_story_banner(snap)

    st.markdown("#### RPM trend")
    render_rpm_trend_chart(snap)
    st.caption(f"Last {TREND_WINDOW_S:.0f}s of tachometer readings — stalled motor highlighted in red.")

    col_a, col_b = st.columns(2)
    with col_a:
        with st.expander("Event log", expanded=bool(snap.events)):
            render_event_log(snap.events)
    with col_b:
        with st.expander("Motor detail", expanded=False):
            render_motor_detail_table(snap.motors)


def main() -> None:
    st.set_page_config(page_title="Octocopter Motor Bench", page_icon="🛸", layout="wide")
    inject_styles()

    st.title("Octocopter Motor Bench")
    st.caption(
        "Preflight power check via InstroDAQ — 8-motor redundant platform, "
        "stall detection, and asymmetric recovery landing "
        "(simulated LabJack T7 + expansion)"
    )

    controls_col, schematic_col = st.columns([2, 3])
    with controls_col:
        bench_controls()
    with schematic_col:
        live_schematic()

    live_telemetry()

    st.divider()
    with st.expander("How this uses instro", expanded=False):
        st.code(
            """from instro.daq import InstroDAQ
from simulated_labjack import SimulatedLabJackT7
# from instro.daq.drivers.labjack import LabJackTSeriesDriver  # real bench

driver = SimulatedLabJackT7(device_id="470010000")
daq = InstroDAQ(name="drone_bench", driver=driver)
daq.open()
daq.configure_analog_channel(Direction.OUTPUT, "DAC0", alias="m1_cmd", range_min=0, range_max=5)
daq.write_analog_value("m1_cmd", 3.5)
measurement = daq.read_analog()  # reads m1_current, m1_tach, ... m8_tach""",
            language="python",
        )


if __name__ == "__main__":
    main()
