"""SmartPlan — интерфейс Streamlit. Бизнес-логика находится в planner.py, хранение — в database.py."""

from __future__ import annotations

import html
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import database as db
import planner as pl
from planner import Task

st.set_page_config(page_title="SmartPlan — учебные задачи", layout="wide")

COLOR_TEXT = "#202733"
COLOR_MUTED = "#667085"
COLOR_BORDER = "#E4E7EC"
COLOR_ACCENT = "#315EFB"
COLOR_SUCCESS = "#278451"
COLOR_WARNING = "#B7791F"
COLOR_ERROR = "#C43D3D"

STATUS_COLORS = {"Не начато": COLOR_MUTED, "В работе": COLOR_ACCENT, "Выполнено": COLOR_SUCCESS}
PRIORITY_COLORS = {"Высокий": COLOR_ERROR, "Средний": COLOR_WARNING, "Низкий": COLOR_MUTED}
SORT_OPTIONS = ("Срок: ближайшие сначала", "Приоритет: высокий сначала")
ALGORITHM_TEXT = (
    "Сначала распределяются задачи с ближайшим сроком. При одинаковом сроке учитывается "
    "приоритет. План ограничен доступным временем на каждый день."
)

CSS = f"""
<style>
[data-testid="stMainBlockContainer"] {{ max-width: 1180px; padding-top: 2.2rem; padding-bottom: 3rem; }}
.sp-header {{ display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
  border-bottom: 1px solid {COLOR_BORDER}; padding-bottom: 12px; margin-bottom: 2px; }}
.sp-title {{ font-size: 27px; font-weight: 650; color: {COLOR_TEXT}; letter-spacing: -0.01em; line-height: 1.2; }}
.sp-subtitle {{ font-size: 14px; color: {COLOR_MUTED}; }}
[class*="st-key-card_"] {{ background: #FFFFFF; }}
table.sp-table {{ width: 100%; border-collapse: collapse; font-size: 14px; background: #FFFFFF;
  border: 1px solid {COLOR_BORDER}; border-radius: 7px; overflow: hidden; }}
table.sp-table th {{ text-align: left; font-weight: 600; font-size: 12px; color: {COLOR_MUTED};
  background: #F9FAFB; padding: 8px 12px; border-bottom: 1px solid {COLOR_BORDER}; }}
table.sp-table td {{ padding: 7px 12px; border-bottom: 1px solid {COLOR_BORDER}; vertical-align: top; color: {COLOR_TEXT}; }}
table.sp-table tr:hover td {{ background: #FAFBFC; }}
table.sp-table .num {{ text-align: right; white-space: nowrap; }}
table.sp-table .day {{ white-space: nowrap; font-weight: 600; }}
table.sp-table .meta {{ color: {COLOR_MUTED}; font-size: 12px; }}
</style>
"""


# ---------- Состояние ----------

@st.cache_resource
def _init_storage() -> bool:
    db.init_db()
    return True


def init_state() -> None:
    _init_storage()
    ss = st.session_state
    ss.setdefault("start_date", date.today())
    if "cap_0" not in ss:
        for i, minutes in enumerate(db.get_capacities()):
            ss[f"cap_{i}"] = minutes
    ss.setdefault("flash", [])
    ss.setdefault("table_version", 0)
    ss.setdefault("confirm_delete", None)


def capacities() -> list[int]:
    return [int(st.session_state[f"cap_{i}"]) for i in range(pl.HORIZON_DAYS)]


def save_capacities() -> None:
    try:
        db.save_capacities(capacities())
    except db.ValidationError as exc:
        flash("error", str(exc))


def flash(kind: str, text: str) -> None:
    st.session_state["flash"].append((kind, text))


def show_flash() -> None:
    for kind, text in st.session_state["flash"]:
        {"success": st.success, "warning": st.warning, "error": st.error}.get(kind, st.info)(text)
    st.session_state["flash"] = []


def data_changed() -> None:
    """Сбрасывает выделение в таблице после изменения данных."""
    st.session_state["table_version"] += 1
    st.session_state["confirm_delete"] = None


# ---------- Экспорт ----------

def to_csv_bytes(df: pd.DataFrame) -> bytes:
    """CSV в UTF-8 с BOM, разделителем «;» и десятичной запятой — для русской версии Excel."""
    return df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")


def tasks_export_df(tasks: list[Task], start: date) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ID": t.id,
                "Задача": t.title,
                "Предмет": t.subject,
                "Срок сдачи": pl.format_date(t.deadline),
                "Осталось, мин": t.remaining_minutes,
                "Осталось, ч": round(t.remaining_minutes / 60, 2),
                "Приоритет": t.priority,
                "Статус": t.status,
                "Просрочено": "да" if pl.is_overdue(t, start) else "нет",
                "Заметки": t.notes,
                "Создано": t.created_at.replace("T", " "),
            }
            for t in tasks
        ],
        columns=["ID", "Задача", "Предмет", "Срок сдачи", "Осталось, мин", "Осталось, ч",
                 "Приоритет", "Статус", "Просрочено", "Заметки", "Создано"],
    )


def plan_export_df(plan: pl.Plan) -> pd.DataFrame:
    rows = []
    for day in plan.days:
        for a in day.allocations:
            rows.append(
                {
                    "Дата": pl.format_date(day.day),
                    "День недели": pl.WEEKDAYS_SHORT[day.day.weekday()],
                    "ID задачи": a.task_id,
                    "Предмет": a.subject,
                    "Задача": a.title,
                    "Запланировано, мин": a.minutes,
                    "Запланировано, ч": round(a.minutes / 60, 2),
                    "Итого за день, мин": day.used,
                    "Доступно за день, мин": day.capacity,
                }
            )
    return pd.DataFrame(
        rows,
        columns=["Дата", "День недели", "ID задачи", "Предмет", "Задача", "Запланировано, мин",
                 "Запланировано, ч", "Итого за день, мин", "Доступно за день, мин"],
    )


# ---------- Диалоги задач ----------

def task_form(task: Task | None) -> None:
    start: date = st.session_state["start_date"]
    key = f"form_{task.id if task else 'new'}"
    title = st.text_input("Название", value=task.title if task else "", max_chars=pl.MAX_TITLE_LEN, key=f"{key}_title")
    subject = st.text_input("Предмет", value=task.subject if task else "", max_chars=pl.MAX_SUBJECT_LEN, key=f"{key}_subject")
    c1, c2 = st.columns(2)
    deadline = c1.date_input(
        "Срок сдачи", value=task.deadline if task else start + timedelta(days=3),
        min_value=pl.MIN_DEADLINE, max_value=pl.MAX_DEADLINE, format="DD.MM.YYYY", key=f"{key}_deadline",
    )
    remaining = c2.number_input(
        "Осталось работы, мин", min_value=0, max_value=pl.MAX_EFFORT_MINUTES, step=15,
        value=task.remaining_minutes if task else 60, key=f"{key}_remaining",
        help="Сколько времени ещё нужно, чтобы закончить задачу. Обновляйте по мере работы.",
    )
    c3, c4 = st.columns(2)
    priority = c3.selectbox("Приоритет", pl.PRIORITIES, index=pl.PRIORITIES.index(task.priority) if task else 1, key=f"{key}_priority")
    status = c4.selectbox("Статус", pl.STATUSES, index=pl.STATUSES.index(task.status) if task else 0, key=f"{key}_status")
    notes = st.text_area("Заметки (необязательно)", value=task.notes if task else "", max_chars=pl.MAX_NOTES_LEN, height=80, key=f"{key}_notes")

    if status != pl.DONE and isinstance(deadline, date) and deadline < start:
        st.warning(
            f"Срок раньше даты начала планирования ({pl.format_date(start)}). Задача будет сохранена "
            "как просроченная и не попадёт в автоматический план, пока срок не будет пересмотрен."
        )

    if st.button("Сохранить", type="primary", key=f"{key}_save"):
        values = dict(title=title, subject=subject, deadline=deadline, remaining_minutes=int(remaining),
                      priority=priority, status=status, notes=notes)
        try:
            if task:
                db.update_task(task.id, **values)
                flash("success", f"Задача «{title.strip()}» обновлена.")
            else:
                db.add_task(**values)
                flash("success", f"Задача «{title.strip()}» добавлена.")
        except db.ValidationError as exc:
            for err in exc.errors:
                st.error(err)
        else:
            data_changed()
            st.rerun()


@st.dialog("Новая задача", width="medium")
def new_task_dialog() -> None:
    task_form(None)


@st.dialog("Изменить задачу", width="medium")
def edit_task_dialog(task: Task) -> None:
    task_form(task)


def load_demo() -> None:
    added = db.add_demo_tasks(pl.demo_tasks(st.session_state["start_date"]))
    if added:
        flash("success", f"Добавлено демонстрационных задач: {added}. Они отмечены префиксом «[Демо]».")
        data_changed()
    else:
        flash("info", "Демонстрационные задачи уже загружены — повторно они не добавляются.")


# ---------- Вкладка «Задачи» ----------

def render_summary(tasks: list[Task], start: date) -> None:
    s = pl.summarize(tasks, start)
    with st.container(border=True, key="card_summary"):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Активные задачи", s["active"])
        c2.metric("Просрочено", s["overdue"])
        c3.metric("Выполнено", s["done"])
        c4.metric("Осталось часов", pl.format_hours(s["remaining_minutes"]))


def styled_task_table(df: pd.DataFrame):
    def status_style(value: str) -> str:
        if "просрочено" in value:
            return f"color: {COLOR_ERROR}; font-weight: 600"
        return f"color: {STATUS_COLORS.get(value, COLOR_TEXT)}"

    def priority_style(value: str) -> str:
        return f"color: {PRIORITY_COLORS.get(value, COLOR_TEXT)}"

    return df.style.map(status_style, subset=["Статус"]).map(priority_style, subset=["Приоритет"])


def render_task_actions(task: Task, start: date) -> None:
    with st.container(border=True, key="card_actions"):
        st.markdown(f"**{task.title}**")
        meta = [task.subject or "Без предмета", f"срок {pl.format_date(task.deadline)}",
                f"осталось {pl.format_minutes(task.remaining_minutes)}", task.priority.lower() + " приоритет", task.status.lower()]
        st.caption(" · ".join(meta))
        if pl.is_overdue(task, start):
            st.markdown(":red[**Просрочено.** Срок раньше даты начала планирования — пересмотрите его.]")
        if task.notes:
            st.caption(task.notes)

        if st.session_state["confirm_delete"] == task.id:
            st.warning(f"Удалить задачу «{task.title}»? Действие нельзя отменить.")
            with st.container(horizontal=True):
                if st.button("Да, удалить", key="confirm_delete_yes"):
                    db.delete_task(task.id)
                    flash("success", f"Задача «{task.title}» удалена.")
                    data_changed()
                    st.rerun()
                if st.button("Отмена", key="confirm_delete_no"):
                    st.session_state["confirm_delete"] = None
                    st.rerun()
            return

        with st.container(horizontal=True, vertical_alignment="bottom"):
            if not task.is_done:
                new_remaining = st.number_input(
                    "Осталось работы, мин", min_value=1, max_value=pl.MAX_EFFORT_MINUTES, step=15,
                    value=max(task.remaining_minutes, 1), key=f"quick_remaining_{task.id}", width=200,
                )
                if st.button("Обновить остаток", key=f"quick_save_{task.id}"):
                    db.update_task(task.id, task.title, task.subject, task.deadline, int(new_remaining),
                                   task.priority, task.status, task.notes)
                    flash("success", f"Остаток по задаче «{task.title}»: {pl.format_minutes(int(new_remaining))}.")
                    data_changed()
                    st.rerun()
                if st.button("Отметить выполненной", key=f"done_{task.id}"):
                    db.mark_done(task.id)
                    flash("success", f"Задача «{task.title}» выполнена и сохранена в истории.")
                    data_changed()
                    st.rerun()
            if st.button("Изменить", key=f"edit_{task.id}"):
                edit_task_dialog(task)
            if st.button("Удалить", key=f"delete_{task.id}"):
                st.session_state["confirm_delete"] = task.id
                st.rerun()


def render_tasks_tab(tasks: list[Task], start: date) -> None:
    render_summary(tasks, start)
    st.caption(
        f"«Просрочено» — невыполненные задачи со сроком раньше даты начала планирования "
        f"({pl.format_date(start)}). Дату можно изменить на вкладке «План на неделю»."
    )

    if not tasks:
        with st.container(border=True, key="card_empty"):
            st.markdown("**Задач пока нет**")
            st.caption("Добавьте первую учебную задачу или загрузите демонстрационный набор, чтобы посмотреть, как работает планирование.")
            with st.container(horizontal=True):
                if st.button("Добавить первую задачу", type="primary", key="empty_add"):
                    new_task_dialog()
                if st.button("Загрузить демонстрационные задачи", key="empty_demo"):
                    load_demo()
                    st.rerun()
        return

    with st.container(horizontal=True, vertical_alignment="center"):
        if st.button("Добавить задачу", type="primary", key="add_task"):
            new_task_dialog()
        if st.button("Загрузить демонстрационные задачи", key="load_demo"):
            load_demo()
            st.rerun()
        st.download_button(
            "Скачать задачи (CSV)", data=to_csv_bytes(tasks_export_df(tasks, start)),
            file_name=f"smartplan_tasks_{date.today().isoformat()}.csv", mime="text/csv",
            on_click="ignore", key="export_tasks",
        )

    subjects = sorted({t.subject or "Без предмета" for t in tasks})
    f1, f2, f3, f4 = st.columns([1.3, 1, 1, 1.1])
    subject_filter = f1.multiselect("Предмет", subjects, placeholder="Все предметы")
    status_filter = f2.multiselect("Статус", pl.STATUSES, placeholder="Все статусы")
    priority_filter = f3.multiselect("Приоритет", pl.PRIORITIES, placeholder="Все приоритеты")
    sort_by = f4.selectbox("Сортировка", SORT_OPTIONS)

    visible = [
        t for t in tasks
        if (not subject_filter or (t.subject or "Без предмета") in subject_filter)
        and (not status_filter or t.status in status_filter)
        and (not priority_filter or t.priority in priority_filter)
    ]
    if sort_by == SORT_OPTIONS[0]:
        visible.sort(key=pl.sort_key)
    else:
        visible.sort(key=lambda t: (pl.PRIORITY_RANK[t.priority], t.deadline, t.id))

    if not visible:
        st.info("Нет задач, соответствующих выбранным фильтрам.")
        return

    df = pd.DataFrame(
        [
            {
                "ID": t.id,
                "Задача": t.title,
                "Предмет": t.subject or "—",
                "Срок": t.deadline,
                "Осталось": pl.format_minutes(t.remaining_minutes) if not t.is_done else "—",
                "Приоритет": t.priority,
                "Статус": t.status + (" · просрочено" if pl.is_overdue(t, start) else ""),
            }
            for t in visible
        ]
    )
    table_key = f"task_table_{st.session_state['table_version']}_{hash((tuple(subject_filter), tuple(status_filter), tuple(priority_filter), sort_by))}"
    event = st.dataframe(
        styled_task_table(df),
        hide_index=True,
        column_order=["Задача", "Предмет", "Срок", "Осталось", "Приоритет", "Статус"],
        column_config={
            "Задача": st.column_config.TextColumn(width="large"),
            "Срок": st.column_config.DateColumn(format="DD.MM.YYYY"),
        },
        on_select="rerun",
        selection_mode="single-row",
        key=table_key,
    )
    st.caption(f"Показано задач: {len(visible)} из {len(tasks)}. Выберите строку, чтобы изменить, завершить или удалить задачу.")

    rows = event.selection.rows if event is not None else []
    if rows and rows[0] < len(df):
        selected = next((t for t in visible if t.id == int(df.iloc[rows[0]]["ID"])), None)
        if selected:
            render_task_actions(selected, start)


# ---------- Вкладка «План на неделю» ----------

def workload_chart(plan: pl.Plan) -> go.Figure:
    labels = [pl.format_day_label(d.day) for d in plan.days]
    fig = go.Figure()
    fig.add_bar(
        x=labels, y=[d.capacity / 60 for d in plan.days], name="Доступно",
        marker_color="#D5DAE1", width=0.62,
        customdata=[pl.format_minutes(d.capacity) for d in plan.days],
        hovertemplate="%{x}<br>Доступно: %{customdata}<extra></extra>",
    )
    fig.add_bar(
        x=labels, y=[d.used / 60 for d in plan.days], name="Запланировано",
        marker_color="#5873D6", width=0.36,
        customdata=[pl.format_minutes(d.used) for d in plan.days],
        hovertemplate="%{x}<br>Запланировано: %{customdata}<extra></extra>",
    )
    fig.update_layout(
        title=dict(text="Учебная нагрузка на неделю", font=dict(size=16, color=COLOR_TEXT), x=0, xanchor="left"),
        barmode="overlay", height=320, margin=dict(l=8, r=8, t=56, b=8),
        paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
        font=dict(family="system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif", size=13, color=COLOR_TEXT),
        legend=dict(orientation="h", x=1, xanchor="right", y=1.12, yanchor="bottom", font=dict(size=12)),
        hoverlabel=dict(bgcolor="#FFFFFF", font_size=13),
    )
    fig.update_yaxes(title_text="Часы", gridcolor="#EEF0F3", zeroline=False, rangemode="tozero",
                     tickformat=",.1~f", separatethousands=False)
    fig.update_xaxes(showgrid=False, linecolor=COLOR_BORDER)
    fig.update_layout(separators=", ")
    return fig


def daily_table_html(plan: pl.Plan) -> str:
    rows: list[str] = []
    for day in plan.days:
        date_cell = f"{pl.WEEKDAYS_SHORT[day.day.weekday()]}, {pl.format_date(day.day)}"
        total = pl.format_minutes(day.used)
        capacity = pl.format_minutes(day.capacity) if day.capacity else "день отдыха"
        items = day.allocations or [None]
        span = len(items)
        for i, a in enumerate(items):
            cells = []
            if i == 0:
                cells.append(f'<td class="day" rowspan="{span}">{date_cell}</td>')
            if a is None:
                note = "Нет доступного времени" if day.capacity == 0 else "Свободное время не занято"
                cells.append(f'<td colspan="2" class="meta">{note}</td><td class="num meta">—</td>')
            else:
                cells.append(
                    f"<td>{html.escape(a.subject or '—')}</td><td>{html.escape(a.title)}</td>"
                    f'<td class="num">{pl.format_minutes(a.minutes)}</td>'
                )
            if i == 0:
                cells.append(f'<td class="num" rowspan="{span}">{total}</td>')
                cells.append(f'<td class="num meta" rowspan="{span}">{capacity}</td>')
            rows.append("<tr>" + "".join(cells) + "</tr>")
    head = (
        "<tr><th>Дата</th><th>Предмет</th><th>Задача</th><th class='num'>Время</th>"
        "<th class='num'>Итого за день</th><th class='num'>Доступно</th></tr>"
    )
    return f'<table class="sp-table"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table>'


def outcomes_df(plan: pl.Plan) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Порядок": i,
                "Задача": o.task.title,
                "Предмет": o.task.subject or "—",
                "Срок": o.task.deadline,
                "Приоритет": o.task.priority,
                "Нужно": pl.format_minutes(o.task.remaining_minutes),
                "Запланировано": pl.format_minutes(o.scheduled_minutes),
                "Не распределено": pl.format_minutes(o.unscheduled_minutes) if o.unscheduled_minutes else "—",
                "Итог": o.label,
            }
            for i, o in enumerate(plan.outcomes, start=1)
        ]
    )


def render_plan_result(plan: pl.Plan) -> None:
    if not plan.outcomes and not plan.overdue:
        st.info("Нет невыполненных задач для планирования. Добавьте задачи на вкладке «Задачи».")
        return

    risks = plan.risk_outcomes
    if risks:
        lines = "\n".join(
            f"- {o.task.title} (срок {pl.format_date(o.task.deadline)}): не хватает {pl.format_minutes(o.unscheduled_minutes)}"
            for o in risks
        )
        st.warning(
            "**Не хватает времени до срока.** План составлен без превышения доступного времени и без работы "
            f"после сроков, но часть работы распределить не удалось:\n\n{lines}\n\n"
            "Увеличьте доступное время, пересмотрите сроки или оценку трудоёмкости."
        )
    elif plan.outcomes:
        st.success(
            "Составлен выполнимый эвристический план: доступное время по дням не превышено, работа не назначена "
            "после сроков, задачи со сроком в пределах недели распределены полностью."
        )

    beyond = [o for o in plan.outcomes if o.kind == pl.OUTCOME_BEYOND_HORIZON]
    if beyond:
        lines = "\n".join(
            f"- {o.task.title} (срок {pl.format_date(o.task.deadline)}): остаток {pl.format_minutes(o.unscheduled_minutes)}"
            for o in beyond
        )
        st.info(
            f"**Остаток за пределами текущего плана.** Срок этих задач позже {pl.format_date(plan.end)}, "
            f"поэтому оставшаяся работа перенесётся в следующий план. Это не означает срыв срока.\n\n{lines}"
        )

    if plan.overdue:
        lines = "\n".join(
            f"- {t.title} (срок {pl.format_date(t.deadline)}, осталось {pl.format_minutes(t.remaining_minutes)})"
            for t in plan.overdue
        )
        st.error(
            "**Просроченные задачи не включены в план.** Пересмотрите их сроки на вкладке «Задачи»:\n\n" + lines
        )

    with st.container(border=True, key="card_plan_metrics"):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Запланировано, ч", pl.format_hours(plan.total_scheduled))
        c2.metric("Доступно, ч", pl.format_hours(plan.total_capacity))
        c3.metric("Не хватает до срока, ч", pl.format_hours(plan.deadline_risk_minutes))
        c4.metric("За пределами плана, ч", pl.format_hours(plan.beyond_horizon_minutes))

    with st.container(border=True, key="card_chart"):
        st.plotly_chart(workload_chart(plan), config={"displayModeBar": False}, width="stretch")

    st.markdown("### Распределение по дням")
    st.markdown(daily_table_html(plan), unsafe_allow_html=True)

    if plan.outcomes:
        st.markdown("### Итог по задачам")
        st.caption("Задачи перечислены в порядке распределения: ближайший срок, затем приоритет.")
        st.dataframe(
            outcomes_df(plan), hide_index=True,
            column_config={"Порядок": st.column_config.NumberColumn("№", width=40),
                           "Срок": st.column_config.DateColumn(format="DD.MM.YYYY", width=90),
                           "Задача": st.column_config.TextColumn(width=250),
                           "Предмет": st.column_config.TextColumn(width=150),
                           "Приоритет": st.column_config.TextColumn(width=80),
                           "Нужно": st.column_config.TextColumn(width=85),
                           "Запланировано": st.column_config.TextColumn(width=105),
                           "Не распределено": st.column_config.TextColumn(width=115),
                           "Итог": st.column_config.TextColumn(width=230)},
        )

    st.download_button(
        "Скачать план (CSV)", data=to_csv_bytes(plan_export_df(plan)),
        file_name=f"smartplan_plan_{plan.start.isoformat()}.csv", mime="text/csv",
        on_click="ignore", key="export_plan", disabled=not plan.allocations,
    )


def render_plan_tab(tasks: list[Task]) -> None:
    with st.container(border=True, key="card_params"):
        st.markdown("### Параметры планирования")
        c_start, c_hint = st.columns([1, 3], vertical_alignment="bottom")
        c_start.date_input("Дата начала планирования", key="start_date", format="DD.MM.YYYY")
        start: date = st.session_state["start_date"]
        c_hint.caption("Доступное время указывается в минутах для каждого из семи дней. 0 — день отдыха.")

        cols = st.columns(pl.HORIZON_DAYS)
        for i, col in enumerate(cols):
            col.number_input(
                pl.format_day_label(start + timedelta(days=i)), min_value=0, max_value=pl.MAX_DAILY_CAPACITY,
                step=15, key=f"cap_{i}", on_change=save_capacities,
            )
        caps = capacities()

        with st.container(horizontal=True, vertical_alignment="center"):
            build = st.button("Составить план", type="primary", key="build_plan")
            st.caption(f"Доступно за неделю: {pl.format_minutes(sum(caps))}. {ALGORITHM_TEXT}")

    fingerprint = pl.plan_fingerprint(tasks, start, caps)
    if build:
        st.session_state["plan"] = pl.build_plan(tasks, start, caps)
        st.session_state["plan_fp"] = fingerprint

    plan: pl.Plan | None = st.session_state.get("plan")
    if plan is None:
        if tasks:
            st.info("План ещё не составлен. Проверьте доступное время и нажмите «Составить план».")
        else:
            st.info("Задач пока нет. Добавьте их на вкладке «Задачи» или загрузите демонстрационные задачи, затем составьте план.")
        return
    if st.session_state.get("plan_fp") != fingerprint:
        st.warning("Задачи, дата начала или доступное время изменились после составления плана. "
                   "Нажмите «Составить план», чтобы получить актуальный план.")
        return
    render_plan_result(plan)


# ---------- Вкладка «О проекте» ----------

ABOUT_MD = f"""
### Цель
Помочь студентам учитывать сроки учебных заданий и распределять нагрузку с учётом доступного времени.

### Гипотеза исследования
Автоматическое распределение задач может упростить составление учебного плана и помочь заранее
обнаруживать нехватку времени.

### Как работает планирование
SmartPlan использует прозрачный детерминированный алгоритм автоматического распределения
(эвристику), а не машинное обучение.

1. Отбираются невыполненные задачи.
2. Просроченные задачи (срок раньше даты начала) не планируются и выводятся отдельно с просьбой пересмотреть срок.
3. Остальные задачи сортируются: ближайший срок → более высокий приоритет → номер задачи.
4. Оставшееся время каждой задачи распределяется по дням, начиная с самого раннего дня со свободным временем.
5. Работа не назначается после срока (день срока включается) и не превышает доступное время дня.
6. Задача может быть разделена между несколькими днями.
7. Нераспределённое время показывается явно: «не хватает времени до срока», если срок внутри недели,
   или «остаток за пределами текущего плана», если срок позже.

{ALGORITHM_TEXT} Результат — выполнимый эвристический план, а не математически оптимальное расписание.

### Технологии
Python, Streamlit (интерфейс), SQLite (хранение данных), Plotly (график нагрузки), pytest (проверка алгоритма и хранения).
Приложение работает локально, без платных сервисов и внешних моделей ИИ.

### Практическая польза
- Все учебные задачи и сроки собраны в одном месте.
- Видно, сколько времени осталось на невыполненную работу.
- Нехватка времени до срока обнаруживается заранее, пока ещё можно изменить план.
- План и список задач можно выгрузить в CSV для Excel.

### Ограничения
- Оценки трудоёмкости вводит пользователь; качество плана зависит от их точности.
- Алгоритм не учитывает усталость, сложность переключения и зависимости между задачами.
- Система не гарантирует выполнение задач и не гарантирует улучшение успеваемости.
- Планировщик использует правила распределения, а не обученную модель ИИ.
- Влияние на пользователей пока не исследовалось экспериментально.
"""


# ---------- Страница ----------

def main() -> None:
    init_state()
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="sp-header"><span class="sp-title">SmartPlan</span>'
        '<span class="sp-subtitle">Учебные задачи и планирование нагрузки</span></div>',
        unsafe_allow_html=True,
    )
    show_flash()

    try:
        tasks = db.list_tasks()
    except Exception as exc:  # повреждённая или недоступная база данных
        st.error(f"Не удалось прочитать базу данных: {exc}")
        st.stop()

    tab_tasks, tab_plan, tab_about = st.tabs(["Задачи", "План на неделю", "О проекте"])
    with tab_tasks:
        render_tasks_tab(tasks, st.session_state["start_date"])
    with tab_plan:
        render_plan_tab(tasks)
    with tab_about:
        st.markdown(ABOUT_MD)


main()
