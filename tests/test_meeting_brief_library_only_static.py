"""Static guards for Argos Venture Live Capture and Meeting Brief."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_meeting_brief_remains_library_only_and_retries_transient_rate_limits():
    source = read("routes/meeting_brief_routes.py")
    ast.parse(source)
    assert '"/generate"' in source
    assert "save-to-notes" not in source
    assert "SaveMeetingBriefRequest" not in source
    assert "from core.database import Note" not in source
    assert "exc.status_code == 429" in source
    assert "await asyncio.sleep(2)" in source


def test_live_capture_is_native_workspace_surface_not_browser_tab():
    storage = read("static/js/storage.js")
    capture = read("static/js/meetingCaptureWorkspace.js")
    assert "./meetingCaptureWorkspace.js" in storage
    assert "meetingBrief.js" not in storage
    assert "window.open(" not in capture
    assert "workspace-tab live-capture-tab" in capture
    assert "MutationObserver" not in capture
    assert 'value="live_capture"' in capture
    assert "Open Live Capture" in capture
    assert "Audio capture" not in capture


def test_live_capture_creation_is_inert_until_start_recording():
    source = read("static/js/meetingCaptureWorkspace.js")
    open_capture = source.split("function openWorkspaceCapture", 1)[1].split("function browserRecognitionSupported", 1)[0]
    start_capture = source.split("async function startCapture", 1)[1].split("async function resumeCapture", 1)[0]
    assert "phase: 'idle'" in source
    assert "getUserMedia" not in open_capture
    assert "getUserMedia" in start_capture
    assert "live-capture-consent" not in source
    assert "consent" not in source.lower()
    assert "Start recording" in source


def test_live_capture_segments_are_persisted_as_timestamped_chat_messages():
    frontend = read("static/js/meetingCaptureWorkspace.js")
    backend = read("routes/meeting_brief_routes.py")
    assert "LIVE_CAPTURE_ENDPOINT" in frontend
    assert "persistSegment" in frontend
    assert "SEGMENT_MS = 15_000" in frontend
    assert "startOffsetMs" in frontend
    assert "addSegment(capture, segment.runId, segment.startOffsetMs" in frontend
    assert 'LIVE_CAPTURE_MODE = "live_capture"' in backend
    assert '"/live-captures"' in backend
    assert '"/live-captures/{session_id}/segments"' in backend
    assert 'ChatMessage("user", content' in backend
    assert '"kind": "live_capture_segment"' in backend


def test_late_callbacks_cannot_append_to_stopped_or_closed_capture():
    source = read("static/js/meetingCaptureWorkspace.js")
    assert "function isCurrentRun" in source
    assert "capture.closed" in source
    assert "capture.activeRunId" in source
    assert "allowStopping: true" in source
    assert "capture.phase === 'stopped'" in source


def test_live_capture_layout_is_single_panel_with_home_chrome_hidden():
    source = read("static/js/meetingCaptureWorkspace.js")
    assert "#sidebar-toggle" in source
    assert "#sidebar-collapse" in source
    assert "setHomeOnlyControlsHidden(true)" in source
    assert "live-capture-control-row" in source
    assert "live-capture-readout" in source
    assert "Capture settings" not in source
    assert "live-capture-details" not in source


def test_live_capture_document_dock_stays_visible_and_reserves_its_rendered_width():
    storage = read("static/js/storage.js")
    dock = read("static/js/liveCaptureDocumentDock.js")
    assert "./liveCaptureDocumentDock.js" in storage
    assert "#chat-container" in dock
    assert "visibility: visible !important" in dock
    assert "#doc-editor-pane" in dock
    assert "#doc-divider" in dock
    assert "ResizeObserver" in dock
    assert "paneRect.left" in dock
    assert "root.style.setProperty('right'" in dock
    assert "attributeFilter: ['class']" in dock
    assert "attributeFilter: ['class', 'style']" not in dock


def test_exports_are_library_markdown_and_keep_capture_open():
    source = read("static/js/meetingCaptureWorkspace.js")
    handoff = source.split("async function openLibraryDocument", 1)[1].split("async function ensureSegmentsPersisted", 1)[0]
    assert "'/api/document'" in source
    assert "language: 'markdown'" in source
    assert "Export transcript" in source
    assert "Generate & export brief" in source
    assert "loadDocument" in source
    assert "openPanel" in source
    assert "documentPaneOffset" in handoff
    assert "deactivateCapture" not in handoff
    assert "Stop recording and wait for final transcription before exporting." in source


def test_legacy_capture_modules_are_removed():
    assert not (ROOT / "static/js/meetingBrief.js").exists()
    assert not (ROOT / "static/js/meetingBriefLibraryExport.js").exists()
    assert not (ROOT / "static/js/liveMeetingCapture.js").exists()
    assert not (ROOT / "static/js/meetingCaptureLibraryHandoff.js").exists()
