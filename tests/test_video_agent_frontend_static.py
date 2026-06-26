from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_ADMIN = (_REPO / "static" / "js" / "admin.js").read_text(encoding="utf-8")
_VIDEO = (_REPO / "static" / "js" / "video.js").read_text(encoding="utf-8")
_PLATFORM = (_REPO / "static" / "js" / "platform.js").read_text(encoding="utf-8")
_MEDIA_RENDERER = _REPO / "static" / "js" / "mediaJobRenderer.js"
_MEDIA_RENDERER_TEXT = _MEDIA_RENDERER.read_text(encoding="utf-8")
_ALL_TOP_LEVEL_JS = "\n".join(
    path.read_text(encoding="utf-8")
    for path in (_REPO / "static" / "js").glob("*.js")
)


def test_media_settings_has_explicit_generate_video_row():
    assert "generate_video:" in _ADMIN
    assert "name: 'Generate video'" in _ADMIN
    assert "cat: 'Media'" in _ADMIN
    assert (
        "Creates an image anchor through the selected Image Default, then generates a local depth-parallax video."
        in _ADMIN
    )


def test_video_agent_heuristic_insertion_and_token_scanner_are_removed():
    assert "videoAgentProgress.js" not in _PLATFORM
    assert "video-job:" not in _ALL_TOP_LEVEL_JS
    assert "built[-\\s]?in agent tools" not in _VIDEO
    assert "querySelectorAll('h1,h2,h3,h4,h5" not in _VIDEO
    assert "MutationObserver" not in _VIDEO
    assert not (_REPO / "static" / "js" / "videoAgentProgress.js").exists()


def test_video_settings_button_uses_stable_settings_tab_and_card_targets():
    assert "open-settings-btn" not in _MEDIA_RENDERER_TEXT
    assert "openVideoSettingsPanel" in _MEDIA_RENDERER_TEXT
    assert "settings.js" in _MEDIA_RENDERER_TEXT
    assert 'data-settings-tab="${tab}"' in _MEDIA_RENDERER_TEXT
    assert "local-depth-video-card" in _MEDIA_RENDERER_TEXT


pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")


def _run_node(script: str) -> dict:
    result = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_structured_video_job_card_updates_in_place_without_duplicates():
    script = textwrap.dedent(f"""
        class Element {{
          constructor(tag) {{
            this.tagName = tag.toUpperCase();
            this.children = [];
            this.parentNode = null;
            this.dataset = {{}};
            this.style = {{}};
            this.className = '';
            this.textContent = '';
            this.attributes = {{}};
          }}
          appendChild(child) {{ child.parentNode = this; this.children.push(child); return child; }}
          append(...items) {{ for (const item of items) this.appendChild(item); }}
          replaceChildren(...items) {{ for (const child of this.children) child.parentNode = null; this.children = []; this.append(...items); }}
          addEventListener() {{}}
          setAttribute(name, value) {{ this.attributes[name] = String(value); }}
          querySelectorAll(selector) {{
            const out = [];
            const cls = selector.startsWith('.') ? selector.slice(1) : '';
            const visit = (node) => {{
              if (cls && String(node.className || '').split(/\\s+/).includes(cls)) out.push(node);
              for (const child of node.children || []) visit(child);
            }};
            visit(this);
            return out;
          }}
          querySelector(selector) {{ return this.querySelectorAll(selector)[0] || null; }}
        }}
        const body = new Element('body');
        globalThis.document = {{
          body,
          createElement: (tag) => new Element(tag),
          getElementById: () => null,
        }};
        body.contains = (node) => {{
          while (node) {{ if (node === body) return true; node = node.parentNode; }}
          return false;
        }};
        const galleryRefresh = [];
        globalThis.window = {{
          location: {{ origin: 'http://localhost' }},
          dispatchEvent: (event) => galleryRefresh.push(event.type),
        }};
        globalThis.Event = class {{ constructor(type) {{ this.type = type; }} }};
        globalThis.setTimeout = (fn) => {{ fn(); return 0; }};
        let fetches = 0;
        globalThis.fetch = async () => {{
          fetches += 1;
          const payload = fetches === 1
            ? {{ job_id: 'job-1', status: 'running', stage: 'anchor_ready', anchor_url: '/api/generated-image/anchor.png' }}
            : {{ job_id: 'job-1', status: 'succeeded', stage: 'succeeded', anchor_url: '/api/generated-image/anchor.png', video_url: '/api/generated-image/final.mp4' }};
          return {{ ok: true, async json() {{ return payload; }} }};
        }};
        const {{ renderVideoJobCard }} = await import('{_MEDIA_RENDERER.as_posix()}');
        const host = new Element('div');
        body.appendChild(host);
        const first = renderVideoJobCard(host, {{ kind: 'video_generation', job_id: 'job-1', status: 'queued', stage: 'planning_anchor' }});
        const second = renderVideoJobCard(host, {{ kind: 'video_generation', job_id: 'job-1', status: 'queued', stage: 'planning_anchor' }});
        for (let i = 0; i < 10; i++) await Promise.resolve();
        console.log(JSON.stringify({{
          sameCard: first === second,
          cards: host.querySelectorAll('.video-agent-job').length,
          anchors: host.querySelectorAll('.video-agent-anchor').length,
          videos: host.querySelectorAll('.video-agent-video').length,
          fetches,
          refreshes: galleryRefresh.length,
          polling: !!first._videoPolling,
        }}));
    """)

    out = _run_node(script)
    assert out["sameCard"] is True
    assert out["cards"] == 1
    assert out["anchors"] == 1
    assert out["videos"] == 1
    assert out["fetches"] == 2
    assert out["refreshes"] == 2
    assert out["polling"] is False
