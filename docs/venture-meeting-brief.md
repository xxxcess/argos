# Argos Venture Meeting Brief

## Purpose

Meeting Brief is an Argos Venture tool for turning a meeting transcript into a
careful, reviewable Markdown record of decisions, actions, open questions, and
uncertainty. It is an Argos implementation inspired by Meetily's workflow, not
a port of Meetily code.

It appears on the Venture Home tab. Users can paste a transcript, transcribe an
uploaded recording, or capture microphone audio live in a dedicated tab. The
configured Argos Speech-to-Text provider produces text, and the configured
Utility model produces the Meeting Brief.

## Storage rule

Meeting Brief never writes to Notes. When a user chooses to keep an output,
Argos creates an owner-scoped Markdown document in the existing Document
Library:

- `<Meeting Title> — Transcript` for an explicitly exported transcript.
- `<Meeting Title> — Meeting Brief` for an explicitly exported AI brief.

The feature does not create a duplicate meeting database or retain raw audio.

## Argos architecture

```text
transcript, imported recording, or consent-gated microphone capture
  -> Argos STT provider
  -> timestamped transcript
  -> Utility model resolved from Model Endpoints / Cookbook
  -> reviewable Meeting Brief Markdown
  -> existing Document Library
```

This reuses Argos endpoint configuration, Cookbook model lifecycle, document
ownership, and Venture runtime isolation. It does not import Meetily's Tauri,
Rust capture, tray, database, or provider code.

## Implemented capabilities

- Paste, import, or live-capture transcripts.
- Browser speech recognition or short server-STT audio segments.
- Consent-gated microphone capture in a dedicated tab.
- Structured Markdown output: Summary, Key Decisions, evidence-linked Action
  Items, Discussion Highlights, and Open Questions & Uncertainty.
- Explicit transcript timestamps where live capture provides them.
- Export transcript Markdown to the Library.
- Export generated Meeting Brief Markdown to the Library from either the
  Meeting Brief window or the live-capture tab.
- Cookbook-aware Utility model selection.
- Long-transcript chunking for bounded local-model requests.
- Venture-only API boundary through `ARGOS_RUNTIME_ID=argos-venture`.

## Follow-on options

1. **Native Argos Companion** for approved system-audio capture.
2. **Authorized recording imports** from calendars or meeting providers.
3. **Quest promotion** that turns a reviewed Library document into a
   Captain-approved artifact.
4. **Durable evidence pipeline** with diarization, retention rules, and
   time-linked Quest citations.

## Guardrails

- Require explicit user action and consent before recording.
- Keep raw audio out of the product's persistent store for this web workflow.
- Never infer speaker identity, ownership, due dates, or decisions.
- Preserve uncertainty rather than fabricating meeting outcomes.
- Keep user-owned meeting output in the existing Markdown Document Library.
