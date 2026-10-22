#!/usr/bin/python3
"""Collect host-wide resource samples and store them in PostgreSQL."""

from __future__ import annotations

import base64
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any


DATABASE = "system_resources_monitoring"
ROLE = "system_resources_monitoring"
PG_SOCKET = "/var/run/postgresql"
PSQL = "/usr/lib/postgresql/16/bin/psql"
SAMPLE_INTERVAL_SECONDS = 60
STOP = threading.Event()

# Values must remain above the warning threshold for this many samples before
# an alert is logged. Recovery uses a five-point margin to avoid flapping.
ALERT_LIMITS = {
    "cpu_percent": (90.0, 5),
    "memory_percent": (90.0, 3),
    "swap_percent": (80.0, 3),
    "root_fs_percent": (90.0, 3),
}
ALERT_CLEAR_MARGIN = 5.0


def _cpu_ticks() -> tuple[int, int]:
    with open("/proc/stat", encoding="ascii") as source:
        fields = source.readline().split()
    if not fields or fields[0] != "cpu" or len(fields) < 5:
        raise RuntimeError("could not read aggregate CPU counters from /proc/stat")
    values = [int(value) for value in fields[1:9]]
    total = sum(values)
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return total, idle


def _cpu_percent(previous: tuple[int, int], current: tuple[int, int]) -> float | None:
    total_delta = current[0] - previous[0]
    idle_delta = current[1] - previous[1]
    if total_delta <= 0 or idle_delta < 0:
        return None
    return round(max(0.0, min(100.0, (total_delta - idle_delta) * 100.0 / total_delta)), 2)


def _meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    with open("/proc/meminfo", encoding="ascii") as source:
        for line in source:
            name, raw, *_ = line.split()
            values[name.rstrip(":")] = int(raw) * 1024
    required = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
    missing = required.difference(values)
    if missing:
        raise RuntimeError("missing /proc/meminfo fields: " + ", ".join(sorted(missing)))
    return values


def collect(previous_cpu: tuple[int, int], current_cpu: tuple[int, int]) -> dict[str, Any]:
    memory = _meminfo()
    filesystem = os.statvfs("/")
    block_size = filesystem.f_frsize or filesystem.f_bsize
    root_total = block_size * filesystem.f_blocks
    root_free = block_size * filesystem.f_bfree
    load_1, load_5, load_15 = os.getloadavg()
    with open("/proc/uptime", encoding="ascii") as source:
        uptime_seconds = float(source.read().split()[0])

    return {
        "sampled_at": datetime.now(timezone.utc).isoformat(),
        "cpu_percent": _cpu_percent(previous_cpu, current_cpu),
        "cpu_count": os.cpu_count() or 1,
        "load_1": load_1,
        "load_5": load_5,
        "load_15": load_15,
        "memory_total_bytes": memory["MemTotal"],
        "memory_available_bytes": min(memory["MemAvailable"], memory["MemTotal"]),
        "swap_total_bytes": memory["SwapTotal"],
        "swap_free_bytes": min(memory["SwapFree"], memory["SwapTotal"]),
        "root_fs_total_bytes": root_total,
        "root_fs_used_bytes": max(0, root_total - root_free),
        "root_fs_available_bytes": block_size * filesystem.f_bavail,
        "uptime_seconds": uptime_seconds,
    }


def check_resource_alerts(sample: dict[str, Any], state: dict[str, dict[str, Any]]) -> None:
    """Log sustained threshold crossings and recovery transitions."""
    memory_total = sample["memory_total_bytes"]
    swap_total = sample["swap_total_bytes"]
    root_total = sample["root_fs_total_bytes"]
    metrics = {
        "cpu_percent": sample["cpu_percent"],
        "memory_percent": (
            100.0 * (memory_total - sample["memory_available_bytes"]) / memory_total
            if memory_total > 0 else None
        ),
        "swap_percent": (
            100.0 * (swap_total - sample["swap_free_bytes"]) / swap_total
            if swap_total > 0 else None
        ),
        "root_fs_percent": (
            100.0 * sample["root_fs_used_bytes"] / root_total
            if root_total > 0 else None
        ),
    }

    for metric, (threshold, required_samples) in ALERT_LIMITS.items():
        value = metrics[metric]
        current = state.setdefault(metric, {"above_count": 0, "active": False})
        if value is None:
            current["above_count"] = 0
            continue
        value = round(value, 2)

        if current["active"]:
            if value <= threshold - ALERT_CLEAR_MARGIN:
                logging.info(
                    "RESOURCE_ALERT event=recovered metric=%s value_percent=%.2f "
                    "clear_below_percent=%.2f",
                    metric, value, threshold - ALERT_CLEAR_MARGIN,
                )
                current["active"] = False
                current["above_count"] = 0
            continue

        if value >= threshold:
            current["above_count"] += 1
            if current["above_count"] >= required_samples:
                logging.warning(
                    "RESOURCE_ALERT event=threshold metric=%s value_percent=%.2f "
                    "threshold_percent=%.2f consecutive_samples=%d",
                    metric, value, threshold, current["above_count"],
                )
                current["active"] = True
        else:
            current["above_count"] = 0


def store(sample: dict[str, Any]) -> None:
    payload = base64.b64encode(json.dumps(sample, separators=(",", ":")).encode("utf-8")).decode("ascii")
    statement = f"""
BEGIN;
SET LOCAL statement_timeout = '15s';
\\set payload_b64 {payload}
WITH p AS (
    SELECT convert_from(decode(:'payload_b64', 'base64'), 'UTF8')::jsonb AS value
)
INSERT INTO public.resource_samples (
    sampled_at, cpu_percent, cpu_count, load_1, load_5, load_15,
    memory_total_bytes, memory_available_bytes, swap_total_bytes, swap_free_bytes,
    root_fs_total_bytes, root_fs_used_bytes, root_fs_available_bytes, uptime_seconds
)
SELECT (value->>'sampled_at')::timestamptz,
       NULLIF(value->>'cpu_percent', '')::double precision,
       (value->>'cpu_count')::integer,
       (value->>'load_1')::double precision,
       (value->>'load_5')::double precision,
       (value->>'load_15')::double precision,
       (value->>'memory_total_bytes')::bigint,
       (value->>'memory_available_bytes')::bigint,
       (value->>'swap_total_bytes')::bigint,
       (value->>'swap_free_bytes')::bigint,
       (value->>'root_fs_total_bytes')::bigint,
       (value->>'root_fs_used_bytes')::bigint,
       (value->>'root_fs_available_bytes')::bigint,
       (value->>'uptime_seconds')::double precision
FROM p;
COMMIT;
"""
    result = subprocess.run(
        [PSQL, "-X", "--quiet", "--set", "ON_ERROR_STOP=1", "--username", ROLE,
         "--dbname", DATABASE, "--host", PG_SOCKET],
        input=statement,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=25,
        check=False,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or "PostgreSQL insert failed")[-1000:])


def _stop(signum: int, _frame: object) -> None:
    logging.info("received signal %s; stopping resource sampling", signum)
    STOP.set()


def run() -> None:
    previous_cpu = _cpu_ticks()
    alert_state: dict[str, dict[str, Any]] = {}
    if STOP.wait(1):
        return
    deadline = time.monotonic()
    while not STOP.is_set():
        try:
            current_cpu = _cpu_ticks()
            sample = collect(previous_cpu, current_cpu)
            check_resource_alerts(sample, alert_state)
            store(sample)
            logging.info(
                "stored resource sample cpu_percent=%s load_1=%.2f memory_available_bytes=%d root_fs_available_bytes=%d",
                sample["cpu_percent"], sample["load_1"], sample["memory_available_bytes"],
                sample["root_fs_available_bytes"],
            )
            previous_cpu = current_cpu
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            logging.exception("resource sample failed: %s", exc)

        deadline += SAMPLE_INTERVAL_SECONDS
        delay = max(0.0, deadline - time.monotonic())
        if delay == 0:
            deadline = time.monotonic()
        STOP.wait(delay)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
