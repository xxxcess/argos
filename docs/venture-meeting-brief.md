# Argos Venture Live Capture and Meeting Brief

## Product surface

Live Capture is created from Argos Venture's **New Tab** wizard. The wizard
offers three workspace session types:

- Regular chat
- Quest
- Live Capture

Choosing **Live Capture** opens a dedicated in-app workspace tab beside chat
and Quest tabs. It does not open a browser tab and it does not add a Home
sidebar tool.

## Live Capture workflow

1. Create a **Live Capture** tab from New Tab.
2. Enter a title.
3. Press **Start recording** and answer the browser's microphone permission
   prompt when it appears.
4. Use the recorder footer to pause, resume, or stop recording and review the
   timestamped transcript.
5. Stop recording and let final transcription finish.
6. Choose an explicit export action:
   - **Export transcript** creates `<Meeting Title> — Transcript`.
   - **Generate & export brief** creates `<Meeting Title> — Meeting Brief`.
7. Argos opens the created Library document beside the still-open Live Capture
   tab.

## Persistence model

Each Live Capture is an owner-scoped Argos session with type `live_capture`.
Each finalized timestamp segment is stored as a timestamped user message in the
existing chat-message history. The Live Capture transcript therefore persists
for later review rather than being only in-memory tab state.

Local and endpoint Speech-to-Text use 15-second microphone segments. Segment
timestamps use the audio segment's start time, so final transcription callbacks
do not collapse into the same final timestamp.

## Architecture

```text
New Tab wizard -> persisted Live Capture session
  -> browser microphone permission after Start recording
  -> browser STT or 15-second segments to Argos STT
  -> timestamped chat-message transcript
  -> Utility model resolved through Model Endpoints / Cookbook
  -> owner-scoped Markdown Document Library export
  -> Library document opens beside Live Capture
```

The feature uses configured Argos Speech-to-Text and Utility model paths. It
does not import Meetily's Tauri, Rust capture, database, tray, or provider code.

## Meeting Brief format

The generated Markdown format takes useful structure from Meetily's standard
meeting-notes template while preserving Argos's evidence requirement:

- Summary
- Key Decisions
- Action Items with Owner, Task, Due, Transcript Reference, and Timestamp
- Discussion Highlights
- Open Questions & Uncertainty

The model must not fabricate attendees, owners, dates, decisions, timestamps,
or outcomes. Missing information remains explicit.

## Persistence rule

Live Capture never exports to Notes. User-requested transcript and brief outputs
are Markdown documents in the existing owner-scoped Document Library. The brief
export opens the document for the user and keeps the Live Capture tab visible.

## Current boundaries

- Browser microphone only; system-audio capture is not included.
- No automatic recording, calendar bot, diarization, or raw audio persistence.
- One active microphone recording at a time.
- The browser controls microphone permission.
- Export is available only after recording stops and final transcription
  completes.
- A transient Utility-provider 429 gets one bounded retry; a continuing rate
  limit is reported without fabricating a brief.

## Future options

1. An optional native Argos Companion for approved system-audio capture.
2. Authorized meeting-provider recording imports.
3. Captain-controlled promotion of a Library document to a Quest artifact.
4. A durable evidence pipeline with diarization, retention rules, and
   time-linked Quest citations.
