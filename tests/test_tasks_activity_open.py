from pathlib import Path


TASKS_JS = Path("static/js/tasks.js")


def _slice(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_tasks_exposes_activity_opener_and_focuses_run_rows():
    source = TASKS_JS.read_text(encoding="utf-8")

    mapper = _slice(
        source,
        "function _activityEntryFromRun",
        "function _activityFocusMatches",
    )
    assert "runId: r.id || ''" in mapper

    render_entry = _slice(
        source,
        "function _renderActivityEntry(entry)",
        "function _escHtml",
    )
    assert 'data-run-id="${_escHtml(entry.runId || \'\')}"' in render_entry
    assert 'data-task-id="${_escHtml(entry.taskId || \'\')}"' in render_entry
    assert 'data-ts="${_escHtml(entry.ts || \'\')}"' in render_entry

    opener = _slice(
        source,
        "export function openActivity",
        "export function closeTasks",
    )
    assert "_pendingActivityFocus = activityFocus || null;" in opener
    assert "openTasks(null, { tab: 'activity'" in opener
    assert "openActivity" in source[source.index("const tasksModule"):]
