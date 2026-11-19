# Dobot Kit

This repository contains the Python USB library for the classic Magician.
The control panel in the parent directory is an independent project.

- Keep the public API small and teach it from the first program in `README.md`.
- Public APIs use millimetres, degrees and seconds. Document exceptions at the call site.
- Run tests with mocked serial IO. Keep test fixtures out of the installed package.
- Real-port examples are operator-run programs. The operator chooses the explicit port
  and sets up any required target poses or tool.
- Do not replay a write after timeout or reconnect. Track the command acknowledgement,
  controller execution, observed physical result and any measurements independently.
- Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run mypy` and `uv build` for protocol or public API changes.
- Keep vendor PDFs, SDK binaries, local photos and personal device identifiers out of Git.
