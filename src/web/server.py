"""Run with: `uv run python -m web.server`. Listens on 0.0.0.0:8080
so phones on the same Wi-Fi can reach `http://<your-laptop-ip>:8080`.
"""
from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "web.api:app",
        host="0.0.0.0",
        port=8090,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
