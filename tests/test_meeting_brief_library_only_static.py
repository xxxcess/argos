"""Static guards for Argos Venture Audio Capture and Meeting Brief."""

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


def test_audio_capture_is_not_a_home_launcher_or_browser_tab():
    storage = read("static/js/storage.js")
    capture = read("static/js/meetingCaptureWorkspace.js")
    assert "./meetingCaptureWorkspace.js" in storage
    assert "meetingBrief.js" not in storage
    assert "meetingCaptureLibraryHandoff.js" not in storage
    assert "window.open(" not in capture
    assert "workspace-tab meeting-capture-tab" in capture
    assert "MutationObserver" not in capture


def test_new_tab_wizard_offers_audio_capture():
    source = read("static/js/meetingCaptureWorkspace.js")
    assert 'value="audio_capture"' in source
    assert "Audio capture" in source
    assert "Open Audio Capture" in source


def test_capture_creation_is_inert_until_explicit_start():
    source = read("static/js/meetingCaptureWorkspace.js")
    open_capture = source.split("function openWorkspaceCapture", 1)[1].split("function browserRecognitionSupported", 1)[0]
    start_capture = source.split("async function startCapture", 1)[1].split("async function resumeCapture", 1)[0]
    assert "phase: 'idle'" in source
    assert "getUserMedia" not in open_capture
    assert source.count("getUserMedia") == 1
    assert "getUserMedia" in start_capture
    assert "Start capture" in source


def test_exports_are_library_markdown_and_open_documents_after_stop():
    capture = read("static/js/meetingCaptureWorkspace.js")
    assert "'/api/document'" in capture
    assert "language: 'markdown'" in capture
    assert "Export transcript" in capture
    assert "Generate & export brief" in capture
    assert "loadDocument" in capture
    assert "openPanel" in capture
    assert "deactivateCapture();" in capture
    assert "Stop capture and wait for final transcription before exporting." in capture


def test_legacy_capture_modules_are_removed():
    assert not (ROOT / "static/js/meetingBrief.js").exists()
    assert not (ROOT / "static/js/meetingBriefLibraryExport.js").exists()
    assert not (ROOT / "static/js/liveMeetingCapture.js").exists()
    assert not (ROOT / "static/js/meetingCaptureLibraryHandoff.js").exists()
