"""Top-down octocopter schematic for live bench motor telemetry."""

from __future__ import annotations

import json
import math

import streamlit as st

from bench_supervisor import FlightMode, RecoveryPhase

MOTOR_MAX_RPM = 10_000.0
MAX_REV_PER_SEC = 10.0

MOTOR_COLORS = {
    1: "#1f77b4",
    2: "#ff7f0e",
    3: "#2ca02c",
    4: "#d62728",
    5: "#9467bd",
    6: "#8c564b",
    7: "#e377c2",
    8: "#17becf",
}

MOTOR_SPIN = {1: "CW", 2: "CCW", 3: "CW", 4: "CCW", 5: "CW", 6: "CCW", 7: "CW", 8: "CCW"}

_CENTER = (150, 150)
_RADIUS = 95
MOTOR_POSITIONS = {
    i: (
        round(_CENTER[0] + _RADIUS * math.sin(math.radians((i - 1) * 45)), 1),
        round(_CENTER[1] - _RADIUS * math.cos(math.radians((i - 1) * 45)), 1),
    )
    for i in range(1, 9)
}

_ARMS_SVG = "\n      ".join(
    f'<line class="arm" x1="150" y1="150" x2="{x}" y2="{y}" />'
    f'<circle class="motor-pad" cx="{x}" cy="{y}" r="6" />'
    for x, y in MOTOR_POSITIONS.values()
)

_SCHEMATIC_HTML = f"""
<div class="octo-schematic-root">
  <svg id="octo-svg" viewBox="0 0 300 300" role="img" aria-label="Octocopter top-down schematic">
    <defs>
      <filter id="prop-blur" x="-50%" y="-50%" width="200%" height="200%">
        <feGaussianBlur in="SourceGraphic" stdDeviation="1.2" />
      </filter>
    </defs>
    <rect class="octo-bg" x="0" y="0" width="300" height="300" rx="12" />
    <g id="octo-frame">
      {_ARMS_SVG}
      <rect class="body" x="132" y="132" width="36" height="36" rx="8" />
      <rect class="body-core" x="140" y="140" width="20" height="20" rx="4" />
      <polygon class="nose" points="150,104 143,122 157,122" />
      <line class="nose-stem" x1="150" y1="122" x2="150" y2="132" />
    </g>
    <g id="octo-motors"></g>
  </svg>
</div>
"""

_SCHEMATIC_CSS = """
.octo-schematic-root {
  width: 100%;
  min-height: 320px;
  display: flex;
  align-items: center;
  justify-content: center;
}
#octo-svg {
  width: 100%;
  max-width: 420px;
  aspect-ratio: 1;
  display: block;
}
.octo-bg {
  fill: #ffffff;
  stroke: #e2e8f0;
  stroke-width: 1;
}
.arm {
  stroke: #718096;
  stroke-width: 3;
  stroke-linecap: round;
}
.motor-pad {
  fill: #4a5568;
  stroke: #2d3748;
  stroke-width: 1;
}
.body {
  fill: #4a5568;
  stroke: #2d3748;
  stroke-width: 1.5;
}
.body-core {
  fill: #2d3748;
}
.nose {
  fill: #718096;
  stroke: #4a5568;
  stroke-width: 1;
}
.nose-stem {
  stroke: #718096;
  stroke-width: 2;
  stroke-linecap: round;
}
.motor-label {
  font-family: system-ui, -apple-system, sans-serif;
  font-size: 10px;
  font-weight: 700;
  fill: #2d3748;
  text-anchor: middle;
  dominant-baseline: middle;
  pointer-events: none;
}
.spin-label {
  font-family: system-ui, -apple-system, sans-serif;
  font-size: 8px;
  font-weight: 600;
  fill: #718096;
  text-anchor: middle;
  dominant-baseline: middle;
  pointer-events: none;
}
.rpm-label {
  font-family: system-ui, -apple-system, sans-serif;
  font-size: 8px;
  font-weight: 600;
  fill: #4a5568;
  text-anchor: middle;
  dominant-baseline: middle;
  pointer-events: none;
}
.prop-disc {
  stroke-width: 2;
}
.prop-blades {
  stroke-width: 2;
  stroke-linecap: round;
  opacity: 0.9;
}
.prop-hub {
  pointer-events: none;
}
.prop-blur-disc {
  opacity: 0.35;
  filter: url(#prop-blur);
}
.stall-ring {
  fill: none;
  stroke: #e53e3e;
  stroke-width: 3;
}
.select-ring {
  fill: none;
  stroke: #3182ce;
  stroke-width: 2.5;
  stroke-dasharray: 4 3;
  visibility: hidden;
}
.stall-badge {
  font-family: system-ui, -apple-system, sans-serif;
  font-size: 7px;
  font-weight: 700;
  fill: #ffffff;
  text-anchor: middle;
  dominant-baseline: middle;
  pointer-events: none;
}
.stall-badge-bg {
  fill: #e53e3e;
  stroke: #c53030;
  stroke-width: 1;
}
"""

_SCHEMATIC_JS = f"""
const MOTOR_POSITIONS = {json.dumps({str(k): list(v) for k, v in MOTOR_POSITIONS.items()})};
const MOTOR_COLORS = {json.dumps({str(k): v for k, v in MOTOR_COLORS.items()})};
const MOTOR_SPIN = {json.dumps({str(k): v for k, v in MOTOR_SPIN.items()})};
const MOTOR_MAX_RPM = {MOTOR_MAX_RPM};
const MAX_REV_PER_SEC = {MAX_REV_PER_SEC};
const DEG_PER_SEC_AT_MAX = MAX_REV_PER_SEC * 360.0;
const RECOVERY_COLOR = "#d69e2e";

const instances = new WeakMap();

function formatRpm(rpm) {{
  if (rpm >= 1000) {{
    return `${{(rpm / 1000).toFixed(1)}}k`;
  }}
  return `${{Math.round(rpm)}}`;
}}

function motorStateKey(motor, selectedMotor, recovering) {{
  return `${{motor.index}}:${{motor.rpm}}:${{motor.stalled}}:${{motor.enabled}}:${{selectedMotor}}:${{recovering}}`;
}}

function ensureMotorNodes(svg, motors) {{
  const motorsRoot = svg.querySelector("#octo-motors");
  const existing = new Map();

  motorsRoot.querySelectorAll("g.motor-node").forEach((node) => {{
    existing.set(Number(node.dataset.index), node);
  }});

  motors.forEach((motor) => {{
    if (existing.has(motor.index)) {{
      return;
    }}

    const color = MOTOR_COLORS[String(motor.index)] || "#4a5568";
    const spin = MOTOR_SPIN[String(motor.index)] || "";
    const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
    group.classList.add("motor-node");
    group.dataset.index = String(motor.index);

    group.innerHTML = `
      <g class="prop-spin">
        <circle class="prop-blur-disc" r="16" fill="${{color}}" />
        <line class="prop-blades" x1="-13" y1="-3" x2="13" y2="3" stroke="${{color}}" />
        <line class="prop-blades" x1="-13" y1="3" x2="13" y2="-3" stroke="${{color}}" />
        <line class="prop-blades" x1="-3" y1="-13" x2="3" y2="13" stroke="${{color}}" />
        <line class="prop-blades" x1="3" y1="-13" x2="-3" y2="13" stroke="${{color}}" />
        <circle class="prop-disc" r="10" fill="${{color}}" stroke="#ffffff" stroke-width="2" />
        <circle class="prop-hub" r="3" fill="#2d3748" stroke="#ffffff" stroke-width="1" />
      </g>
      <circle class="select-ring" r="19" />
      <circle class="stall-ring" r="19" visibility="hidden" />
      <g class="stall-badge-group" visibility="hidden">
        <rect class="stall-badge-bg" x="-16" y="-6" width="32" height="12" rx="3" />
        <text class="stall-badge" y="1">STALL</text>
      </g>
      <text class="motor-label" y="28">M${{motor.index}}</text>
      <text class="spin-label" y="38">${{spin}}</text>
      <text class="rpm-label" y="-24">0</text>
    `;

    motorsRoot.appendChild(group);
    existing.set(motor.index, group);
  }});
}}

function applyMotorVisual(node, motor, selectedMotor, recovering) {{
  const baseColor = MOTOR_COLORS[String(motor.index)] || "#4a5568";
  const disc = node.querySelector(".prop-disc");
  const blurDisc = node.querySelector(".prop-blur-disc");
  const blades = node.querySelectorAll(".prop-blades");
  const stallRing = node.querySelector(".stall-ring");
  const stallBadge = node.querySelector(".stall-badge-group");
  const selectRing = node.querySelector(".select-ring");
  const rpmLabel = node.querySelector(".rpm-label");

  const running = motor.enabled && !motor.stalled && motor.rpm > 0.5;
  const idle = !motor.stalled && motor.rpm <= 0.5;
  const stalled = motor.stalled;
  const inRecovery = recovering && !stalled && running;

  let color = baseColor;
  if (stalled) {{
    color = "#fc8181";
  }} else if (inRecovery) {{
    color = RECOVERY_COLOR;
  }}

  node.style.opacity = idle ? "0.45" : "1";
  if (rpmLabel) {{
    rpmLabel.textContent = formatRpm(motor.rpm);
  }}

  disc.setAttribute("fill", color);
  disc.setAttribute("stroke", stalled ? "#e53e3e" : "#ffffff");
  blurDisc.setAttribute("fill", color);
  blurDisc.style.display = running ? "block" : "none";

  blades.forEach((blade) => {{
    blade.setAttribute("stroke", stalled ? "#e53e3e" : color);
  }});

  stallRing.setAttribute("visibility", stalled ? "visible" : "hidden");
  stallBadge.setAttribute("visibility", stalled ? "visible" : "hidden");
  selectRing.setAttribute(
    "visibility",
    selectedMotor === motor.index && !stalled ? "visible" : "hidden",
  );

  node.dataset.running = running ? "1" : "0";
  node.dataset.stalled = stalled ? "1" : "0";
  node.dataset.rpm = String(motor.rpm);
}}

function updateMotors(svg, motors, selectedMotor, recovering) {{
  ensureMotorNodes(svg, motors);
  const motorsRoot = svg.querySelector("#octo-motors");
  const byIndex = new Map(motors.map((motor) => [motor.index, motor]));

  motorsRoot.querySelectorAll("g.motor-node").forEach((node) => {{
    const motor = byIndex.get(Number(node.dataset.index));
    if (!motor) {{
      return;
    }}

    const [cx, cy] = MOTOR_POSITIONS[String(motor.index)];
    node.setAttribute("transform", `translate(${{cx}}, ${{cy}})`);

    const nextKey = motorStateKey(motor, selectedMotor, recovering);
    if (node.dataset.stateKey !== nextKey) {{
      applyMotorVisual(node, motor, selectedMotor, recovering);
      node.dataset.stateKey = nextKey;
    }} else {{
      node.dataset.rpm = String(motor.rpm);
      node.dataset.running = motor.enabled && !motor.stalled && motor.rpm > 0.5 ? "1" : "0";
    }}
  }});
}}

function startAnimationLoop(state) {{
  if (state.rafId !== null) {{
    return;
  }}

  let lastTs = performance.now();

  const tick = (ts) => {{
    const dt = Math.min((ts - lastTs) / 1000.0, 0.05);
    lastTs = ts;

    state.svg.querySelectorAll("g.motor-node").forEach((node) => {{
      const spinGroup = node.querySelector(".prop-spin");
      if (!spinGroup) {{
        return;
      }}

      const running = node.dataset.running === "1";
      const stalled = node.dataset.stalled === "1";
      if (!running || stalled) {{
        return;
      }}

      const rpm = Number(node.dataset.rpm || "0");
      const degPerSec = (rpm / MOTOR_MAX_RPM) * DEG_PER_SEC_AT_MAX;
      state.angles.set(
        node.dataset.index,
        (state.angles.get(node.dataset.index) || 0) + degPerSec * dt,
      );

      const angle = state.angles.get(node.dataset.index) || 0;
      spinGroup.setAttribute("transform", `rotate(${{angle.toFixed(2)}})`);
    }});

    state.rafId = requestAnimationFrame(tick);
  }};

  state.rafId = requestAnimationFrame(tick);
}}

function stopAnimationLoop(state) {{
  if (state.rafId !== null) {{
    cancelAnimationFrame(state.rafId);
    state.rafId = null;
  }}
}}

export default function (component) {{
  const {{ data, parentElement }} = component;
  const root = parentElement.querySelector(".octo-schematic-root");
  const svg = parentElement.querySelector("#octo-svg");
  if (!root || !svg) {{
    return;
  }}

  let state = instances.get(parentElement);
  if (!state) {{
    state = {{
      svg,
      angles: new Map(),
      rafId: null,
    }};
    instances.set(parentElement, state);
    startAnimationLoop(state);
  }}

  const motors = (data && data.motors) || [];
  const selectedMotor = data && data.selectedMotor ? Number(data.selectedMotor) : null;
  const recovering = Boolean(data && data.recovering);

  updateMotors(svg, motors, selectedMotor, recovering);

  return () => {{
    stopAnimationLoop(state);
    instances.delete(parentElement);
  }};
}}
"""

_OCTOCOPTER_COMPONENT = st.components.v2.component(
    "octocopter_schematic",
    html=_SCHEMATIC_HTML,
    css=_SCHEMATIC_CSS,
    js=_SCHEMATIC_JS,
    isolate_styles=True,
)


def _speed_pct_to_rpm(speed_pct: float) -> float:
    return speed_pct / 100.0 * MOTOR_MAX_RPM


def _motor_payload(motors: list) -> list[dict]:
    return [
        {
            "index": motor.index,
            "rpm": _speed_pct_to_rpm(motor.speed_pct),
            "stalled": motor.stalled,
            "enabled": motor.enabled,
        }
        for motor in motors
    ]


def render_octocopter_schematic(
    motors: list,
    mode: FlightMode,
    recovery_phase: RecoveryPhase | None = None,
    selected_motor: int | None = None,
) -> None:
    """Render top-down 8-motor octocopter schematic from motor snapshots."""
    recovering = mode == FlightMode.RECOVERY_LANDING and recovery_phase in (
        RecoveryPhase.APPLYING,
        RecoveryPhase.DESCEND,
        RecoveryPhase.TOUCHDOWN,
    )
    payload = {
        "mode": mode.value,
        "recoveryPhase": recovery_phase.value if recovery_phase else RecoveryPhase.NONE.value,
        "recovering": recovering,
        "selectedMotor": selected_motor,
        "motors": _motor_payload(motors),
    }
    _OCTOCOPTER_COMPONENT(
        data=payload,
        key="octocopter_schematic",
        height=380,
    )
