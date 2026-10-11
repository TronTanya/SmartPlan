from datetime import date

import pytest

import database as db
import planner as pl

START = date(2026, 3, 2)


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "test.db"
    db.init_db(path)
    return path


def add(db_path, **overrides) -> int:
    fields = dict(title="Курсовая", subject="Экономика", deadline=START, remaining_minutes=120,
                  priority="Высокий", status="Не начато", notes="")
    fields.update(overrides)
    return db.add_task(**fields, db_path=db_path)


def test_create_and_read(db_path):
    task_id = add(db_path, title="  Курсовая  ", notes="глава 1")
    task = db.get_task(task_id, db_path=db_path)
    assert task is not None
    assert task.title == "Курсовая"
    assert task.deadline == START
    assert task.remaining_minutes == 120
    assert task.notes == "глава 1"
    assert task.created_at
    assert [t.id for t in db.list_tasks(db_path=db_path)] == [task_id]


def test_create_rejects_invalid_task(db_path):
    with pytest.raises(db.ValidationError):
        add(db_path, title="")
    with pytest.raises(db.ValidationError):
        add(db_path, remaining_minutes=0)
    assert db.list_tasks(db_path=db_path) == []


def test_update(db_path):
    task_id = add(db_path)
    assert db.update_task(task_id, "Курсовая", "Экономика", date(2026, 3, 9), 45, "Средний", "В работе",
                          "осталось оформление", db_path=db_path)
    task = db.get_task(task_id, db_path=db_path)
    assert (task.deadline, task.remaining_minutes, task.priority, task.status) == (date(2026, 3, 9), 45, "Средний", "В работе")
    assert not db.update_task(9999, "X", "", START, 10, "Низкий", "Не начато", db_path=db_path)


def test_mark_done_keeps_task_in_history(db_path):
    task_id = add(db_path)
    assert db.mark_done(task_id, db_path=db_path)
    task = db.get_task(task_id, db_path=db_path)
    assert task.status == pl.DONE and task.remaining_minutes == 0
    plan = pl.build_plan(db.list_tasks(db_path=db_path), START, [120] * 7)
    assert plan.outcomes == [] and plan.allocations == []


def test_delete(db_path):
    keep = add(db_path, title="Оставить")
    remove = add(db_path, title="Удалить")
    assert db.delete_task(remove, db_path=db_path)
    assert db.get_task(remove, db_path=db_path) is None
    assert [t.id for t in db.list_tasks(db_path=db_path)] == [keep]
    assert not db.delete_task(remove, db_path=db_path)


def test_persistence_across_reinitialization(db_path):
    task_id = add(db_path, title="Сохраняется")
    db.save_capacities([60, 0, 90, 120, 30, 0, 45], db_path=db_path)
    db.init_db(db_path)  # повторный запуск приложения не должен стирать данные
    assert db.get_task(task_id, db_path=db_path).title == "Сохраняется"
    assert db.get_capacities(db_path=db_path) == [60, 0, 90, 120, 30, 0, 45]


def test_default_and_invalid_capacities(db_path):
    assert db.get_capacities(db_path=db_path) == list(pl.DEFAULT_CAPACITIES)
    with pytest.raises(db.ValidationError):
        db.save_capacities([60] * 6, db_path=db_path)
    with pytest.raises(db.ValidationError):
        db.save_capacities([60, -1, 0, 0, 0, 0, 0], db_path=db_path)


def test_demo_tasks_are_not_duplicated(db_path):
    existing = add(db_path, title="Моя задача")
    first = db.add_demo_tasks(pl.demo_tasks(START), db_path=db_path)
    second = db.add_demo_tasks(pl.demo_tasks(START), db_path=db_path)
    tasks = db.list_tasks(db_path=db_path)
    assert first == 6 and second == 0
    assert len(tasks) == 7
    assert db.get_task(existing, db_path=db_path).title == "Моя задача"
    assert sum(t.title.startswith(pl.DEMO_PREFIX) for t in tasks) == 6


def test_parameterized_queries_store_special_characters(db_path):
    title = "Robert'); DROP TABLE tasks;--"
    task_id = add(db_path, title=title)
    assert db.get_task(task_id, db_path=db_path).title == title
    assert len(db.list_tasks(db_path=db_path)) == 1
