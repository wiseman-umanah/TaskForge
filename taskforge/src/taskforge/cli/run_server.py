"""TaskForge v2 server entry point — thin wrapper around app.py.

The canonical way to start the server is now::

    uvicorn taskforge.coordinator.app:app --port 8400

This module exists for backwards compatibility and for ``uv run``::

    uv run python -m taskforge.cli.run_server

Both methods perform identical startup (HCS topic, agent registration,
first task).  See :mod:`taskforge.coordinator.app` for the full boot
sequence.
"""
from __future__ import annotations

import uvicorn

# Importing app triggers the full boot sequence (HCS topic, pre-registration,
# first task).  All side-effects run exactly once at import time.
from taskforge.coordinator.app import app  # noqa: F401


def main() -> None:
    """Start uvicorn programmatically (used by ``uv run python -m``)."""
    # Pass the already-bootstrapped app object, not an import string.
    # Passing a string causes uvicorn to re-import the module, which would
    # call _bootstrap() a second time and create a duplicate HCS topic.
    uvicorn.run(app, host="0.0.0.0", port=8400, log_level="warning")


if __name__ == "__main__":
    main()
