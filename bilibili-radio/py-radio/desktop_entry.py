from __future__ import annotations

import os
import atexit


os.environ.setdefault("APP_RUNTIME", "desktop")
os.environ.setdefault("AUTH_MODE", "disabled")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")
os.environ.setdefault("APP_BIND_HOST", "127.0.0.1")
os.environ.setdefault(
    "CORS_ALLOWED_ORIGINS",
    "http://tauri.localhost,https://tauri.localhost,tauri://localhost,http://localhost:3000,http://127.0.0.1:3000",
)

from app import app, enforce_loopback_binding, resolve_bind_host, resolve_bind_port  # noqa: E402
from constant import Server  # noqa: E402
from metadata_maintenance import start_desktop_maintenance  # noqa: E402


def main() -> None:
    bind_host = resolve_bind_host()
    bind_port = resolve_bind_port()
    enforce_loopback_binding(bind_host)
    maintenance = start_desktop_maintenance()
    atexit.register(maintenance.set)
    app.run(
        host=bind_host,
        port=bind_port,
        debug=Server.DEBUG,
        threaded=True,
    )


if __name__ == "__main__":
    main()
