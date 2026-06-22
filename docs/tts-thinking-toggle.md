# TTS thinking toggle

This feature adds a Settings → AI Defaults control for whether text-to-speech playback includes model thinking/reasoning sections.

The frontend stores the preference locally and also posts `tts_include_thinking` to `/api/auth/settings` when available. TTS extraction skips both raw `<think>`/`<thinking>` blocks and rendered `.thinking-section` content by default. When enabled, it includes the thinking content while suppressing UI labels such as “View thinking process”.
