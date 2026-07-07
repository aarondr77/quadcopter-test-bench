# instro-streamlit

A Streamlit demo that shows how you'd use [instro](https://github.com/nominal-io/instro) to run a real hardware test bench. 

Here, we simulate a LabJack T7 and a LabJack LJTick-DAC so you can run the demo without any actual hardware.

The scenario: you're doing a **drone preflight power check**. Four motors are wired up to a LabJack T7 (with an LJTick-DAC for extra analog outputs). You throttle up, watch current and speed on each motor, and if one prop stalls the system automatically logs the fault, cuts power to that motor, and ramps the others down in recovery landing mode.

## Why this exists

If you're evaluating instro or trying to explain it to someone, reading API docs only gets you so far. This app is meant to show what instro code actually looks like in a test workflow: configure channels, write command voltages, read measurements, react to faults.

## How it's put together

```
streamlit_app.py       ← the UI
bench_supervisor.py    ← test logic (stall detection, recovery landing)
InstroDAQ              ← real instro, unmodified
simulated_labjack.py   ← fake LabJack driver (the one thing you swap out)
drone_physics.py       ← the "physical world" behind the driver
```

**`streamlit_app.py`** is just buttons and charts. Power on, throttle slider, inject stall, watch motor speeds update.

**`bench_supervisor.py`** is where the actual test logic lives. It runs a 10 Hz loop that calls instro for real:
- `configure_analog_channel()` to set up the wiring
- `write_analog_value()` to send motor command voltages
- `read_analog()` to read current and tachometer channels
- Stall detection happens here — when a current reading crosses the threshold, it isolates the motor and enters recovery mode

**`simulated_labjack.py`** implements instro's `DAQDriverBase` interface. Same shape as the real `LabJackTSeriesDriver`, but instead of talking to a USB device it feeds voltages into a physics model and returns simulated sensor readings.

**`drone_physics.py`** is plain Python that models the physics of the drone. Given a command voltage it figures out motor speed and current. When you hit "inject stall" it makes one motor's current spike — the supervisor only finds out through the normal `read_analog()` path, which is how it would work on a real bench.

## The wiring (simulated T7 + LJTick-DAC)

| Channel | Alias | What it is |
|---------|-------|------------|
| `DAC0`, `DAC1` | `m1_cmd`, `m2_cmd` | Motor command out (0–5 V) |
| `TDAC0`, `TDAC1` | `m3_cmd`, `m4_cmd` | Motor command out via LJTick-DAC |
| `AIN0`–`AIN3` | `m1_current`–`m4_current` | Current sense (shunt amp → instro scaler → amps) |
| `AIN4`–`AIN7` | `m1_tach`–`m4_tach` | Tachometer (voltage → instro scaler → speed %) |

A real T7 only has 2 analog outputs, so we model it with a LJTick-DAC expansion board plugged in. That's a pretty normal setup if you actually need 4 analog outs.

## Running it

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Then: **Power on** → crank the throttle to ~70% → **Inject stall on Motor 1** → watch M1 get isolated and M2–M4 ramp down over about 6 seconds.

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
