# Speech-to-text settings

Speech to Text is enabled by default with the browser provider. The Settings → AI Defaults panel surfaces a Speech to Text card where admins can enable or disable transcription and choose a provider.

Supported providers:

- `browser` — uses the browser Web Speech API. This is the default and requires HTTPS or localhost microphone access. If recognition is unavailable, recordings fall back to audio attachments.
- `local` — sends microphone recordings to `/api/stt/transcribe`, which uses `faster-whisper` through the backend STT service.
- `endpoint:<id>` — sends microphone recordings to `/api/stt/transcribe`, then forwards to the selected OpenAI-compatible endpoint's `/audio/transcriptions` route.
- `disabled` — keeps the original behavior and attaches microphone recordings as `.webm` files.

The UI persists `stt_enabled`, `stt_provider`, `stt_model`, `stt_language`, `stt_conversation_loop`, `stt_loop_submit_seconds`, and `stt_loop_idle_timeout_seconds` through `/api/auth/settings`.

## Conversation loop

Conversation loop is an opt-in hands-free mode. When enabled, clicking the microphone starts a looped voice conversation:

1. Clear the chat input for the new loop turn.
2. Wait for fresh user voice activity.
3. Stop recording after `stt_loop_submit_seconds` seconds of silence, default `3`.
4. Release recording state, then transcribe and submit only fresh text from that turn.
5. Wait for the assistant's response to finish.
6. Wait for TTS playback and its queue to finish, followed by a short quiet cooldown.
7. Start the next recording turn.

The loop checks both browser speech synthesis and the application TTS manager. It will not open the microphone while audio is playing or queued. If TTS starts while a loop recording is active, the recorder stops, discards that capture without transcribing it, waits for playback to finish, and only then resumes listening. This prevents the assistant's spoken response from becoming the next user prompt.

The loop also uses client-side voice activity detection and duplicate-transcript protection to avoid empty or repeated submissions.
