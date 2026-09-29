"""Start the packaged backend on loopback with temporary data and check readiness."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve(strict=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    opener = build_opener(ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix="bilibili-radio-smoke-") as data_dir:
        env = dict(os.environ, APP_RUNTIME="desktop", AUTH_MODE="disabled",
                   APP_BIND_HOST="127.0.0.1", APP_BIND_PORT=str(port),
                   APP_DATA_DIR=data_dir, SESSION_COOKIE_SECURE="false")
        process = subprocess.Popen(
            [str(executable)], cwd=data_dir, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + 45
            last_error = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Packaged backend exited early: {process.returncode}")
                try:
                    with opener.open(f"http://127.0.0.1:{port}/health/ready", timeout=2) as response:
                        payload = json.load(response)
                    if payload.get("success") and payload.get("data", {}).get("status") == "ready":
                        break
                except (URLError, TimeoutError, OSError, ValueError) as error:
                    last_error = error
                time.sleep(0.25)
            else:
                raise RuntimeError(f"Packaged backend did not become ready: {last_error}")
            with opener.open(f"http://127.0.0.1:{port}/health/live", timeout=2) as response:
                if not json.load(response).get("success"):
                    raise RuntimeError("Packaged backend liveness failed")
            print("Packaged backend liveness and database readiness: OK")
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    # PyInstaller one-file executables own a child process.
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   check=True, stdout=subprocess.DEVNULL)
                else:
                    process.terminate()
                process.wait(timeout=15)


if __name__ == "__main__":
    main()
