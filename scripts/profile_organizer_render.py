"""
ABOUTME: Profile one /organizer render end to end, phase by phase, on the real cloud.
ABOUTME: Isolated session copy (PROBE_SESSION_FILE); the live session is never touched.

Run on m3 from the repo root (or a worktree):

    PROBE_SESSION_FILE=/tmp/organizer-render-probe/session.json \
    PYTHONPATH=src \
    /Users/braydon/projects/experiments/paia-supernote/.venv/bin/python \
    scripts/profile_organizer_render.py [--notebook NAME] [--repeat N]

Prints per-phase wall time, python CPU, process-tree CPU (python + playwright
driver + chromium), cloud API call counts by endpoint, and optionally the
cProfile top entries (--profile) or a download/parse/serialize split of
get_snapshot (--split).
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import faulthandler
import os
import pstats
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from paia_supernote.organizer_runtime import create_organizer_api
from paia_supernote.organizer_ui import render_index
from paia_supernote.uploader import SupernoteUploader

# Dump all thread stacks and exit if a probe run wedges, so a hang leaves
# evidence instead of a silent timeout.
faulthandler.dump_traceback_later(
    float(os.environ.get("PROBE_WATCHDOG_S") or 150), exit=True
)


def _ps_tree_cpu_seconds(root_pid: int) -> float:
    """Sum CPU seconds over root_pid and all its descendants (macOS ps)."""
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,time="],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except Exception:
        return float("nan")
    rows: dict[int, tuple[int, float]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        pid, ppid, timestr = int(parts[0]), int(parts[1]), parts[2]
        minutes, seconds = timestr.split(":")[-2:]
        rows[pid] = (ppid, float(minutes) * 60 + float(seconds))
    total = 0.0
    seen = {root_pid}
    while seen:
        pid = seen.pop()
        entry = rows.get(pid)
        if entry is None:
            continue
        total += entry[1]
        seen.update(child for child, (parent, _) in rows.items() if parent == pid)
    return total


class ProbeUploader(SupernoteUploader):
    """Uploader pointed at an isolated session copy; counts cloud API calls."""

    api_calls: Counter[str]

    def __init__(self, session_file: Path) -> None:
        self.SESSION_FILE = session_file
        self.api_calls = Counter()
        super().__init__(headless=True)

    async def _api_call(self, endpoint: str, body: dict) -> dict:
        trace = bool(os.environ.get("PROBE_TRACE"))
        if trace:
            print(f"[{time.strftime('%H:%M:%S')}] api -> {endpoint}", flush=True)
        self.api_calls[endpoint] += 1
        result = await super()._api_call(endpoint, body)
        if trace:
            status = result.get("status") if isinstance(result, dict) else "?"
            print(
                f"[{time.strftime('%H:%M:%S')}] api <- {endpoint}" f" status={status}",
                flush=True,
            )
        return result


def _snapshot_cpu_pair() -> tuple[float, float]:
    return time.process_time(), _ps_tree_cpu_seconds(os.getpid())


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--notebook", default=None, help="selected notebook (default: first listed)"
    )
    parser.add_argument("--profile", action="store_true", help="print cProfile entries")
    parser.add_argument(
        "--split",
        action="store_true",
        help="also split get_snapshot into download/parse/serialize",
    )
    parser.add_argument(
        "--repeat", type=int, default=1, help="render N times, timing each pass"
    )
    args = parser.parse_args()

    session_file = Path(os.environ["PROBE_SESSION_FILE"]).expanduser()
    if not session_file.exists():
        print(f"session file missing: {session_file}", file=sys.stderr)
        return 2

    uploader = ProbeUploader(session_file)
    api = create_organizer_api(
        uploader=uploader,
        cache_dir=os.environ.get("PROBE_CACHE_DIR")
        or "/tmp/organizer-render-probe/images",
    )

    await uploader.start()
    try:
        prof = cProfile.Profile()
        py_cpu_start = time.process_time()
        if args.profile:
            prof.enable()

        c0, s0 = _snapshot_cpu_pair()
        t0 = time.perf_counter()
        notebooks = await api.list_notebooks()
        t_list = time.perf_counter()
        list_wall = t_list - t0
        list_py = time.process_time() - c0
        list_tree = _ps_tree_cpu_seconds(os.getpid()) - s0

        selected = args.notebook or (
            str(notebooks[0].get("name")) if notebooks else None
        )
        c0, s0 = _snapshot_cpu_pair()
        snapshot = (
            await api.get_snapshot(selected)
            if selected
            else {"notebook_name": "", "revision": "", "page_order": [], "pages": {}}
        )
        t_snapshot = time.perf_counter()
        snap_wall = t_snapshot - t_list
        snap_py = time.process_time() - c0
        snap_tree = _ps_tree_cpu_seconds(os.getpid()) - s0

        c0, s0 = _snapshot_cpu_pair()
        html = render_index(notebooks=notebooks, snapshot=snapshot)
        t_render = time.perf_counter()
        render_wall = t_render - t_snapshot
        render_py = time.process_time() - c0
        render_tree = _ps_tree_cpu_seconds(os.getpid()) - s0

        if args.profile:
            prof.disable()
        py_cpu = time.process_time() - py_cpu_start
        wall = t_render - t0

        print(f"selected notebook : {selected}")
        print(f"notebooks listed  : {len(notebooks)}")
        print(f"pages in snapshot : {len(snapshot.get('page_order') or [])}")
        print(f"html bytes        : {len(html.encode('utf-8'))}")
        print(
            f"list_notebooks    : {list_wall:.3f} s wall | {list_py:.3f} s"
            f" py CPU | {list_tree:.3f} s tree CPU"
        )
        print(
            f"get_snapshot      : {snap_wall:.3f} s wall | {snap_py:.3f} s"
            f" py CPU | {snap_tree:.3f} s tree CPU"
        )
        print(
            f"render_index      : {render_wall:.3f} s wall | {render_py:.3f} s"
            f" py CPU | {render_tree:.3f} s tree CPU"
        )
        tree_cpu = list_tree + snap_tree + render_tree
        print(
            f"total render      : {wall:.3f} s wall | {py_cpu:.3f} s"
            f" python CPU | {tree_cpu:.3f} s tree CPU"
        )
        print(f"cloud API calls   : {dict(uploader.api_calls)}")

        for i in range(args.repeat - 1):
            gap = float(os.environ.get("PROBE_RENDER_GAP_S") or 0)
            if gap:
                await asyncio.sleep(gap)
            c0, s0 = _snapshot_cpu_pair()
            t0 = time.perf_counter()
            notebooks = await api.list_notebooks()
            snapshot = await api.get_snapshot(selected) if selected else snapshot
            html2 = render_index(notebooks=notebooks, snapshot=snapshot)
            r_wall = time.perf_counter() - t0
            r_py = time.process_time() - c0
            r_tree = _ps_tree_cpu_seconds(os.getpid()) - s0
            same = html2 == html
            print(
                f"render pass {i + 2}   : {r_wall:.3f} s wall | {r_py:.3f} s"
                f" py CPU | {r_tree:.3f} s tree CPU"
                f" | identical html: {same}"
                f" | api calls so far: {dict(uploader.api_calls)}"
            )

        if args.split:
            from paia_supernote.organizer_api import serialize_snapshot

            c0, s0 = _snapshot_cpu_pair()
            t0 = time.perf_counter()
            note_bytes = await uploader.download_notebook(f"{selected}.note")
            d_wall = time.perf_counter() - t0
            d_py = time.process_time() - c0
            d_tree = _ps_tree_cpu_seconds(os.getpid()) - s0

            loader = api.snapshot_loader
            c0 = time.process_time()
            t0 = time.perf_counter()
            snap_obj = loader(str(selected), note_bytes)
            p_wall = time.perf_counter() - t0
            p_py = time.process_time() - c0

            c0 = time.process_time()
            t0 = time.perf_counter()
            _ = serialize_snapshot(snap_obj)
            ser_wall = time.perf_counter() - t0
            ser_py = time.process_time() - c0

            print(f"note bytes        : {len(note_bytes):,}")
            print(
                f"download only     : {d_wall:.3f} s wall | {d_py:.3f} s"
                f" py CPU | {d_tree:.3f} s tree CPU"
            )
            print(f"snapshot_loader   : {p_wall:.3f} s wall | {p_py:.3f} s py CPU")
            print(
                f"serialize_snapshot : {ser_wall:.3f} s wall"
                f" | {ser_py:.3f} s py CPU"
            )

        if args.profile:
            stats = pstats.Stats(prof)
            print("\n--- top 20 by cumulative ---")
            stats.sort_stats("cumulative").print_stats(20)
            print("\n--- top 20 by internal time ---")
            stats.sort_stats("tottime").print_stats(20)
    finally:
        await uploader.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
