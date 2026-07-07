"""Top-down quadcopter schematic for live bench motor telemetry."""

from __future__ import annotations

import json

import streamlit as st

from bench_supervisor import FlightMode

MOTOR_MAX_RPM = 10_000.0
MAX_REV_PER_SEC = 10.0

MOTOR_COLORS = {
    1: "#1f77b4",
    2: "#ff7f0e",
    3: "#2ca02c",
    4: "#d62728",
}

# Motor arm endpoints in SVG coordinates (viewBox 0 0 300 300).
MOTOR_POSITIONS = {
    1: (78, 78),    # front-left
    2: (78, 222),   # rear-left
    3: (222, 222),  # rear-right
    4: (222, 78),   # front-right
}

_SCHEMATIC_HTML = """
<div class="quad-schematic-root">
  <svg id="quad-svg" viewBox="0 0 300 300" role="img" aria-label="Quadcopter top-down schematic">
    <defs>
      <filter id="prop-blur" x="-50%" y="-50%" width="200%" height="200%">
        <feGaussianBlur in="SourceGraphic" stdDeviation="1.2" />
      </filter>
    </defs>
    <rect class="quad-bg" x="0" y="0" width="300" height="300" rx="12" />
    <g id="quad-stand">
      <rect class="stand-base" x="108" y="254" width="84" height="7" rx="2" />
      <rect class="stand-post" x="147" y="174" width="6" height="82" rx="2" />
    </g>
    <g id="quad-frame">
      <line class="arm" x1="150" y1="150" x2="78" y2="78" />
      <line class="arm" x1="150" y1="150" x2="78" y2="222" />
      <line class="arm" x1="150" y1="150" x2="222" y2="222" />
      <line class="arm" x1="150" y1="150" x2="222" y2="78" />
      <circle class="motor-pad" cx="78" cy="78" r="7" />
      <circle class="motor-pad" cx="78" cy="222" r="7" />
      <circle class="motor-pad" cx="222" cy="222" r="7" />
      <circle class="motor-pad" cx="222" cy="78" r="7" />
      <rect class="body" x="132" y="132" width="36" height="36" rx="8" />
      <rect class="body-core" x="140" y="140" width="20" height="20" rx="4" />
      <rect class="stand-clamp" x="136" y="164" width="28" height="10" rx="2" />
      <polygon class="nose" points="150,104 143,122 157,122" />
      <line class="nose-stem" x1="150" y1="122" x2="150" y2="132" />
    </g>
    <g id="quad-motors"></g>
  </svg>
</div>
"""

_SCHEMATIC_CSS = """
.quad-schematic-root {
  width: 100%;
  min-height: 280px;
  display: flex;
  align-items: center;
  justify-content: center;
}
#quad-svg {
  width: 100%;
  max-width: 320px;
  aspect-ratio: 1;
  display: block;
}
.quad-bg {
  fill: #ffffff;
  stroke: #e2e8f0;
  stroke-width: 1;
}
.stand-base {
  fill: #e2e8f0;
  stroke: #cbd5e0;
  stroke-width: 1;
}
.stand-post {
  fill: #cbd5e0;
  stroke: #a0aec0;
  stroke-width: 1;
}
.stand-clamp {
  fill: #a0aec0;
}
.arm {
  stroke: #718096;
  stroke-width: 4;
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
  font-size: 11px;
  font-weight: 700;
  fill: #2d3748;
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
.stall-badge {
  font-family: system-ui, -apple-system, sans-serif;
  font-size: 8px;
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
const MOTOR_MAX_RPM = {MOTOR_MAX_RPM};
const MAX_REV_PER_SEC = {MAX_REV_PER_SEC};
const DEG_PER_SEC_AT_MAX = MAX_REV_PER_SEC * 360.0;

const instances = new WeakMap();

function motorStateKey(motor) {{
  return `${{motor.index}}:${{motor.rpm}}:${{motor.stalled}}:${{motor.enabled}}`;
}}

function ensureMotorNodes(svg, motors) {{
  const motorsRoot = svg.querySelector("#quad-motors");
  const existing = new Map();

  motorsRoot.querySelectorAll("g.motor-node").forEach((node) => {{
    existing.set(Number(node.dataset.index), node);
  }});

  motors.forEach((motor) => {{
    if (existing.has(motor.index)) {{
      return;
    }}

    const [cx, cy] = MOTOR_POSITIONS[String(motor.index)];
    const color = MOTOR_COLORS[String(motor.index)] || "#4a5568";
    const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
    group.classList.add("motor-node");
    group.dataset.index = String(motor.index);
    group.setAttribute("transform", `translate(${{cx}}, ${{cy}})`);

    group.innerHTML = `
      <g class="prop-spin">
        <circle class="prop-blur-disc" r="18" fill="${{color}}" />
        <line class="prop-blades" x1="-15" y1="-4" x2="15" y2="4" stroke="${{color}}" />
        <line class="prop-blades" x1="-15" y1="4" x2="15" y2="-4" stroke="${{color}}" />
        <line class="prop-blades" x1="-4" y1="-15" x2="4" y2="15" stroke="${{color}}" />
        <line class="prop-blades" x1="4" y1="-15" x2="-4" y2="15" stroke="${{color}}" />
        <circle class="prop-disc" r="12" fill="${{color}}" stroke="#ffffff" stroke-width="2" />
        <circle class="prop-hub" r="4" fill="#2d3748" stroke="#ffffff" stroke-width="1" />
      </g>
      <circle class="stall-ring" r="21" visibility="hidden" />
      <g class="stall-badge-group" visibility="hidden">
        <rect class="stall-badge-bg" x="-18" y="-7" width="36" height="14" rx="4" />
        <text class="stall-badge" y="1">STALLED</text>
      </g>
      <text class="motor-label" y="32">M${{motor.index}}</text>
    `;

    motorsRoot.appendChild(group);
    existing.set(motor.index, group);
  }});
}}

function applyMotorVisual(node, motor) {{
  const color = MOTOR_COLORS[String(motor.index)] || "#4a5568";
  const spinGroup = node.querySelector(".prop-spin");
  const disc = node.querySelector(".prop-disc");
  const blurDisc = node.querySelector(".prop-blur-disc");
  const blades = node.querySelectorAll(".prop-blades");
  const stallRing = node.querySelector(".stall-ring");
  const stallBadge = node.querySelector(".stall-badge-group");

  const running = motor.enabled && !motor.stalled && motor.rpm > 0.5;
  const idle = !motor.stalled && motor.rpm <= 0.5;
  const stalled = motor.stalled;

  node.style.opacity = idle ? "0.5" : "1";

  disc.setAttribute("fill", stalled ? "#fc8181" : color);
  disc.setAttribute("stroke", stalled ? "#e53e3e" : "#ffffff");
  blurDisc.setAttribute("fill", stalled ? "#fc8181" : color);
  blurDisc.style.display = running ? "block" : "none";

  blades.forEach((blade) => {{
    blade.setAttribute("stroke", stalled ? "#e53e3e" : color);
    blade.style.display = running ? "block" : "block";
  }});

  stallRing.setAttribute("visibility", stalled ? "visible" : "hidden");
  stallBadge.setAttribute("visibility", stalled ? "visible" : "hidden");

  node.dataset.running = running ? "1" : "0";
  node.dataset.stalled = stalled ? "1" : "0";
  node.dataset.rpm = String(motor.rpm);
}}

function updateMotors(svg, motors) {{
  ensureMotorNodes(svg, motors);
  const motorsRoot = svg.querySelector("#quad-motors");

  const byIndex = new Map(motors.map((motor) => [motor.index, motor]));
  motorsRoot.querySelectorAll("g.motor-node").forEach((node) => {{
    const motor = byIndex.get(Number(node.dataset.index));
    if (!motor) {{
      return;
    }}
    const nextKey = motorStateKey(motor);
    if (node.dataset.stateKey !== nextKey) {{
      applyMotorVisual(node, motor);
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
  const root = parentElement.querySelector(".quad-schematic-root");
  const svg = parentElement.querySelector("#quad-svg");
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
  updateMotors(svg, motors);

  return () => {{
    stopAnimationLoop(state);
    instances.delete(parentElement);
  }};
}}
"""

_QUADCOPTER_COMPONENT = st.components.v2.component(
    "quadcopter_schematic",
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


def render_quadcopter_schematic(motors: list, mode: FlightMode) -> None:
    """Render top-down 2D quad schematic from motor snapshots."""
    payload = {
        "mode": mode.value,
        "motors": _motor_payload(motors),
    }
    _QUADCOPTER_COMPONENT(
        data=payload,
        key="quadcopter_schematic",
        height=320,
    )
