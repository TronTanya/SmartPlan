"""Бизнес-логика SmartPlan: модель задачи, проверка ввода и эвристическое планирование.

Модуль не зависит от Streamlit и базы данных, поэтому его легко тестировать.
Все длительности хранятся и считаются в целых минутах.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Sequence

PRIORITIES: tuple[str, ...] = ("Низкий", "Средний", "Высокий")
STATUSES: tuple[str, ...] = ("Не начато", "В работе", "Выполнено")
DONE = "Выполнено"

# Меньшее значение — выше приоритет при сортировке.
PRIORITY_RANK: dict[str, int] = {"Высокий": 0, "Средний": 1, "Низкий": 2}

WEEKDAYS_SHORT: tuple[str, ...] = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")

HORIZON_DAYS = 7
MAX_TITLE_LEN = 120
MAX_SUBJECT_LEN = 60
MAX_NOTES_LEN = 1000
MAX_EFFORT_MINUTES = 10_000
MAX_DAILY_CAPACITY = 1_440
MIN_DEADLINE = date(2000, 1, 1)
MAX_DEADLINE = date(2100, 12, 31)

# Итог распределения задачи.
OUTCOME_FULL = "full"
OUTCOME_DEADLINE_RISK = "deadline_risk"
OUTCOME_BEYOND_HORIZON = "beyond_horizon"

OUTCOME_LABELS: dict[str, str] = {
    OUTCOME_FULL: "Распределено полностью",
    OUTCOME_DEADLINE_RISK: "Не хватает времени до срока",
    OUTCOME_BEYOND_HORIZON: "Остаток за пределами текущего плана",
}


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    subject: str
    deadline: date
    remaining_minutes: int
    priority: str
    status: str
    notes: str = ""
    created_at: str = ""

    @property
    def is_done(self) -> bool:
        return self.status == DONE


@dataclass(frozen=True)
class Allocation:
    day: date
    task_id: int
    title: str
    subject: str
    minutes: int


@dataclass
class DayPlan:
    day: date
    capacity: int
    allocations: list[Allocation] = field(default_factory=list)

    @property
    def used(self) -> int:
        return sum(a.minutes for a in self.allocations)

    @property
    def free(self) -> int:
        return self.capacity - self.used


@dataclass(frozen=True)
class TaskOutcome:
    task: Task
    scheduled_minutes: int
    unscheduled_minutes: int
    kind: str

    @property
    def label(self) -> str:
        return OUTCOME_LABELS[self.kind]


@dataclass
class Plan:
    start: date
    days: list[DayPlan]
    outcomes: list[TaskOutcome]
    overdue: list[Task]

    @property
    def end(self) -> date:
        return self.days[-1].day if self.days else self.start

    @property
    def allocations(self) -> list[Allocation]:
        return [a for d in self.days for a in d.allocations]

    @property
    def total_capacity(self) -> int:
        return sum(d.capacity for d in self.days)

    @property
    def total_scheduled(self) -> int:
        return sum(d.used for d in self.days)

    @property
    def deadline_risk_minutes(self) -> int:
        return sum(o.unscheduled_minutes for o in self.outcomes if o.kind == OUTCOME_DEADLINE_RISK)

    @property
    def beyond_horizon_minutes(self) -> int:
        return sum(o.unscheduled_minutes for o in self.outcomes if o.kind == OUTCOME_BEYOND_HORIZON)

    @property
    def risk_outcomes(self) -> list[TaskOutcome]:
        return [o for o in self.outcomes if o.kind == OUTCOME_DEADLINE_RISK]


def validate_task_fields(
    title: str,
    subject: str,
    deadline: object,
    remaining_minutes: object,
    priority: str,
    status: str,
    notes: str = "",
) -> list[str]:
    """Возвращает список сообщений об ошибках (пустой — если данные корректны)."""
    errors: list[str] = []
    title = (title or "").strip()
    subject = (subject or "").strip()
    notes = notes or ""

    if not title:
        errors.append("Укажите название задачи.")
    elif len(title) > MAX_TITLE_LEN:
        errors.append(f"Название не должно превышать {MAX_TITLE_LEN} символов.")
    if len(subject) > MAX_SUBJECT_LEN:
        errors.append(f"Название предмета не должно превышать {MAX_SUBJECT_LEN} символов.")
    if len(notes) > MAX_NOTES_LEN:
        errors.append(f"Заметки не должны превышать {MAX_NOTES_LEN} символов.")

    if not isinstance(deadline, date):
        errors.append("Укажите корректный срок сдачи.")
    elif not (MIN_DEADLINE <= deadline <= MAX_DEADLINE):
        errors.append("Срок сдачи должен быть в диапазоне 2000–2100 годов.")

    if priority not in PRIORITIES:
        errors.append("Выберите приоритет из списка.")
    if status not in STATUSES:
        errors.append("Выберите статус из списка.")

    if isinstance(remaining_minutes, bool) or not isinstance(remaining_minutes, int):
        errors.append("Оставшаяся трудоёмкость должна быть целым числом минут.")
    elif remaining_minutes < 0:
        errors.append("Оставшаяся трудоёмкость не может быть отрицательной.")
    elif remaining_minutes > MAX_EFFORT_MINUTES:
        errors.append(f"Оставшаяся трудоёмкость не должна превышать {MAX_EFFORT_MINUTES} минут.")
    elif remaining_minutes == 0 and status != DONE:
        errors.append("Для невыполненной задачи укажите оставшееся время больше нуля.")

    return errors


def is_overdue(task: Task, start: date) -> bool:
    """Просрочена — невыполненная задача со сроком раньше даты начала планирования."""
    return not task.is_done and task.deadline < start


def sort_key(task: Task) -> tuple[date, int, int]:
    """Ближайший срок, затем более высокий приоритет, затем ID для стабильности."""
    return (task.deadline, PRIORITY_RANK.get(task.priority, len(PRIORITY_RANK)), task.id)


def build_plan(tasks: Iterable[Task], start: date, capacities: Sequence[int]) -> Plan:
    """Строит детерминированный эвристический план на len(capacities) дней.

    Правила:
    1. Берутся только невыполненные задачи.
    2. Просроченные задачи не планируются и возвращаются отдельно.
    3. Остальные сортируются по сроку, приоритету и ID.
    4. Остаток каждой задачи распределяется по дням, начиная с самого раннего
       дня со свободным временем, не позже срока (срок включительно)
       и без превышения доступного времени дня.
    5. Нераспределённые минуты сохраняются в итоге по задаче.
    """
    if not capacities:
        raise ValueError("Нужно указать доступное время хотя бы для одного дня.")
    for minutes in capacities:
        if isinstance(minutes, bool) or not isinstance(minutes, int):
            raise ValueError("Доступное время должно быть целым числом минут.")
        if minutes < 0 or minutes > MAX_DAILY_CAPACITY:
            raise ValueError(f"Доступное время дня должно быть от 0 до {MAX_DAILY_CAPACITY} минут.")

    days = [DayPlan(day=start + timedelta(days=i), capacity=c) for i, c in enumerate(capacities)]
    horizon_end = days[-1].day

    active = [t for t in tasks if not t.is_done]
    overdue = sorted((t for t in active if t.deadline < start), key=sort_key)
    schedulable = sorted((t for t in active if t.deadline >= start), key=sort_key)

    outcomes: list[TaskOutcome] = []
    for task in schedulable:
        remaining = max(task.remaining_minutes, 0)
        scheduled = 0
        for day_plan in days:
            if remaining == 0 or day_plan.day > task.deadline:
                break
            take = min(remaining, day_plan.free)
            if take <= 0:
                continue
            day_plan.allocations.append(
                Allocation(day_plan.day, task.id, task.title, task.subject, take)
            )
            remaining -= take
            scheduled += take

        if remaining == 0:
            kind = OUTCOME_FULL
        elif task.deadline > horizon_end:
            kind = OUTCOME_BEYOND_HORIZON
        else:
            kind = OUTCOME_DEADLINE_RISK
        outcomes.append(TaskOutcome(task, scheduled, remaining, kind))

    return Plan(start=start, days=days, outcomes=outcomes, overdue=overdue)


def summarize(tasks: Iterable[Task], start: date) -> dict[str, int]:
    """Показатели для сводной полосы. Минуты — только по невыполненным задачам."""
    tasks = list(tasks)
    active = [t for t in tasks if not t.is_done]
    return {
        "active": len(active),
        "overdue": sum(1 for t in active if t.deadline < start),
        "done": sum(1 for t in tasks if t.is_done),
        "remaining_minutes": sum(t.remaining_minutes for t in active),
    }


def plan_fingerprint(tasks: Iterable[Task], start: date, capacities: Sequence[int]) -> str:
    """Отпечаток входных данных плана: если он изменился, план нужно составить заново."""
    parts = [start.isoformat(), ",".join(str(c) for c in capacities)]
    for t in sorted(tasks, key=lambda t: t.id):
        parts.append(
            f"{t.id}|{t.title}|{t.subject}|{t.deadline.isoformat()}|"
            f"{t.remaining_minutes}|{t.priority}|{t.status}"
        )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def format_minutes(minutes: int) -> str:
    """125 -> «2 ч 05 мин», 60 -> «1 ч», 45 -> «45 мин»."""
    minutes = int(minutes)
    if minutes <= 0:
        return "0 мин"
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours} ч {mins:02d} мин"
    if hours:
        return f"{hours} ч"
    return f"{mins} мин"


def format_hours(minutes: int) -> str:
    """Часы с одним знаком после запятой в русском формате: 150 -> «2,5»."""
    return f"{minutes / 60:.1f}".replace(".", ",")


def format_date(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def format_day_label(d: date) -> str:
    return f"{WEEKDAYS_SHORT[d.weekday()]} {d.strftime('%d.%m')}"


DEMO_PREFIX = "[Демо] "


def demo_tasks(start: date) -> list[dict]:
    """Шесть вымышленных демонстрационных заданий со сроками относительно даты начала.

    При профиле времени по умолчанию (DEFAULT_CAPACITIES) лабораторной работе
    по физике не хватает времени до срока — это реальное предупреждение планировщика.
    """
    def d(offset: int) -> date:
        return start + timedelta(days=offset)

    return [
        {
            "demo_key": "demo-1",
            "title": DEMO_PREFIX + "Литературный обзор для курсовой",
            "subject": "Методология науки",
            "deadline": d(2),
            "remaining_minutes": 300,
            "priority": "Высокий",
            "status": "В работе",
            "notes": "Демонстрационное задание.",
        },
        {
            "demo_key": "demo-2",
            "title": DEMO_PREFIX + "Лабораторная работа №3: отчёт",
            "subject": "Физика",
            "deadline": d(2),
            "remaining_minutes": 150,
            "priority": "Средний",
            "status": "Не начато",
            "notes": "Демонстрационное задание.",
        },
        {
            "demo_key": "demo-3",
            "title": DEMO_PREFIX + "Решить задачи к контрольной",
            "subject": "Математический анализ",
            "deadline": d(5),
            "remaining_minutes": 180,
            "priority": "Высокий",
            "status": "Не начато",
            "notes": "Демонстрационное задание.",
        },
        {
            "demo_key": "demo-4",
            "title": DEMO_PREFIX + "Тезисы доклада",
            "subject": "Научная конференция",
            "deadline": d(6),
            "remaining_minutes": 60,
            "priority": "Средний",
            "status": "Не начато",
            "notes": "Демонстрационное задание.",
        },
        {
            "demo_key": "demo-5",
            "title": DEMO_PREFIX + "Эссе по истории науки",
            "subject": "История",
            "deadline": d(12),
            "remaining_minutes": 200,
            "priority": "Низкий",
            "status": "Не начато",
            "notes": "Демонстрационное задание. Срок за пределами недельного плана.",
        },
        {
            "demo_key": "demo-6",
            "title": DEMO_PREFIX + "Реферат по философии",
            "subject": "Философия",
            "deadline": d(-1),
            "remaining_minutes": 0,
            "priority": "Низкий",
            "status": "Выполнено",
            "notes": "Демонстрационное задание (уже выполнено).",
        },
    ]


DEFAULT_CAPACITIES: tuple[int, ...] = (120, 120, 90, 0, 120, 60, 90)
