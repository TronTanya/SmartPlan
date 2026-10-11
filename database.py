"""Хранение задач и настроек SmartPlan в SQLite (только параметризованные запросы)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

from planner import (
    DEFAULT_CAPACITIES,
    DONE,
    HORIZON_DAYS,
    MAX_DAILY_CAPACITY,
    Task,
    validate_task_fields,
)

DB_PATH = Path(__file__).resolve().parent / "data" / "smartplan.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    title             TEXT    NOT NULL,
    subject           TEXT    NOT NULL DEFAULT '',
    deadline          TEXT    NOT NULL,
    remaining_minutes INTEGER NOT NULL CHECK (remaining_minutes >= 0),
    priority          TEXT    NOT NULL,
    status            TEXT    NOT NULL,
    notes             TEXT    NOT NULL DEFAULT '',
    created_at        TEXT    NOT NULL,
    demo_key          TEXT    UNIQUE
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_TASK_COLUMNS = "id, title, subject, deadline, remaining_minutes, priority, status, notes, created_at"


class ValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | str = DB_PATH) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(db_path)) as conn, conn:
        conn.executescript(_SCHEMA)


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        title=row["title"],
        subject=row["subject"],
        deadline=date.fromisoformat(row["deadline"]),
        remaining_minutes=row["remaining_minutes"],
        priority=row["priority"],
        status=row["status"],
        notes=row["notes"],
        created_at=row["created_at"],
    )


def _clean(
    title: str, subject: str, deadline: date, remaining_minutes: int,
    priority: str, status: str, notes: str,
) -> tuple[str, str, str, int, str, str, str]:
    errors = validate_task_fields(title, subject, deadline, remaining_minutes, priority, status, notes)
    if errors:
        raise ValidationError(errors)
    return (
        title.strip(), (subject or "").strip(), deadline.isoformat(),
        remaining_minutes, priority, status, (notes or "").strip(),
    )


def add_task(
    title: str, subject: str, deadline: date, remaining_minutes: int,
    priority: str, status: str, notes: str = "", db_path: Path | str = DB_PATH,
) -> int:
    values = _clean(title, subject, deadline, remaining_minutes, priority, status, notes)
    created_at = datetime.now().isoformat(timespec="seconds")
    with closing(_connect(db_path)) as conn, conn:
        cur = conn.execute(
            "INSERT INTO tasks (title, subject, deadline, remaining_minutes, priority, status, notes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (*values, created_at),
        )
        return int(cur.lastrowid)


def update_task(
    task_id: int, title: str, subject: str, deadline: date, remaining_minutes: int,
    priority: str, status: str, notes: str = "", db_path: Path | str = DB_PATH,
) -> bool:
    values = _clean(title, subject, deadline, remaining_minutes, priority, status, notes)
    with closing(_connect(db_path)) as conn, conn:
        cur = conn.execute(
            "UPDATE tasks SET title = ?, subject = ?, deadline = ?, remaining_minutes = ?, "
            "priority = ?, status = ?, notes = ? WHERE id = ?",
            (*values, task_id),
        )
        return cur.rowcount > 0


def mark_done(task_id: int, db_path: Path | str = DB_PATH) -> bool:
    """Отмечает задачу выполненной; оставшееся время становится равным нулю."""
    with closing(_connect(db_path)) as conn, conn:
        cur = conn.execute(
            "UPDATE tasks SET status = ?, remaining_minutes = 0 WHERE id = ?", (DONE, task_id)
        )
        return cur.rowcount > 0


def delete_task(task_id: int, db_path: Path | str = DB_PATH) -> bool:
    with closing(_connect(db_path)) as conn, conn:
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cur.rowcount > 0


def get_task(task_id: int, db_path: Path | str = DB_PATH) -> Task | None:
    with closing(_connect(db_path)) as conn:
        row = conn.execute(f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_task(row) if row else None


def list_tasks(db_path: Path | str = DB_PATH) -> list[Task]:
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(f"SELECT {_TASK_COLUMNS} FROM tasks ORDER BY id").fetchall()
    return [_row_to_task(r) for r in rows]


def add_demo_tasks(items: Sequence[dict], db_path: Path | str = DB_PATH) -> int:
    """Добавляет демонстрационные задачи, пропуская уже загруженные. Возвращает число добавленных."""
    created_at = datetime.now().isoformat(timespec="seconds")
    inserted = 0
    with closing(_connect(db_path)) as conn, conn:
        for item in items:
            values = _clean(
                item["title"], item["subject"], item["deadline"], item["remaining_minutes"],
                item["priority"], item["status"], item.get("notes", ""),
            )
            cur = conn.execute(
                "INSERT OR IGNORE INTO tasks (title, subject, deadline, remaining_minutes, priority, "
                "status, notes, created_at, demo_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*values, created_at, item["demo_key"]),
            )
            inserted += cur.rowcount
    return inserted


def get_capacities(db_path: Path | str = DB_PATH) -> list[int]:
    """Доступное время (мин) на каждый из семи дней; при отсутствии или ошибке — значения по умолчанию."""
    with closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", ("capacities",)).fetchone()
    if row is None:
        return list(DEFAULT_CAPACITIES)
    try:
        values = json.loads(row["value"])
        if (
            isinstance(values, list)
            and len(values) == HORIZON_DAYS
            and all(isinstance(v, int) and 0 <= v <= MAX_DAILY_CAPACITY for v in values)
        ):
            return values
    except (TypeError, ValueError):
        pass
    return list(DEFAULT_CAPACITIES)


def save_capacities(capacities: Sequence[int], db_path: Path | str = DB_PATH) -> None:
    values = [int(v) for v in capacities]
    if len(values) != HORIZON_DAYS or any(v < 0 or v > MAX_DAILY_CAPACITY for v in values):
        raise ValidationError([f"Укажите от 0 до {MAX_DAILY_CAPACITY} минут для каждого из {HORIZON_DAYS} дней."])
    with closing(_connect(db_path)) as conn, conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("capacities", json.dumps(values)),
        )
