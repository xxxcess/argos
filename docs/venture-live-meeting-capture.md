# Argos Venture Audio Capture Tab

## Entry point

Audio Capture is a choice in the Venture **New Tab** wizard. Selecting it opens
a dedicated tab inside the Argos workspace tab strip, alongside chat and Quest
tabs. It never opens a browser window.

The tab is idle when created. It does not request microphone permission, start
recording, start browser recognition, or send audio to Speech-to-Text until the
user confirms consent and presses **Start capture**.

## Recorder controls

The tab uses a two-row recorder footer so timing and audio activity remain
separate from the action controls:

- Elapsed time and microphone waveform appear above the controls.
- Start capture / Resume capture, Pause, and Stop appear on the control row.
- Export transcript and Generate & export brief appear on that same control row.

The Home sidebar, its collapse control, and Home-only tools are hidden while an
Audio Capture tab is active.

## Transcript and export flow

Browser STT uses `SpeechRecognition` when configured. Local or endpoint STT
receives self-contained microphone segments through Argos's existing
`/api/stt/transcribe` route. Final transcript entries include a relative
`[MM:SS]` marker.

Each recording has a distinct run identifier. Late callbacks from an old,
stopped, or closed run are ignored rather than appended to the current
transcript. Switching away from the active capture tab stops an active capture;
Audio Capture does not continue recording in the background.

Stop capture before exporting so the final audio segment can be transcribed.
Exports are explicit and Library-only:

- **Export transcript** writes an owner-scoped Markdown document titled
  `<Meeting Title> — Transcript` and opens it in the existing document editor
  panel beside the still-open Audio Capture tab.
- **Generate & export brief** uses the existing Meeting Brief generation route,
  writes `<Meeting Title> — Meeting Brief`, and opens it beside the capture tab.

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
