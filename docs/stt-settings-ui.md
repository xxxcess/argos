# Speech-to-text settings

Speech to Text is enabled by default with the browser provider. The Settings → AI Defaults panel now surfaces a Speech to Text card where admins can enable or disable transcription and choose a provider.

Supported providers:

- `browser` — uses the browser Web Speech API. This is the default and requires HTTPS or localhost microphone access. If speech recognition is unavailable, recordings fall back to audio attachments.
- `local` — sends microphone recordings to `/api/stt/transcribe`, which uses `faster-whisper` through the backend STT service.
- `endpoint:<id>` — sends microphone recordings to `/api/stt/transcribe`, then forwards to the selected OpenAI-compatible endpoint's `/audio/transcriptions` route.
- `disabled` — keeps the original behavior and attaches microphone recordings as `.webm` files.

The UI persists `stt_enabled`, `stt_provider`, `stt_model`, and `stt_language` through `/api/auth/settings` and refreshes the active recorder provider immediately after saving.
