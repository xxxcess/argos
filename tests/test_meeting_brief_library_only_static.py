"""Static guards for Argos Venture Meeting Brief persistence."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_meeting_brief_api_only_generates_markdown():
    source = read("routes/meeting_brief_routes.py")
    ast.parse(source)
    assert '"/generate"' in source
    assert "save-to-notes" not in source
    assert "SaveMeetingBriefRequest" not in source
    assert "from core.database import Note" not in source
    assert "SessionLocal" not in source


def test_meeting_brief_window_has_no_notes_export_path():
    source = read("static/js/meetingBrief.js")
    assert "Save to Notes" not in source
    assert "save-to-notes" not in source
    assert "meeting-brief-save" not in source


def test_completed_briefs_export_as_library_markdown_documents():
    source = read("static/js/meetingBriefLibraryExport.js")
    assert "'/api/document'" in source
    assert "language: 'markdown'" in source
    assert "Export brief to Library" in source
    assert "Generate & export brief" in source
