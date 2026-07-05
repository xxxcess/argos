# Argos Venture Live Meeting Capture

## User flow

1. Open **Meeting Brief** from the Venture Home tab.
2. Select **Capture live meeting** to open a dedicated tab.
3. Enter a title, explicitly confirm participant consent, and start microphone capture.
4. Review timestamped live transcript entries as they arrive.
5. Select **Export transcript** to create a private Markdown document in the user's Library.
6. Select **Review in Meeting Brief** to return the transcript to the parent Meeting Brief workflow for AI synthesis and Notes storage.

## Architecture

The tab uses browser microphone capture only. It does not capture system audio.

- With browser STT configured, `SpeechRecognition` provides incremental final and interim text.
- With local or OpenAI-compatible endpoint STT configured, the browser creates a new self-contained WebM segment every 10 seconds and posts it to Argos's existing `/api/stt/transcribe` endpoint.
- Each final segment is timestamped in the transcript, allowing the Meeting Brief synthesis to reference the evidence it used.
- Export calls the existing `/api/document` endpoint with no `session_id`, creating an owner-scoped Library document rather than a new meeting data store.
- A same-origin `BroadcastChannel`, with `window.postMessage` as a same-origin fallback, hands the transcript back to Meeting Brief.

## Summary shape

The brief follows a standard meeting-notes structure inspired by Meetily:

- Summary
- Key Decisions
- Action Items with Owner, Task, Due, Transcript Reference, and Timestamp
- Discussion Highlights

Argos additionally requires Open Questions & Uncertainty, and treats owners,
due dates, decisions, and timestamps as unknown unless supported by the
transcript.

## Constraints

- The browser requests microphone access only after the user explicitly starts capture.
- The user must affirm consent before starting.
- Audio is not persistently stored by this feature; it is sent only as short
  segments to the selected Argos STT provider when server-side STT is in use.
- This is not a substitute for future native system-audio capture, speaker
  diarization, or calendar/provider recording imports.
