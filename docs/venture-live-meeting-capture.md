# Argos Venture Live Meeting Capture

## User flow

1. Open **Meeting Brief** from the Venture Home tab.
2. Select **Capture live meeting** to open a dedicated tab.
3. Enter a title, explicitly confirm participant consent, and start microphone capture.
4. Review timestamped live transcript entries as they arrive.
5. Choose one or both explicit Library exports:
   - **Export transcript** creates `<Meeting Title> — Transcript`.
   - **Generate & export brief** creates `<Meeting Title> — Meeting Brief`.
6. Select **Review in Meeting Brief** to send the transcript back to the parent
   window for optional synthesis and Library export.

## Architecture

The tab uses browser microphone capture only. It does not capture system audio.

- Browser STT uses `SpeechRecognition` for incremental final and interim text.
- Local or endpoint STT receives self-contained WebM segments through the
  existing `/api/stt/transcribe` route.
- Final transcript segments have relative timestamps, which the brief generator
  can cite when present.
- Both transcript and brief export use the existing `/api/document` endpoint
  with no `session_id`, creating owner-scoped Markdown documents in the user's
  Library.
- A same-origin `BroadcastChannel`, with `window.postMessage` fallback, hands
  the transcript back to Meeting Brief.

## Summary shape

The generated brief follows a standard meeting-record structure inspired by
Meetily:

- Summary
- Key Decisions
- Action Items with Owner, Task, Due, Transcript Reference, and Timestamp
- Discussion Highlights
- Open Questions & Uncertainty

Argos treats owners, due dates, decisions, and timestamps as unknown unless the
transcript supports them.

## Constraints

- The browser requests microphone access only after the user explicitly starts capture.
- The user must affirm consent before starting.
- Audio is not persistently stored by this feature; short segments are sent only
  to the selected Argos STT provider when server-side STT is active.
- No output is exported to Notes. User-requested outputs are Markdown documents
  in the existing Library.
- This is not a substitute for future native system-audio capture, speaker
  diarization, or calendar/provider recording imports.
