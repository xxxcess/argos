# Argos Venture Audio Capture and Meeting Brief

## Product surface

Audio Capture is created from Argos Venture's **New Tab** wizard. The wizard
now offers three choices:

- Regular chat
- Quest
- Audio capture

Choosing **Audio capture** opens a dedicated in-app workspace tab beside chat
and Quest tabs. It does not open a browser tab and it does not add a Home
sidebar tool.

## Live capture workflow

1. Create an **Audio capture** tab from New Tab.
2. Enter a meeting title and explicitly confirm consent.
3. Use the recorder controls at the bottom of the tab: Start/Resume, Pause,
   Stop, elapsed time, and live waveform.
4. Review the timestamped transcript in the main panel.
5. Choose an explicit export action:
   - **Export transcript** creates `<Meeting Title> — Transcript`.
   - **Generate & export brief** creates `<Meeting Title> — Meeting Brief`.
6. Argos opens the created Library document for review.

Raw transcript text and recorder state stay in browser memory. They are not
serialized into localStorage or written to a parallel meeting database.

## Architecture

```text
New Tab wizard -> Audio Capture workspace tab
  -> consent-gated browser microphone
  -> browser STT or short audio segments to Argos STT
  -> timestamped transcript held in memory
  -> Utility model resolved through Model Endpoints / Cookbook
  -> owner-scoped Markdown Document Library export
  -> Library document opens for review
```

The feature uses the configured Argos Speech-to-Text and Utility model paths.
It does not import Meetily's Tauri, Rust capture, database, tray, or provider
code.

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

Audio Capture never exports to Notes. User-requested transcript and brief
outputs are Markdown documents in the existing owner-scoped Document Library.
The brief export immediately opens the document for the user.

## Current boundaries

- Browser microphone only; system-audio capture is not included.
- No automatic recording, calendar bot, diarization, or raw audio persistence.
- One active microphone capture at a time.
- User consent is required before microphone access.

## Future options

1. An optional native Argos Companion for approved system-audio capture.
2. Authorized meeting-provider recording imports.
3. Captain-controlled promotion of a Library document to a Quest artifact.
4. A durable evidence pipeline with diarization, retention rules, and
   time-linked Quest citations.
