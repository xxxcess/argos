# Speech-to-text settings

Speech to Text is enabled by default with the browser provider. The Settings → AI Defaults panel now surfaces a Speech to Text card where admins can enable or disable transcription and choose a provider.

Supported providers:

- `browser` — uses the browser Web Speech API. This is the default and requires HTTPS or localhost microphone access. If speech recognition is unavailable, recordings fall back to audio attachments.
- `local` — sends microphone recordings to `/api/stt/transcribe`, which uses `faster-whisper` through the backend STT service.
- `endpoint:<id>` — sends microphone recordings to `/api/stt/transcribe`, then forwards to the selected OpenAI-compatible endpoint's `/audio/transcriptions` route.
- `disabled` — keeps the original behavior and attaches microphone recordings as `.webm` files.

The UI persists `stt_enabled`, `stt_provider`, `stt_model`, `stt_language`, `stt_conversation_loop`, `stt_loop_submit_seconds`, and `stt_loop_idle_timeout_seconds` through `/api/auth/settings` and refreshes the active recorder provider immediately after saving.

## Conversation loop

Conversation loop is an opt-in hands-free mode. When enabled, clicking the microphone starts a looped voice conversation:

1. Start listening and clear the chat input for the loop turn so old prompts cannot be reused.
2. Wait for fresh voice activity before any auto-stop or auto-submit can occur.
3. After speech is detected, stop recording only after `stt_loop_submit_seconds` seconds of silence, default `3`.
4. Transcribe and auto-submit only if the current recording produced fresh transcript text.
5. Replace the chat input with the fresh transcript before clicking send.
6. Wait for the model to finish its final response.
7. Start recording again for the next instruction.
8. End the loop if no instruction is heard before `stt_loop_idle_timeout_seconds`, default `5`, or if the user manually stops recording.

The loop uses client-side voice activity detection to avoid transcribing silence. It also guards against duplicate rapid re-submission of the same transcript, which prevents an old cached prompt from being resent if the browser STT API returns stale or empty results.

## Safety behavior

The recorder treats the loop turn as invalid until it sees voice activity or a fresh browser recognition result. Auto-submit validates that the input exactly matches the transcript from the current recording before it clicks send. This prevents a previously typed prompt, an earlier transcript, or a stale browser STT result from being sent in the next loop turn.
