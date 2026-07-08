"""Octocopter motor bench demo — stall detection and autonomous recovery landing."""

from __future__ import annotations

import time
from collections import deque

import altair as alt
import pandas as pd
import streamlit as st

from bench_supervisor import (
    BenchSupervisor,
    FlightMode,
    RECOVERY_DURATION_S,
    RECOVERY_PHASES,
    RecoveryPhase,
)
from drone_physics import NUM_MOTORS
from octocopter_schematic import MOTOR_COLORS, render_octocopter_schematic

MOTOR_MAX_RPM = 10_000.0
TREND_WINDOW_S = 120.0
TREND_MAX_SAMPLES = 800


def speed_pct_to_rpm(speed_pct: float) -> float:
    return speed_pct / 100.0 * MOTOR_MAX_RPM


def clear_demo_session() -> None:
    st.session_state.throttle_input = 0
    st.session_state.rpm_history = deque(maxlen=TREND_MAX_SAMPLES)
    st.session_state.pop("demo_frozen", None)
    st.session_state.pop("frozen_snapshot", None)
    st.session_state.pop("recovery_chart_started_at", None)


def get_display_snapshot():
    """Return live snapshot, or freeze everything at landing for review."""
    if st.session_state.get("demo_frozen") and st.session_state.get("frozen_snapshot"):
        return st.session_state.frozen_snapshot

    snap = get_bench().snapshot()
    if (
        snap.mode == FlightMode.RECOVERY_LANDING
        and snap.recovery_phase == RecoveryPhase.COMPLETE
        and not st.session_state.get("demo_frozen")
    ):
        st.session_state.demo_frozen = True
        st.session_state.frozen_snapshot = snap
    return snap


@st.cache_resource
def get_bench() -> BenchSupervisor:
    return BenchSupervisor(name="drone_bench")


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        .recovery-stepper-block {
            margin: 0.75rem 0 1rem 0;
            padding: 0.75rem 0.85rem;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            background: #f8fafc;
        }
        .recovery-stepper-label {
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            color: #718096;
            margin-bottom: 0.5rem;
        }
        .recovery-substepper {
            display: flex;
            gap: 0.3rem;
            flex-wrap: wrap;
        }
        .recovery-step {
            font-size: 0.72rem;
            font-weight: 600;
            padding: 0.3rem 0.55rem;
            border-radius: 999px;
            border: 1px solid #cbd5e0;
            color: #718096;
            background: #ffffff;
        }
        .recovery-step.active {
            color: #9c4221;
            border-color: #ed8936;
            background: #fffaf0;
        }
        .recovery-step.done {
            color: #276749;
            border-color: #68d391;
            background: #f0fff4;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def recovery_landing_complete(snap) -> bool:
    if snap.mode != FlightMode.RECOVERY_LANDING:
        return False
    return snap.recovery_phase == RecoveryPhase.COMPLETE


RECOVERY_STEPS: tuple[tuple[RecoveryPhase, str], ...] = (
    (RecoveryPhase.FAULT_DETECTED, "Error"),
    (RecoveryPhase.CALCULATING, "Calculating"),
    (RecoveryPhase.APPLYING, "Applying"),
    (RecoveryPhase.DESCEND, "Descending"),
    (RecoveryPhase.TOUCHDOWN, "Touchdown"),
    (RecoveryPhase.COMPLETE, "Review"),
)

CHART_PHASE_LABELS = ("Error", "Calc", "Apply", "Descend", "Touchdown")
CHART_PHASE_COLORS = ("#fca5a5", "#cbd5e1", "#fcd34d", "#7dd3fc", "#6ee7b7")
CHART_PHASE_OPACITY = 0.48


def recovery_step_index(snap) -> int:
    for idx, (phase, _) in enumerate(RECOVERY_STEPS):
        if snap.recovery_phase == phase:
            return idx
    return 0


def recovery_overall_progress(snap) -> float:
    if snap.recovery_phase == RecoveryPhase.COMPLETE:
        return 1.0
    if snap.mode != FlightMode.RECOVERY_LANDING:
        return 0.0

    elapsed = 0.0
    for duration, phase in RECOVERY_PHASES:
        if phase == snap.recovery_phase:
            elapsed += duration * snap.recovery_progress
            break
        elapsed += duration
    return min(1.0, elapsed / RECOVERY_DURATION_S)


def in_recovery_story(snap) -> bool:
    return snap.mode == FlightMode.RECOVERY_LANDING or st.session_state.get("demo_frozen", False)


def _record_recovery_chart_start(snap) -> None:
    if snap.mode != FlightMode.RECOVERY_LANDING:
        return
    if st.session_state.get("recovery_chart_started_at") is not None:
        return
    if snap.events:
        st.session_state.recovery_chart_started_at = snap.events[-1].timestamp_ns / 1e9


def _recovery_phase_regions_df(recovery_start_elapsed: float, y_max: float) -> pd.DataFrame:
    rows = []
    cursor = recovery_start_elapsed
    for (duration, _), label in zip(RECOVERY_PHASES, CHART_PHASE_LABELS):
        rows.append(
            {
                "start": cursor,
                "end": cursor + duration,
                "phase": label,
                "y_min": 0.0,
                "y_max": y_max,
            }
        )
        cursor += duration
    return pd.DataFrame(rows)


def render_recovery_stepper(snap) -> None:
    if not in_recovery_story(snap):
        return

    recovery_active = recovery_step_index(snap)
    sub_pills = []
    for idx, (_, label) in enumerate(RECOVERY_STEPS):
        css_class = "recovery-step"
        if idx < recovery_active:
            css_class += " done"
        elif idx == recovery_active:
            css_class += " active"
        sub_pills.append(f'<span class="{css_class}">{label}</span>')

    st.markdown(
        f'<div class="recovery-stepper-block">'
        f'<div class="recovery-stepper-label">Recovery sequence</div>'
        f'<div class="recovery-substepper">{"".join(sub_pills)}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )
    st.progress(recovery_overall_progress(snap), text="Recovery progress")


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
        motor = snap.stalled_motor or "?"
        if recovery_landing_complete(snap):
            st.success(
                "Safe landing complete — timeline paused. "
                "Review the motor cadence chart below, then **Start New Preflight**."
            )
            return
        if snap.recovery_phase == RecoveryPhase.FAULT_DETECTED:
            st.error(f"**Motor {motor} error detected** — channel isolated.")
        elif snap.recovery_phase == RecoveryPhase.CALCULATING:
            st.warning("Calculating safe descent with remaining motors...")
        elif snap.recovery_phase == RecoveryPhase.APPLYING:
            st.warning(f"Applying safe motor cadence to the **7 remaining motors**.")
        elif snap.recovery_phase == RecoveryPhase.DESCEND:
            st.warning("Controlled descent in progress.")
        elif snap.recovery_phase == RecoveryPhase.TOUCHDOWN:
            st.warning("Touchdown. Shutting down motors.")
        else:
            st.warning(f"Motor {motor} error. Recovery landing in progress.")


def append_rpm_history(snap) -> None:
    if st.session_state.get("demo_frozen"):
        return

    if "rpm_history" not in st.session_state:
        st.session_state.rpm_history = deque(maxlen=TREND_MAX_SAMPLES)

    _record_recovery_chart_start(snap)

    now = time.time()
    row: dict = {"time": now}
    for motor in snap.motors:
        row[f"M{motor.index}"] = speed_pct_to_rpm(motor.speed_pct)
    st.session_state.rpm_history.append(row)

    if snap.mode == FlightMode.RECOVERY_LANDING:
        return

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
    if stalled_label:
        color_range[motor_domain.index(stalled_label)] = "#e53e3e"

    max_rpm = float(long_df["RPM"].max()) if not long_df.empty else 0.0
    if max_rpm > MOTOR_MAX_RPM * 0.8:
        y_max = MOTOR_MAX_RPM
    else:
        y_max = min(MOTOR_MAX_RPM, max(1_000.0, max_rpm * 1.2))

    x_max = float(df["elapsed_s"].max())
    recovery_start = st.session_state.get("recovery_chart_started_at")
    if recovery_start is not None:
        recovery_start_elapsed = recovery_start - t0
        regions_df = _recovery_phase_regions_df(recovery_start_elapsed, y_max)
        x_max = max(x_max, float(regions_df["end"].max()))

    line_stroke = 2.25
    line_encode = {
        "x": alt.X("elapsed_s:Q", title="Seconds", scale=alt.Scale(domain=[0, x_max])),
        "y": alt.Y("RPM:Q", title="RPM", scale=alt.Scale(domain=[0, y_max], zero=True)),
        "color": alt.Color(
            "Motor:N",
            scale=alt.Scale(domain=motor_domain, range=color_range),
            legend=alt.Legend(title="Motor"),
        ),
        "tooltip": ["Motor", alt.Tooltip("RPM", format=",.0f"), alt.Tooltip("elapsed_s", format=".1f")],
    }
    if stalled_label:
        line_encode["strokeDash"] = alt.condition(
            alt.datum.Motor == stalled_label,
            alt.value([8, 4]),
            alt.value([]),
        )

    lines = alt.Chart(long_df).encode(**line_encode).mark_line(strokeWidth=line_stroke, opacity=1.0)

    chart_layers = []
    if recovery_start is not None:
        phase_chart = (
            alt.Chart(regions_df)
            .mark_rect(opacity=CHART_PHASE_OPACITY)
            .encode(
                x=alt.X("start:Q", scale=alt.Scale(domain=[0, x_max])),
                x2="end:Q",
                y=alt.Y("y_min:Q", scale=alt.Scale(domain=[0, y_max], zero=True)),
                y2="y_max:Q",
                color=alt.Color(
                    "phase:N",
                    scale=alt.Scale(domain=list(CHART_PHASE_LABELS), range=list(CHART_PHASE_COLORS)),
                    legend=alt.Legend(title="Phase", orient="top", direction="horizontal"),
                ),
                tooltip=[
                    alt.Tooltip("phase:N", title="Phase"),
                    alt.Tooltip("start:Q", title="Start (s)", format=".1f"),
                    alt.Tooltip("end:Q", title="End (s)", format=".1f"),
                ],
            )
        )
        chart_layers.append(phase_chart)

    chart_layers.append(lines)

    chart = (
        alt.layer(*chart_layers)
        .resolve_scale(color="independent", x="shared", y="shared")
        .properties(height=300)
    )
    st.altair_chart(chart, width="stretch")


def render_controls_column(snap, bench: BenchSupervisor) -> None:
    st.markdown("#### Controls")

    if snap.mode == FlightMode.OFF:
        if st.button("Power On", type="primary", key="power_on"):
            bench.power_on()
            clear_demo_session()
            st.rerun()
    else:
        if st.button("Power Off", key="power_off"):
            bench.power_off()
            clear_demo_session()
            st.rerun()
        if recovery_landing_complete(snap):
            if st.button("Start New Preflight", type="primary", key="new_preflight"):
                bench.power_off()
                bench.power_on()
                clear_demo_session()
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
    selected_motor = st.session_state.fault_motor
    if st.button(
        f"Stall Motor {selected_motor}",
        disabled=fault_disabled,
        key="inject_stall",
        use_container_width=True,
    ):
        bench.inject_stall(selected_motor)
        st.rerun()


@st.fragment
def bench_controls() -> None:
    snap = get_display_snapshot()
    render_controls_column(snap, get_bench())


@st.fragment(run_every=0.15)
def live_schematic() -> None:
    if st.session_state.get("demo_frozen"):
        snap = st.session_state.frozen_snapshot
    else:
        snap = get_display_snapshot()
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
    snap = get_display_snapshot()

    if snap.mode != FlightMode.OFF:
        append_rpm_history(snap)

    render_recovery_stepper(snap)
    render_story_banner(snap)

    st.markdown("#### RPM trend")
    render_rpm_trend_chart(snap)
    if st.session_state.get("demo_frozen"):
        st.caption("Timeline paused at touchdown — review how each motor responded during recovery.")
    else:
        st.caption("Live tachometer readings — stalled motor highlighted in red.")


def render_instro_faq_section() -> None:
    st.divider()
    st.markdown("## FAQ")

    with st.container(border=True):
        with st.expander("What is Nominal Instro?"):
            st.markdown(
                "Nominal Instro is a Python library for talking to test-and-measurement instruments "
                "(power supplies, multimeters, electronic loads, DAQs, oscilloscopes, PLCs) from a "
                "unified, typed API."
            )
            st.markdown(
                "Install with `pip install instro` (Python 3.10–3.13). "
                "This demo uses **`InstroDAQ`** with a simulated LabJack T7 driver — "
                "the same class you'd use on a real bench."
            )

        with st.expander("Why is it useful?"):
            st.markdown(
                "Instro gives you one consistent pattern across instrument types: construct, `open()`, "
                "configure, measure, `close()`. Test logic talks to Instro classes (`InstroPSU`, "
                "`InstroDAQ`, `InstroDMM`, …) instead of vendor-specific APIs, so you swap drivers "
                "without rewriting your workflow."
            )
            st.markdown(
                "- **Simulated drivers** — develop and demo without hardware (this app runs entirely in-process)\n"
                "- **Optional extras** — install only the vendor SDKs you need, e.g. `pip install \"instro[labjack]\"`\n"
                "- **Publishers** — stream measurements to a file, a custom destination, or [Nominal](https://nominal.io)\n"
                "- **Typed channels** — aliases, scaling, and structured reads (see `bench_supervisor.py` calling "
                "`configure_analog_channel()`, `write_analog_value()`, and `read_analog()` at 10 Hz)"
            )
            st.markdown(
                "In this demo, stall detection and recovery landing react to real `read_analog()` "
                "measurements — the supervisor never bypasses Instro to peek at the physics model."
            )

        with st.expander("What devices does it support?"):
            st.markdown(
                "Instro ships drivers for common bench equipment. Vendor-specific SDKs are optional "
                "extras; community-contributed drivers live in [`instro-contrib`](https://pypi.org/project/instro-contrib/) "
                "(`pip install \"instro[contrib]\"`)."
            )
            st.dataframe(
                pd.DataFrame(
                    [
                        ("Power supply", "InstroPSU", "B&K Precision (9115, 914X), Keysight (E36100-series), Rigol (DP800-series), Siglent (SPD3303), TDK Lambda (Genesys), simulated"),
                        ("Multimeter", "InstroDMM", "Agilent 34401A, Keithley 2400, Keithley 2750 (unstable)"),
                        ("Electronic load", "InstroELoad", "B&K Precision (85xxB-series)"),
                        ("Oscilloscope", "InstroScope", "Keysight (1200X-series), Tektronix (2-series), Siglent (SDS1000X-E)"),
                        ("DAQ", "InstroDAQ", "Keysight 34980A, NI-DAQmx, LabJack T-series, MCC USB-series"),
                        ("I2C", "I2CInterface", "Total Phase Aardvark"),
                        ("Modbus", "ModbusDevice", "Any Modbus TCP / RTU device"),
                        ("EtherNet/IP", "EtherNetIPDevice", "Allen-Bradley / CompactLogix-class PLCs"),
                    ],
                    columns=["Category", "Class", "Vendors"],
                ),
                column_config={
                    "Category": st.column_config.TextColumn("Category", width="small"),
                    "Class": st.column_config.TextColumn("Class", width="small"),
                    "Vendors": st.column_config.TextColumn("Vendors", width="large"),
                },
                hide_index=True,
            )
            st.caption(
                "This demo uses InstroDAQ + LabJack T-series. Swap `SimulatedLabJackT7` for "
                "`LabJackTSeriesDriver` in `bench_supervisor.py` to run on hardware."
            )


def main() -> None:
    st.set_page_config(
        page_title="Octocopter motor bench | Nominal Instro",
        page_icon=":material/sensors:",
        layout="wide",
    )
    inject_styles()

    st.markdown("# :material/sensors: Octocopter motor failure testbench - Powered by Nominal Instro")
    st.markdown("""This app is designed to test the recovery logic for our octocopter. Start the motors, choose one to inject a fault into, and watch the system safely execute a simulated landing sequence using the remaining working motors.
    """)

    controls_col, schematic_col = st.columns([2, 3])
    with controls_col:
        bench_controls()
    with schematic_col:
        live_schematic()

    live_telemetry()

    render_instro_faq_section()


if __name__ == "__main__":
    main()
