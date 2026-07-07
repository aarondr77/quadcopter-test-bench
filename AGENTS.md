# AGENTS.md

## Project overview

Single-process **Drone Motor Bench** demo: a Streamlit UI (`streamlit_app.py`) over an in-process `BenchSupervisor` that uses Nominal **Instro** `InstroDAQ` with a simulated LabJack T7 (`simulated_labjack.py`) and quadcopter plant model (`drone_physics.py`).

There is no database, Docker, or separate backend service. Default mode is fully simulated (no physical LabJack hardware).

## Cursor Cloud specific instructions

### Python environment

- Use the project virtualenv at `.venv/` (Python 3.12+).
- Dependencies: `pip install -r requirements.txt` (packages: `instro>=1.1.0`, `streamlit>=1.28.0`).
- If `.venv` is missing and `python3 -m venv .venv` fails with an `ensurepip` error, the base image needs `python3.12-venv` installed once (`sudo apt-get install -y python3.12-venv`).

### Running the app (dev)

```bash
.venv/bin/streamlit run streamlit_app.py --server.port 8501 --server.headless true --server.address 0.0.0.0
```

- Default URL: http://localhost:8501
- The supervisor background thread starts when the user clicks **Power on** in the UI (not at import time).

### Hello-world / E2E smoke test (no browser)

```bash
.venv/bin/python -c "
import time
from bench_supervisor import BenchSupervisor, FlightMode
b = BenchSupervisor(name='test_bench')
b.power_on()
b.set_throttle(80.0)
time.sleep(0.6)
b.inject_stall(1)
time.sleep(1.0)
s = b.snapshot()
assert s.mode.name == 'RECOVERY_LANDING'
assert s.motors[0].stalled
print('OK')
"
```

### Lint / tests / build

- **Lint:** not configured in this repo (no `ruff`, `mypy`, `black`, or CI).
- **Tests:** no `tests/` directory or `pytest` config.
- **Build:** N/A (interpreted Python). “Run” = Streamlit dev server above.

### Optional hardware mode

To use a real LabJack T7, swap `SimulatedLabJackT7` for `LabJackTSeriesDriver` in `bench_supervisor.py` and install `instro[labjack]` plus LabJack LJM on the host. Not required for local/dev demo.
