# PR notes

- Branch: `feature/tts-thinking-toggle`
- Base: `nightly`
- Adds a Settings → AI Defaults TTS control for including/excluding thinking sections during read-aloud.
- Default behavior skips raw `<think>`/`<thinking>` content and rendered `.thinking-section` content.
- Toggle-on behavior keeps thinking content but removes UI labels from spoken text.
