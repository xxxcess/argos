import json
from types import SimpleNamespace

from src.venture_bible_refresh_guard import (
    is_book_scoped_job,
    is_obsolete_unscoped_bible_job,
)


def _job(scope, *, status="queued", error_code=None):
    return SimpleNamespace(
        scope_json=json.dumps(scope),
        status=status,
        error_code=error_code,
    )


def test_book_import_jobs_are_not_reclassified_as_generic_refreshes():
    job = _job({
        "kind": "bible_book_import",
        "book_id": "MAT",
        "selection_id": "selection-1",
    })
    assert is_book_scoped_job(job)
    assert not is_obsolete_unscoped_bible_job(job)


def test_legacy_generic_bible_refresh_is_repaired_before_execution():
    job = _job({}, status="queued")
    assert not is_book_scoped_job(job)
    assert is_obsolete_unscoped_bible_job(job)


def test_failed_scope_error_is_repaired_only_when_no_book_scope_exists():
    invalid = _job({}, status="failed", error_code="bible_scope_invalid")
    valid = _job(
        {"kind": "bible_book_import", "book_id": "MAT", "selection_id": "selection-1"},
        status="failed",
        error_code="bible_scope_invalid",
    )
    assert is_obsolete_unscoped_bible_job(invalid)
    assert not is_obsolete_unscoped_bible_job(valid)
