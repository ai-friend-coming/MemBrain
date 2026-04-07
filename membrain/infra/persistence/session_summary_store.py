"""Session summary persistence adapter."""

from __future__ import annotations

from membrain.infra.models.memory import SessionSummaryModel
from membrain.infra.transaction_manager import TransactionManager


class SessionSummaryStore:
    def __init__(self, transactions: TransactionManager) -> None:
        self._transactions = transactions

    def exists(self, task_id: int, session_id: int) -> bool:
        with self._transactions.read() as db:
            return (
                db.query(SessionSummaryModel.id)
                .filter(
                    SessionSummaryModel.task_id == task_id,
                    SessionSummaryModel.session_id == session_id,
                )
                .first()
                is not None
            )

    def save(self, task_id: int, session_id: int, content: str) -> None:
        with self._transactions.write() as db:
            db.add(
                SessionSummaryModel(
                    task_id=task_id,
                    session_id=session_id,
                    subject="",
                    content=content,
                )
            )
