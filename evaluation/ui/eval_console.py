"""Rich Live display for the QA evaluation pipeline."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evaluation.answering.pipeline import EvalState


def _format_elapsed(seconds: float) -> str:
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    if s >= 60:
        return f"{s // 60}m {s % 60}s"
    return f"{s}s"


def _display_rich_eval_ui(
    state: "EvalState", stop_event: threading.Event, workers: int
) -> None:
    """Render a fixed-height Rich Live UI: status, active questions, errors."""
    from rich.console import Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    def _render() -> Group:
        elapsed_str = _format_elapsed(time.monotonic() - state.start_time)

        with state.progress_lock:
            judged = state.judged
            correct = state.correct
        wrong = judged - correct
        pct = correct / judged * 100 if judged else 0.0

        status_text = Text()
        status_text.append("MemBrain Eval  ", style="bold cyan")
        status_text.append(
            f"run: {state.run_tag}  Total: {state.total}  "
            f"Elapsed: {elapsed_str}  Workers: {workers}\n"
        )
        status_text.append(f"Judged: {judged}/{state.total}  Accuracy: {pct:.1f}%  ")
        status_text.append(f"✓ {correct}  ", style="green")
        status_text.append(f"✗ {wrong}", style="red")
        status_panel = Panel(status_text, border_style="blue")

        worker_table = Table.grid(padding=(0, 1))
        worker_table.add_column(min_width=20, no_wrap=True)
        worker_table.add_column(min_width=12, no_wrap=True)
        worker_table.add_column(min_width=20, no_wrap=True)
        active = {
            qid: p
            for qid, p in list(state.progress.items())
            if p["status"] in ("answering", "judging")
        }
        for qid, info in sorted(active.items()):
            stage_label = (
                "[Answering]" if info["status"] == "answering" else "[Judging]"
            )
            stage_style = "yellow" if info["status"] == "answering" else "magenta"
            worker_table.add_row(
                Text(qid, style="cyan"),
                Text(stage_label, style=stage_style),
                Text(info.get("task_id", ""), style="dim"),
            )

        active_panel = Panel(
            worker_table if active else Text("(no active questions)", style="dim"),
            title="Active Questions",
            border_style="green",
        )

        with state.log_lock:
            log_lines = list(state.log_buffer)
        while len(log_lines) < 3:
            log_lines.append("")
        error_panel = Panel(
            "\n".join(line[:120] for line in log_lines),
            title="Errors",
            border_style="dim",
        )
        return Group(status_panel, active_panel, error_panel)

    with Live(_render(), refresh_per_second=4) as live:
        while not stop_event.is_set():
            time.sleep(0.25)
            live.update(_render())
        live.update(_render())
