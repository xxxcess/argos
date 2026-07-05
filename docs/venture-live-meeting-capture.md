# Argos Venture Audio Capture Tab

## Entry point

Audio Capture is a choice in the Venture **New Tab** wizard. Selecting it opens
a dedicated tab inside the Argos workspace tab strip, alongside chat and Quest
tabs. It never opens a browser window.

## Recorder controls

The tab provides traditional recorder controls at the bottom of the workspace:

- Start capture / Resume capture
- Pause
- Stop
- Elapsed time
- Live microphone waveform
- Export transcript
- Generate & export brief

The user enters a title and must affirm they have authority and participant
consent before the browser requests microphone access.

## Transcript and export flow

Browser STT uses `SpeechRecognition` when configured. Local or endpoint STT
receives self-contained microphone segments through Argos's existing
`/api/stt/transcribe` route. Final transcript entries include a relative
`[MM:SS]` marker.

Exports are explicit and Library-only:

- **Export transcript** writes an owner-scoped Markdown document titled
  `<Meeting Title> — Transcript`.
- **Generate & export brief** uses the existing Meeting Brief generation route,
  writes `<Meeting Title> — Meeting Brief`, and opens that document for review.

The capture tab retains raw transcript text only in runtime memory. It does not
write to Notes, localStorage, or a new meeting-specific database.

## Brief structure

The generated brief contains:

- Summary
- Key Decisions
- Action Items with Owner, Task, Due, Transcript Reference, and Timestamp
- Discussion Highlights
- Open Questions & Uncertainty

Argos leaves ownership, due dates, decisions, and timestamps unknown when the
transcript does not establish them.

## Boundaries

- Microphone only; no system-audio capture.
- One active capture at a time.
- No automatic capture, speaker diarization, raw audio persistence, or
  calendar-provider recording import in this phase.
