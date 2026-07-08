# instro-streamlit

A Streamlit demo that shows how you'd use [instro](https://github.com/nominal-io/instro) to run a real hardware test bench.

Here, we simulate a LabJack T7 with expansion boards so you can run the demo without any actual hardware.

The scenario: you're doing a **preflight power check** on a **redundant octocopter**. Eight motors are wired through InstroDAQ. You throttle up, watch current and speed on each motor, and if one prop stalls the system automatically logs the fault, cuts power to that motor, asymmetrically redistributes thrust across the remaining seven, and executes a controlled recovery landing.

## Why this exists

If you're evaluating instro or trying to explain it to someone, reading API docs only gets you so far. This app is meant to show what instro code actually looks like in a test workflow: configure channels, write command voltages, read measurements, react to faults.

## How it's put together

```
streamlit_app.py       ← the UI (mission console)
octocopter_schematic.py← live 8-motor SVG schematic (st.components.v2)
bench_supervisor.py    ← test logic (stall detection, asymmetric recovery landing)
InstroDAQ              ← real instro, unmodified
simulated_labjack.py   ← fake LabJack driver (the one thing you swap out)
drone_physics.py       ← the "physical world" behind the driver
```

**`streamlit_app.py`** is the mission console: power on, throttle, inject stall on any of 8 motors, watch the octocopter schematic and RPM trend chart.

**`bench_supervisor.py`** is where the actual test logic lives. It runs a 10 Hz loop that calls instro for real:
- `configure_analog_channel()` to set up the wiring
- `write_analog_value()` to send motor command voltages
- `read_analog()` to read current and tachometer channels
- Stall detection happens here — when a current reading crosses the threshold, it isolates the motor and enters a 3-phase recovery landing (compensate → descend → touchdown)

**`simulated_labjack.py`** implements instro's `DAQDriverBase` interface. Same shape as the real `LabJackTSeriesDriver`, but instead of talking to a USB device it feeds voltages into a physics model and returns simulated sensor readings.

**`drone_physics.py`** is plain Python that models eight motors. Given a command voltage it figures out motor speed and current. When you inject a stall it makes one motor's current spike — the supervisor only finds out through the normal `read_analog()` path, which is how it would work on a real bench.

## The wiring (simulated T7 + expansion)

| Channel | Alias | What it is |
|---------|-------|------------|
| `DAC0`, `DAC1` | `m1_cmd`, `m2_cmd` | Motor command out (0–5 V) |
| `TDAC0`–`TDAC5` | `m3_cmd`–`m8_cmd` | Motor command out via 3× LJTick-DAC |
| `AIN0`–`AIN7` | `m1_current`–`m8_current` | Current sense (shunt amp → instro scaler → amps) |
| `AIN8`–`AIN15` | `m1_tach`–`m8_tach` | Tachometer via analog mux (voltage → instro scaler → speed %) |

A real T7 only has 2 analog outputs, so we model it with three LJTick-DAC expansion boards plus an analog mux for the 16 sense channels.

## Running it

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Then: **Power on** → set throttle to ~75% → select **Motor 1** → **Inject stall** → watch M1 isolate, remaining motors compensate asymmetrically, then descend over ~8 seconds to touchdown.

## Swapping in real hardware

The whole point of structuring it this way. In `bench_supervisor.py`:

```python
# Demo (what we ship)
from simulated_labjack import SimulatedLabJackT7
driver = SimulatedLabJackT7(device_id="470010000")

# Real bench
from instro.daq.drivers.labjack import LabJackTSeriesDriver
driver = LabJackTSeriesDriver(device_id="470010000")
```

Everything above that line — the supervisor, the instro calls, the Streamlit UI — stays the same.

You'll need `pip install "instro[labjack]"` and the LJM driver installed for the real device.
