from datetime import date, timedelta

import pytest

import planner as pl
from planner import Task, build_plan

START = date(2026, 3, 2)  # понедельник; фиксированная дата, чтобы тесты не зависели от текущего дня
WEEK = [120] * 7


def make_task(
    task_id: int,
    deadline_offset: int,
    minutes: int,
    priority: str = "Средний",
    status: str = "Не начато",
) -> Task:
    return Task(
        id=task_id,
        title=f"Задача {task_id}",
        subject="Предмет",
        deadline=START + timedelta(days=deadline_offset),
        remaining_minutes=minutes,
        priority=priority,
        status=status,
    )


def scheduled_by_task(plan: pl.Plan) -> dict[int, int]:
    result: dict[int, int] = {}
    for a in plan.allocations:
        result[a.task_id] = result.get(a.task_id, 0) + a.minutes
    return result


def assert_invariants(plan: pl.Plan, tasks: list[Task]) -> None:
    """Общие ограничения, которые должны выполняться для любого плана."""
    for day in plan.days:
        assert day.used <= day.capacity
        assert all(a.minutes > 0 for a in day.allocations)

    by_id = {t.id: t for t in tasks}
    for a in plan.allocations:
        assert a.day <= by_id[a.task_id].deadline
        assert not by_id[a.task_id].is_done

    totals = scheduled_by_task(plan)
    for outcome in plan.outcomes:
        task = outcome.task
        assert outcome.scheduled_minutes == totals.get(task.id, 0)
        assert outcome.scheduled_minutes + outcome.unscheduled_minutes == task.remaining_minutes


def first_day_index(plan: pl.Plan, task_id: int) -> int:
    return min(i for i, d in enumerate(plan.days) for a in d.allocations if a.task_id == task_id)


def test_earlier_deadline_scheduled_first():
    tasks = [make_task(1, 5, 120, "Высокий"), make_task(2, 2, 120, "Низкий")]
    plan = build_plan(tasks, START, WEEK)
    assert [o.task.id for o in plan.outcomes] == [2, 1]
    assert plan.days[0].allocations[0].task_id == 2
    assert first_day_index(plan, 2) < first_day_index(plan, 1)
    assert_invariants(plan, tasks)


def test_priority_breaks_ties_for_equal_deadlines():
    tasks = [make_task(1, 3, 60, "Низкий"), make_task(2, 3, 60, "Высокий"), make_task(3, 3, 60, "Средний")]
    plan = build_plan(tasks, START, WEEK)
    assert [o.task.id for o in plan.outcomes] == [2, 3, 1]
    assert [a.task_id for a in plan.days[0].allocations] == [2, 3]
    assert_invariants(plan, tasks)


def test_stable_order_for_identical_deadline_and_priority():
    tasks = [make_task(7, 3, 60), make_task(3, 3, 60), make_task(5, 3, 60)]
    first = build_plan(tasks, START, WEEK)
    second = build_plan(list(reversed(tasks)), START, WEEK)
    assert [o.task.id for o in first.outcomes] == [3, 5, 7]
    assert first.allocations == second.allocations


def test_task_split_across_days():
    tasks = [make_task(1, 6, 300)]
    plan = build_plan(tasks, START, WEEK)
    assert [(a.day, a.minutes) for a in plan.allocations] == [
        (START, 120),
        (START + timedelta(days=1), 120),
        (START + timedelta(days=2), 60),
    ]
    assert plan.outcomes[0].kind == pl.OUTCOME_FULL
    assert_invariants(plan, tasks)


def test_daily_capacity_never_exceeded():
    caps = [30, 45, 0, 200, 15, 90, 60]
    tasks = [make_task(i, i % 7, 50 + 37 * i, pl.PRIORITIES[i % 3]) for i in range(1, 12)]
    plan = build_plan(tasks, START, caps)
    for day, cap in zip(plan.days, caps):
        assert day.used <= cap
    assert_invariants(plan, tasks)


def test_no_allocation_after_deadline():
    tasks = [make_task(1, 1, 600), make_task(2, 0, 30)]
    plan = build_plan(tasks, START, WEEK)
    for a in plan.allocations:
        assert a.day <= START + timedelta(days=1 if a.task_id == 1 else 0)
    outcome = next(o for o in plan.outcomes if o.task.id == 1)
    assert outcome.kind == pl.OUTCOME_DEADLINE_RISK
    assert_invariants(plan, tasks)


def test_deadline_day_is_inclusive():
    tasks = [make_task(1, 2, 100)]
    plan = build_plan(tasks, START, [0, 0, 100, 0, 0, 0, 0])
    assert [(a.day, a.minutes) for a in plan.allocations] == [(START + timedelta(days=2), 100)]
    assert plan.outcomes[0].kind == pl.OUTCOME_FULL


def test_completed_tasks_excluded():
    tasks = [make_task(1, 2, 0, status="Выполнено"), make_task(2, 2, 60)]
    plan = build_plan(tasks, START, WEEK)
    assert [o.task.id for o in plan.outcomes] == [2]
    assert all(a.task_id != 1 for a in plan.allocations)
    assert plan.total_scheduled == 60


def test_zero_capacity_days_receive_no_allocations():
    caps = [0, 60, 0, 60, 0, 60, 0]
    tasks = [make_task(1, 6, 500)]
    plan = build_plan(tasks, START, caps)
    for day, cap in zip(plan.days, caps):
        if cap == 0:
            assert day.allocations == []
    assert plan.total_scheduled == 180
    assert_invariants(plan, tasks)


def test_overdue_tasks_reported_separately():
    tasks = [make_task(1, -1, 90, "Высокий"), make_task(2, 3, 60), make_task(3, -3, 30, status="Выполнено")]
    plan = build_plan(tasks, START, WEEK)
    assert [t.id for t in plan.overdue] == [1]
    assert [o.task.id for o in plan.outcomes] == [2]
    assert all(a.task_id != 1 for a in plan.allocations)
    assert pl.is_overdue(tasks[0], START)
    assert not pl.is_overdue(tasks[2], START)


def test_insufficient_capacity_preserves_unscheduled_effort():
    tasks = [make_task(1, 2, 300, "Высокий"), make_task(2, 2, 150)]
    plan = build_plan(tasks, START, [120, 120, 90, 0, 0, 0, 0])
    outcomes = {o.task.id: o for o in plan.outcomes}
    assert outcomes[1].scheduled_minutes == 300 and outcomes[1].unscheduled_minutes == 0
    assert outcomes[2].scheduled_minutes == 30 and outcomes[2].unscheduled_minutes == 120
    assert outcomes[2].kind == pl.OUTCOME_DEADLINE_RISK
    assert plan.deadline_risk_minutes == 120
    assert_invariants(plan, tasks)


def test_tasks_beyond_horizon_labeled_correctly():
    tasks = [make_task(1, 10, 1000), make_task(2, 6, 60)]
    plan = build_plan(tasks, START, WEEK)
    outcomes = {o.task.id: o for o in plan.outcomes}
    assert outcomes[2].kind == pl.OUTCOME_FULL
    assert outcomes[1].kind == pl.OUTCOME_BEYOND_HORIZON
    assert outcomes[1].scheduled_minutes == 7 * 120 - 60
    assert outcomes[1].unscheduled_minutes == 1000 - (7 * 120 - 60)
    assert plan.deadline_risk_minutes == 0
    assert plan.beyond_horizon_minutes == outcomes[1].unscheduled_minutes
    assert all(a.day <= plan.end for a in plan.allocations)
    assert_invariants(plan, tasks)


def test_deadline_on_last_horizon_day_is_not_beyond_horizon():
    tasks = [make_task(1, 6, 1000)]
    plan = build_plan(tasks, START, WEEK)
    assert plan.outcomes[0].kind == pl.OUTCOME_DEADLINE_RISK


def test_empty_task_list():
    plan = build_plan([], START, WEEK)
    assert plan.outcomes == [] and plan.overdue == [] and plan.allocations == []
    assert plan.total_scheduled == 0
    assert plan.total_capacity == 7 * 120
    assert len(plan.days) == 7 and plan.days[0].day == START


def test_invalid_capacity_rejected():
    with pytest.raises(ValueError):
        build_plan([], START, [60, -1, 0, 0, 0, 0, 0])
    with pytest.raises(ValueError):
        build_plan([], START, [])


def test_demo_set_produces_real_capacity_warning():
    demo = pl.demo_tasks(START)
    tasks = [
        Task(id=i, title=d["title"], subject=d["subject"], deadline=d["deadline"],
             remaining_minutes=d["remaining_minutes"], priority=d["priority"], status=d["status"])
        for i, d in enumerate(demo, start=1)
    ]
    assert len(tasks) == 6
    assert sum(t.is_done for t in tasks) == 1
    for d in demo:
        assert pl.validate_task_fields(d["title"], d["subject"], d["deadline"], d["remaining_minutes"],
                                       d["priority"], d["status"], d["notes"]) == []

    plan = build_plan(tasks, START, list(pl.DEFAULT_CAPACITIES))
    assert plan.risk_outcomes, "демонстрационный набор должен показывать нехватку времени"
    assert any(o.kind == pl.OUTCOME_BEYOND_HORIZON for o in plan.outcomes)
    assert_invariants(plan, tasks)


def test_summary_counts():
    tasks = [make_task(1, -1, 60), make_task(2, 3, 90), make_task(3, -5, 0, status="Выполнено")]
    s = pl.summarize(tasks, START)
    assert s == {"active": 2, "overdue": 1, "done": 1, "remaining_minutes": 150}


def test_fingerprint_changes_with_inputs():
    tasks = [make_task(1, 3, 60)]
    base = pl.plan_fingerprint(tasks, START, WEEK)
    assert base == pl.plan_fingerprint(tasks, START, WEEK)
    assert base != pl.plan_fingerprint([make_task(1, 3, 45)], START, WEEK)
    assert base != pl.plan_fingerprint(tasks, START + timedelta(days=1), WEEK)
    assert base != pl.plan_fingerprint(tasks, START, [60] + WEEK[1:])


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        (dict(title="  "), "Укажите название задачи."),
        (dict(title="x" * (pl.MAX_TITLE_LEN + 1)), "Название"),
        (dict(remaining_minutes=0), "больше нуля"),
        (dict(remaining_minutes=-5), "отрицательной"),
        (dict(deadline="2026-03-01"), "срок"),
        (dict(priority="Срочный"), "приоритет"),
    ],
)
def test_validation_errors(kwargs, expected):
    fields = dict(title="Эссе", subject="История", deadline=START, remaining_minutes=60,
                  priority="Средний", status="Не начато", notes="")
    fields.update(kwargs)
    errors = pl.validate_task_fields(**fields)
    assert any(expected.lower() in e.lower() for e in errors), errors


def test_validation_allows_past_deadline_and_zero_effort_for_done():
    assert pl.validate_task_fields("Эссе", "", START - timedelta(days=30), 60, "Низкий", "Не начато") == []
    assert pl.validate_task_fields("Эссе", "", START, 0, "Низкий", "Выполнено") == []


def test_format_minutes():
    assert pl.format_minutes(0) == "0 мин"
    assert pl.format_minutes(45) == "45 мин"
    assert pl.format_minutes(60) == "1 ч"
    assert pl.format_minutes(125) == "2 ч 05 мин"
    assert pl.format_hours(150) == "2,5"
