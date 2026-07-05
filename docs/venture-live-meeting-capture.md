# Argos Venture Live Capture Tab

## Entry point

**Live Capture** is a choice in the Venture **New Tab** wizard. Selecting it
opens a dedicated tab in the Argos workspace tab strip, alongside chat and
Quest tabs. It does not open a browser window or add a Home sidebar tool.

The tab is idle when created. Pressing **Start recording** is the sole action
that requests microphone permission; the browser supplies the standard allow or
block prompt.

## Recorder controls

The recorder footer uses two rows:

- Elapsed time and microphone waveform are above the controls.
- Start recording / Resume recording, Pause, Stop, transcript export, and brief
  export are on the control row.

The Home sidebar, collapse button, hamburger control, and other Home-only tools
are hidden while Live Capture is active.

## Persisted transcript session

Live Capture uses Argos's existing persisted session and chat-message store.
Each finalized timestamped transcript segment is a user message in a session
whose type is `live_capture`. This means the transcript survives tab switches,
reloads, and later review like a regular Argos conversation.

- Local or endpoint Speech-to-Text receives microphone segments every 15 seconds
  through `/api/stt/transcribe`.
- Each server-transcribed segment is timestamped from its audio segment start,
  rather than from the later transcription callback. This prevents repeated
  timestamps when final segments return after Stop.
- Browser STT uses `SpeechRecognition` when configured.
- Old, stopped, closed, or superseded recording runs cannot append late results
  into the active transcript.

Switching away from an active Live Capture tab stops recording; it does not
continue in the background.

## Transcript and export flow

Stop recording and wait for final transcription before exporting.

- **Export transcript** writes `<Meeting Title> — Transcript` as an
  owner-scoped Markdown document and opens it in the existing document editor
  panel beside the still-open Live Capture tab.
- **Generate & export brief** sends the persisted timestamped transcript to the
  configured Utility model, writes `<Meeting Title> — Meeting Brief`, and opens
  it in the same side panel. Transient provider 429 responses receive one bounded
  server retry before Argos reports the limit.

No output is exported to Notes. Raw microphone audio is not persisted.

## Brief structure

The generated brief contains Summary, Key Decisions, timestamp-aware Action
Items, Discussion Highlights, and Open Questions & Uncertainty. Argo does not
fabricate owners, due dates, decisions, timestamps, or outcomes absent from the
transcript.

## Boundaries

- Microphone only; no system-audio capture.
- One active capture at a time.
- No automatic recording, speaker diarization, calendar-provider recording
  imports, or raw-audio persistence in this phase.
