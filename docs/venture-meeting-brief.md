# Argos Venture Meeting Brief

## Purpose

Meeting Brief is an Argos Venture tool for turning a meeting transcript into a
careful, reviewable record of decisions, actions, open questions, and
uncertainty. It is intentionally an Argos implementation, not a fork or port
of Meetily.

The first implementation appears only on the workspace Home tab, alongside
other personal tools such as Notes. It accepts pasted transcripts or an
optional recording selected by the user, uses the configured Speech-to-Text
service when transcription is requested, generates a brief through the
configured Utility model, and saves the finished result as an existing Note.

## Inspiration mapping

Meetily demonstrates a useful product pipeline:

```text
capture audio -> transcribe -> persist transcript -> summarize -> review/export
```

Argos Venture maps that intent to its own systems:

```text
user-provided transcript or recording
  -> Argos STT provider
  -> Utility model resolved from Argos Model Endpoints / Cookbook
  -> reviewable Markdown Meeting Brief
  -> existing Notes record
```

This keeps provider credentials, endpoint selection, model lifecycle, storage,
and authorization inside systems Argos already owns. It also avoids moving
Tauri, Rust audio capture, desktop-tray behavior, or Meetily database code into
the Venture runtime.

## Implemented baseline

- **Transcript first.** Paste a transcript directly, or select an audio/video
  file for the existing `/api/stt/transcribe` flow.
- **Cookbook-aware synthesis.** The server uses the existing `utility`
  endpoint resolver; a locally served Cookbook model works without a second
  model registry or API key store.
- **Grounded output.** The synthesis prompt requires explicit evidence for
  decisions, owners, due dates, and outcomes. Ambiguity must remain visible.
- **Long meeting handling.** Transcripts are bounded and summarized in chunks
  before final consolidation.
- **Existing lifecycle.** A reviewed brief is saved to owner-scoped Notes with
  a `meeting_brief` type and `meeting` label. No duplicate meeting database is
  created in the first phase.
- **Venture-only boundary.** API calls require `ARGOS_RUNTIME_ID=argos-venture`.

## Candidate implementation paths

### 1. Transcript-first Meeting Brief — implemented

A Home-tab tool accepts pasted transcript text or a selected recording. Argos
STT transcribes the recording; the Utility model creates a structured Markdown
brief; the user saves it to Notes.

**Strengths:** smallest surface area, useful immediately, works with local
Cookbook models, no OS-specific permissions, simplest privacy story.

**Trade-off:** not a live recorder and no speaker diarization.

### 2. Browser live recorder with incremental transcription

Use `MediaRecorder` for the microphone and upload short chunks to the existing
STT service. The UI presents a live draft transcript, then invokes the same
brief endpoint at stop time.

**Strengths:** stays fully web-native and reuses the baseline summarization
path.

**Trade-off:** browser system-audio support is inconsistent and permission
behavior differs by platform; it cannot replace desktop capture for all users.

### 3. Optional Argos Companion desktop capture agent

Create a purpose-built Argos Companion process for microphone plus approved
system-audio capture. The companion streams encrypted local chunks to Venture
or writes a local staging file that the browser imports. It should use Argos
API tokens, endpoint configuration, and meeting APIs, but remain a separately
installed native component.

**Strengths:** closest to Meetily's all-audio desktop experience while keeping
Argos as the product owner.

**Trade-off:** substantial cross-platform audio, installer, permission, and
security work. This should not be embedded in the web server.

### 4. Calendar/recording-import workflow

Connect calendar and meeting providers so a Captain can import an authorized
recording or existing transcript after a meeting. Process it as a background
job, create a private Meeting Brief, and offer an explicit action to attach it
to a Quest as a Captain-reviewed artifact or source.

**Strengths:** better team workflow, avoids recording-consent ambiguity, aligns
with Venture's evidence and review model.

**Trade-off:** provider integrations, OAuth, retention policy, and job-status
UX are required.

### 5. Durable meeting evidence pipeline

Add first-class `Meeting` and `MeetingSegment` records, background
transcription/diarization workers, object storage, retention controls, and a
Quest-aware evidence adapter. Finished briefs can become traceable Artifact
Drafts with time-linked transcript citations.

**Strengths:** highest-quality enterprise path; supports search, auditability,
semantic retrieval, and long-running source analysis.

**Trade-off:** most expensive path: async workers, media storage, consent and
retention policy, speaker identity safeguards, and new authorization rules.

## Recommended sequence

1. Validate the implemented transcript-first experience with a local
   Cookbook-served STT/LLM configuration.
2. Add browser microphone recording only if the transcript-first workflow sees
   regular use.
3. Add post-meeting recording import and Captain-to-Quest promotion.
4. Consider a native Companion only when system-audio capture is essential.
5. Build the durable evidence pipeline only when Venture needs meeting content
   to operate as a governed Quest Source rather than a personal note.

## Guardrails for later phases

- Never record or ingest audio without an explicit user action and clear
  consent language.
- Keep raw recordings and transcripts private by default.
- Do not infer speaker identity, task ownership, due dates, or decisions.
- For Quest use, preserve evidence links and Captain approval before sharing.
- Reuse Argos model endpoint, Cookbook, runtime isolation, and owner/Quest
  authorization systems instead of adding a parallel provider stack.
