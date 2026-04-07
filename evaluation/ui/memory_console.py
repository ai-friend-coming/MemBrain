"""Rich Live display for the memory ingestion pipeline."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evaluation.memory.pipeline import RunState


def _format_eta(seconds: float) -> str:
    if seconds <= 0:
        return "done"
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    if s >= 60:
        return f"{s // 60}m {s % 60}s"
    return f"{s}s"


def _display_rich_ui(state: "RunState", stop_event: threading.Event) -> None:
    """Render a fixed-height Rich Live UI showing status, active workers, and logs."""
    from rich.console import Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    def _render() -> Group:
        prog_values = list(state.progress.values())
        done = sum(1 for p in prog_values if p["status"] == "completed")
        failed = sum(1 for p in prog_values if p["status"] == "failed")
        running = sum(1 for p in prog_values if p["status"] == "running")
        pending = state.total_tasks - done - failed - running

        status_text = Text()
        status_text.append("MemBrain  ", style="bold cyan")
        status_text.append(f"run: {state.run_tag}\n")
        status_text.append(f"Tasks: {state.total_tasks}  ")
        status_text.append(f"Done: {done}  ", style="green")
        status_text.append(f"Running: {running}  ", style="yellow")
        status_text.append(f"Failed: {failed}  ", style="red")
        status_text.append(f"Pending: {pending}")

        now_mono = time.monotonic()
        p1_done = sum(p["done_summary"] for p in prog_values)
        p1_total = sum(p["total_summary"] for p in prog_values)
        p1_base = sum(p.get("summary_base", 0) for p in prog_values)
        p1_actual_done = p1_done - p1_base
        p1_rate_start = state.pass1_rate_start or state.pass1_start
        p1_rate_elapsed = now_mono - p1_rate_start
        p1_elapsed = now_mono - state.pass1_start
        if p1_total > 0 and p1_done >= p1_total:
            p1_eta = "done"
        elif p1_actual_done > 0 and p1_rate_elapsed > 0:
            p1_eta = _format_eta(
                p1_rate_elapsed / p1_actual_done * (p1_total - p1_done)
            )
        else:
            p1_eta = "--"

        p2_done = sum(p["done_ingest"] for p in prog_values)
        p2_total = sum(p["total_ingest"] for p in prog_values)
        p2_base = sum(p.get("ingest_base", 0) for p in prog_values)
        p2_actual_done = p2_done - p2_base
        if state.pass2_start and p2_actual_done > 0:
            p2_elapsed = now_mono - state.pass2_start
            if p2_total > p2_done:
                p2_eta = _format_eta(p2_elapsed / p2_actual_done * (p2_total - p2_done))
            else:
                p2_eta = "done"
        else:
            p2_eta = "--"

        elapsed_s = int(p1_elapsed)
        if elapsed_s >= 3600:
            elapsed_str = f"{elapsed_s // 3600}h {(elapsed_s % 3600) // 60}m"
        elif elapsed_s >= 60:
            elapsed_str = f"{elapsed_s // 60}m {elapsed_s % 60}s"
        else:
            elapsed_str = f"{elapsed_s}s"

        status_text.append(
            f"\nElapsed: {elapsed_str}  |  Pass 1 ETA: {p1_eta}  |  Pass 2 ETA: {p2_eta}",
            style="dim",
        )
        status_panel = Panel(status_text, border_style="blue")

        worker_table = Table.grid(padding=(0, 1))
        worker_table.add_column(min_width=20, no_wrap=True)
        worker_table.add_column(min_width=8, no_wrap=True)
        worker_table.add_column(min_width=22, no_wrap=True)
        worker_table.add_column(min_width=12, no_wrap=True)

        active = {
            tid: p for tid, p in state.progress.items() if p["status"] == "running"
        }
        for tid, info in sorted(active.items()):
            if info["phase"] == "summary":
                done_n, total_n, phase_label = (
                    info["done_summary"],
                    info["total_summary"],
                    "[Pass 1]",
                )
            else:
                done_n, total_n, phase_label = (
                    info["done_ingest"],
                    info["total_ingest"],
                    "[Pass 2]",
                )
            pct = done_n / total_n if total_n > 0 else 0
            bar_w = 20
            bar = "█" * int(pct * bar_w) + "░" * (bar_w - int(pct * bar_w))
            worker_table.add_row(
                Text(tid, style="cyan"),
                Text(phase_label, style="yellow"),
                Text(bar),
                Text(f"{done_n}/{total_n} sess"),
            )

        worker_panel = Panel(
            worker_table if active else Text("(no active workers)", style="dim"),
            title="Workers",
            border_style="green",
        )

        with state.log_lock:
            log_lines = list(state.log_buffer)
        while len(log_lines) < 3:
            log_lines.append("")
        log_panel = Panel(
            "\n".join(line[:120] for line in log_lines),
            title="Recent Events",
            border_style="dim",
        )
        return Group(status_panel, worker_panel, log_panel)

    with Live(_render(), refresh_per_second=4) as live:
        while not stop_event.is_set():
            time.sleep(0.25)
            live.update(_render())
        live.update(_render())
