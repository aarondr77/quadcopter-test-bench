# AGENTS.md

## Project overview

Single-process **Octocopter Motor Bench** demo: a Streamlit UI (`streamlit_app.py`) over an in-process `BenchSupervisor` that uses Nominal **Instro** `InstroDAQ` with a simulated LabJack T7 (`simulated_labjack.py`) and octocopter plant model (`drone_physics.py`, 8 motors).

There is no database, Docker, or separate backend service. Default mode is fully simulated (no physical LabJack hardware).

## Cursor Cloud specific instructions

### Python environment

- Use the project virtualenv at `.venv/` (Python 3.12+).
- Dependencies: `pip install -r requirements.txt` (packages: `instro>=1.1.0`, `streamlit>=1.28.0`).
- Cloud VMs use `.cursor/Dockerfile` (Python 3.12 + venv). Local dev: if `python3 -m venv .venv` fails with an `ensurepip` error, install `python3.12-venv` once.

### Running the app (dev)

In cloud agent VMs, Streamlit is started automatically via `.cursor/environment.json` → `terminals`. To start manually:

```bash
.venv/bin/streamlit run streamlit_app.py --server.port 8501 --server.headless true --server.address 0.0.0.0
```

- Default URL: http://localhost:8501
- The supervisor background thread starts when the user clicks **Power on** in the UI (not at import time).

### Visual verification (required — include a screen recording)

Every cloud agent run that changes bench UI or behavior must **record a short screen recording** of this demo before finishing. Use computer use / browser tools to walk through the flow; do not mark the task complete without video proof.

**Demo script (record all steps):**

1. Open http://localhost:8501 and wait for the **Octocopter Motor Bench** page to load.
2. Click **Power on** → mission stepper should show **Preflight** / **Hover check**.
3. Set **Throttle (%)** to ~75% → all eight motors should spin on the octocopter schematic.
4. Select **Motor 1** and click **Inject stall**.
5. Confirm recovery state:
   - Banner: stall detected, asymmetric redistribution / controlled descent
   - Motor 1: **STALL** badge, 0 RPM on schematic
   - Recovery phase badge: Compensate → Descend → Touchdown
   - RPM trend: M1 flat at zero; M2–M8 diverge then descend together
   - Event log: Motor 1 stall with peak current (~12 A)
6. Wait for **Landed** stepper state and touchdown success banner.
6. Save the screen recording as an artifact (e.g. `drone-bench-demo`) and include at least one screenshot of the recovery state in the PR/summary.

If you changed only non-UI backend logic, still run this flow and record it so reviewers can see behavior is unchanged (or show what changed).

### One-time dashboard setup (repo owner)

To get a **video on every code change**, configure Cursor outside the repo:

1. **Artifacts in PRs:** [Cloud Agents dashboard](https://cursor.com/dashboard/cloud-agents#my-pull-requests) → enable **Allow posting artifacts to GitHub**.
2. **Automation:** [cursor.com/automations](https://cursor.com/automations) → create automation:
   - **Trigger:** `Pull request pushed` (or `Push to branch` for direct pushes)
   - **Repository:** `quadcopter-test-bench`
   - **Environment:** use the repo’s `.cursor/environment.json` (or a saved environment linked to this repo)
   - **Prompt:** *After implementing changes, run the Visual verification demo script in AGENTS.md, record a screen recording of the full flow, and attach the video to the PR summary. Do not finish until the demo is recorded.*
   - **Computer use:** leave enabled (default)
3. **Optional:** comment `@cursor` on an existing PR with *“Record a demo video per AGENTS.md”* to trigger a one-off walkthrough.

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
