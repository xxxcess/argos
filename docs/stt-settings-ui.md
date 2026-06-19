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

1. Record the user's instruction.
2. Auto-stop and transcribe after `stt_loop_submit_seconds` seconds, default `3`.
3. Auto-submit the transcribed text to the active chat.
4. Wait for the model to finish its final response.
5. Start recording again for the next instruction.
6. End the loop if no instruction is heard before `stt_loop_idle_timeout_seconds`, default `5`, or if the user manually stops recording.

If no transcript is detected before the idle timeout, the loop exits without sending an empty message.
