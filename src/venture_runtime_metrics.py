"""Low-cost process metrics for Captain-only Venture diagnostics."""

from __future__ import annotations

import os
import resource
from typing import Any


def _linux_status() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                if key not in {"VmRSS", "VmSwap", "Threads"}:
                    continue
                number = rest.strip().split(" ", 1)[0]
                values[key] = int(number)
    except (FileNotFoundError, OSError, ValueError):
        pass
    return values


def process_pressure_snapshot() -> dict[str, Any]:
    """Return a safe, small process-memory snapshot without retaining history."""
    status = _linux_status()
    max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports ru_maxrss in KiB; macOS reports bytes. /proc values are KiB.
    max_rss_kib = int(max_rss if os.name != "darwin" else max_rss / 1024)
    return {
        "rss_kib": status.get("VmRSS", max_rss_kib),
        "swap_kib": status.get("VmSwap", 0),
        "peak_rss_kib": max_rss_kib,
        "threads": status.get("Threads"),
    }
