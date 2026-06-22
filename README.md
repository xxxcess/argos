<p align="center">
  <img src="docs/odysseus-wordmark.png" alt="Odysseus" width="238">
</p>

<p align="center">
  A self-hosted AI workspace for chat, agents, research, documents, email, notes, calendar, and local model workflows.
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="#text-to-speech-tts">TTS</a> ·
  <a href="docs/setup.md">Setup Guide</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="ROADMAP.md">Roadmap</a>
</p>

<p align="center">
  <a href="https://repology.org/project/odysseus-ai/versions"><img src="https://repology.org/badge/vertical-allrepos/odysseus-ai.svg" alt="Packaging status"></a>
</p>

<p align="center">
  <img src="docs/odysseus-browser.jpg" alt="Odysseus interface">
</p>

---

## Quick Start

> `dev` is the default branch and gets the newest changes first. Use [`main`](https://github.com/pewdiepie-archdaemon/odysseus/tree/main) if you want the more curated branch.

```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
cp .env.example .env
docker compose up -d --build
```

Open `http://localhost:7000` when the containers are healthy. The first admin password is printed in `docker compose logs odysseus`.

Native installs, GPU notes, Windows/macOS instructions, HTTPS, and configuration live in the [setup guide](docs/setup.md).

## Features

- **Chat + Agents** — local/API models, tools, MCP, files, shell, skills, and memory.
- **Cookbook** — hardware-aware model recommendations, downloads, and serving.
- **Deep Research** — multi-step web research with source reading and report generation.
- **Compare** — blind side-by-side model testing and synthesis.
- **Documents** — writing-first editor with AI edits, suggestions, Markdown, HTML, CSV, and syntax highlighting.
- **Email** — IMAP/SMTP inbox with triage, tags, summaries, reminders, and reply drafts.
- **Notes, Tasks + Calendar** — reminders, todos, scheduled agent tasks, and CalDAV sync.
- **Extras** — gallery/image editor, themes, uploads, web search, presets, sessions, and 2FA.

## Text-to-Speech (TTS)

Odysseus supports three TTS providers:

- `browser` — uses the browser Web Speech API. This is the easiest mode to test first.
- `endpoint:<id>` — uses an OpenAI-compatible `/audio/speech` endpoint configured in Odysseus.
- `local` — uses Kokoro for local speech synthesis.

### Browser TTS quick test

Edit `data/settings.json`:

```json
{
  "tts_enabled": true,
  "tts_provider": "browser",
  "tts_voice": "",
  "tts_speed": "1"
}
```

Restart Odysseus and test from the browser UI.

### Local Kokoro TTS on macOS Apple Silicon

For Mac Mini M1/M2/M3/M4, run Odysseus natively with `./start-macos.sh`. Do not use Docker for local Kokoro if you want Apple Silicon acceleration.

Install the system dependency:

```bash
brew install espeak-ng
```

Install the Python dependencies inside the Odysseus repo:

```bash
./venv/bin/python -m pip install --force-reinstall "setuptools<82"
./venv/bin/python -m pip install --no-cache-dir \
  kokoro \
  soundfile \
  torch \
  numpy \
  transformers \
  "tokenizers==0.22.2"
```

`tokenizers==0.22.2` is required because `tokenizers==0.23.1` can break the installed `transformers` requirement.

Add these pins to `requirements.txt`:

```txt
# Local Kokoro TTS
kokoro
soundfile
torch
numpy
transformers
tokenizers==0.22.2
setuptools<82
```

If `start-macos.sh` reinstalls `chromadb`, make sure it re-pins the compatible TTS dependencies afterward:

```bash
"$VENV_PY" -m pip install --force-reinstall "setuptools<82" "tokenizers==0.22.2"
```

This prevents dependency resolution from pulling `tokenizers==0.23.1` back in.

Enable local Kokoro in `data/settings.json`:

```json
{
  "tts_enabled": true,
  "tts_provider": "local",
  "tts_model": "kokoro-82m",
  "tts_voice": "af_heart",
  "tts_speed": "1"
}
```

Test Kokoro directly:

```bash
./venv/bin/python - <<'PY'
from kokoro import KPipeline
import soundfile as sf

pipeline = KPipeline(lang_code="a")

for _, _, audio in pipeline(
    "Kokoro is working locally on this Mac.",
    voice="af_heart",
    speed=1
):
    sf.write("/tmp/kokoro-test.wav", audio, 24000)
    break

print("wrote /tmp/kokoro-test.wav")
PY

afplay /tmp/kokoro-test.wav
```

Test the Odysseus TTS service directly:

```bash
./venv/bin/python - <<'PY'
from services.tts import get_tts_service

tts = get_tts_service()
print(tts.get_stats())

audio = tts.synthesize("Hello from local Kokoro inside Odysseus.")
print("audio bytes:", len(audio) if audio else None)

if audio:
    open("/tmp/odysseus-kokoro.wav", "wb").write(audio)
    print("wrote /tmp/odysseus-kokoro.wav")
PY

afplay /tmp/odysseus-kokoro.wav
```

Start Odysseus:

```bash
./start-macos.sh
```

Log in, open the browser DevTools Console, and verify TTS availability:

```js
await fetch('/api/tts/stats').then(r => r.json())
```

Expected result:

```json
{
  "available": true,
  "ready": true,
  "provider": "local",
  "voice": "af_heart"
}
```

Enable automatic playback in the browser:

```js
localStorage.setItem('odysseus-tts-autoplay', 'true');
location.reload();
```

Disable automatic playback:

```js
localStorage.removeItem('odysseus-tts-autoplay');
location.reload();
```

### Troubleshooting

If Odysseus reports Kokoro as unavailable:

```json
{
  "available": false,
  "provider": "local",
  "model": "Kokoro (not loaded)"
}
```

Check the installed `tokenizers` version:

```bash
./venv/bin/python - <<'PY'
import tokenizers
print(tokenizers.__version__)
PY
```

It should be:

```txt
0.22.2
```

If it shows `0.23.1`, reinstall the compatible version and restart Odysseus:

```bash
./venv/bin/python -m pip install --force-reinstall "setuptools<82" "tokenizers==0.22.2"
```

## Demo

A full hover-to-play tour lives on the landing page: [`docs/index.html`](docs/index.html).

## Contributing

Help is welcome. The best entry points are fresh-install testing, provider setup bugs, mobile/editor polish, docs, and small focused refactors. See [CONTRIBUTING.md](CONTRIBUTING.md) and [ROADMAP.md](ROADMAP.md).

## Security

Odysseus is a self-hosted workspace with powerful local tools. Keep auth enabled, keep private data out of Git, and do not expose raw model/service ports publicly. Deployment details are in the [setup guide](docs/setup.md#security-notes).

## Star History

<a href="https://www.star-history.com/?repos=pewdiepie-archdaemon%2Fodysseus&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&legend=top-left" />
 </picture>
</a>

## License

AGPL-3.0-or-later -- see [LICENSE](LICENSE) and [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).
