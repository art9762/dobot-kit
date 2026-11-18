# Dobot Kit

This repository contains the Python USB library for the classic Magician.
The control panel in the parent directory is an independent project.

- Read `docs/PLAN.md`, `docs/API.md` and `docs/CAPABILITIES.md` before adding features.
  Use `docs/IMPLEMENTATION.md` for driver contracts.
- Public APIs use millimetres, degrees and seconds. Document exceptions at the call site.
- Run all ordinary tests with mocked serial IO or the private controller double under
  `tests/`. Keep these fixtures out of the installed package and user-facing APIs.
  Tests that exercise examples must inject the connection or mock the serial port.
- Real-port examples are operator-run programs. The operator chooses the explicit port
  and sets up any required target poses or tool. For a separately authorized hardware
  acceptance run, release the port from the panel and follow `docs/HARDWARE.md`.
- Do not replay a write after timeout or reconnect. Track the command acknowledgement,
  controller execution, observed physical result and any measurements independently.
- Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run mypy` and `uv build` for protocol or public API changes.
- Keep vendor PDFs, SDK binaries, local photos and personal device identifiers out of Git.
