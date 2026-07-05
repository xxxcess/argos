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


def test_transcripts_are_scoped_to_the_current_recording_run():
    source = read("static/js/meetingCaptureWorkspace.js")
    assert "runId" in source
    assert "activeRunId" in source
    assert "function isCurrentRun" in source
    assert "appendTranscript(capture, runId" in source
    assert "isCurrentRun(capture, segment.runId, { allowStopping: true })" in source
    assert "capture.phase === 'stopped'" in source


def test_capture_layout_hides_home_only_chrome_and_header_overlay():
    source = read("static/js/meetingCaptureWorkspace.js")
    assert "#sidebar-toggle" in source
    assert "#sidebar-collapse" in source
    assert "setHomeOnlyControlsHidden(true)" in source
    assert "Live Meeting Transcript" not in source
    assert "meeting-capture-control-row" in source
    assert "meeting-recorder-readout" in source


def test_exports_are_library_markdown_and_keep_capture_open():
    source = read("static/js/meetingCaptureWorkspace.js")
    handoff = source.split("async function openLibraryDocument", 1)[1].split("async function exportTranscript", 1)[0]
    assert "'/api/document'" in source
    assert "language: 'markdown'" in source
    assert "Export transcript" in source
    assert "Generate & export brief" in source
    assert "loadDocument" in source
    assert "openPanel" in source
    assert "documentPaneOffset" in handoff
    assert "deactivateCapture" not in handoff
    assert "Stop capture and wait for final transcription before exporting." in source


def test_legacy_capture_modules_are_removed():
    assert not (ROOT / "static/js/meetingBrief.js").exists()
    assert not (ROOT / "static/js/meetingBriefLibraryExport.js").exists()
    assert not (ROOT / "static/js/liveMeetingCapture.js").exists()
    assert not (ROOT / "static/js/meetingCaptureLibraryHandoff.js").exists()
