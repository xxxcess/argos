"""learn_workflow tool for script-backed skills.

This tool turns an explicit user request like "remember how to do this next
time" into a deterministic artifact bundle:

* data/skills/<category>/<slug>/SKILL.md
* data/skills/<category>/<slug>/scripts/<slug>.py|sh
* one persistent memory that nudges future requests toward that skill

The LLM still decides when a workflow should be learned, but the filesystem
writes are deterministic, validated, and idempotent.
"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.constants import DATA_DIR
from src.memory import MemoryManager
from services.memory.skill_format import slugify


def _parse_args(content: str) -> Dict[str, Any]:
    if isinstance(content, dict):
        return content
    raw = (content or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"learn_workflow expects JSON arguments: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("learn_workflow expects a JSON object")
    return parsed


def _as_list(value: Any, *, field: str) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [line.strip() for line in value.splitlines()]
        return [line for line in items if line]
    if isinstance(value, Iterable):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValueError(f"{field} must be a list of strings or newline-delimited string")


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_yaml_scalar(item) for item in value) + "]"
    text = str(value)
    if not text:
        return '""'
    if any(ch in text for ch in (":", "#", "\n", "[", "]", "{", "}", ",", "&", "*", "!", "|", ">", "'", '"', "%", "@")):
        return json.dumps(text)
    return text


def _atomic_write(path: Path, content: str, mode: Optional[int] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def _default_script(language: str, slug: str, steps: List[str]) -> str:
    if language == "bash":
        quoted_steps = "\n".join(f"# {idx + 1}. {step}" for idx, step in enumerate(steps))
        return f"""#!/usr/bin/env bash
set -euo pipefail

if [[ "${{1:-}}" == "--check" || "${{1:-}}" == "--dry-run" ]]; then
  printf '{{"ok":true,"changed":false,"skill":"{slug}","mode":"%s"}}\n' "${{1#--}}"
  exit 0
fi

{quoted_steps}

echo '{{"ok":false,"changed":false,"error":"No implementation supplied yet. Fill in this learned workflow script."}}'
exit 2
"""
    steps_literal = json.dumps(steps, indent=2)
    return f'''#!/usr/bin/env python3
"""Deterministic workflow entrypoint for skill: {slug}."""

import argparse
import json

STEPS = {steps_literal}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the {slug} workflow")
    parser.add_argument("--check", action="store_true", help="Validate the script without changing anything")
    parser.add_argument("--dry-run", action="store_true", help="Show intended work without changing anything")
    args = parser.parse_args()

    if args.check or args.dry_run:
        print(json.dumps({{"ok": True, "changed": False, "skill": "{slug}", "steps": STEPS}}))
        return 0

    print(json.dumps({{"ok": False, "changed": False, "error": "No implementation supplied yet. Fill in this learned workflow script."}}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _script_extension(language: str) -> str:
    return "sh" if language == "bash" else "py"


def _script_toolset(language: str) -> str:
    return "bash" if language == "bash" else "python"


def _build_skill_markdown(
    *,
    slug: str,
    description: str,
    category: str,
    trigger: str,
    steps: List[str],
    pitfalls: List[str],
    verification: List[str],
    tags: List[str],
    language: str,
    entrypoint: str,
    status: str,
    owner: Optional[str],
) -> str:
    frontmatter: Dict[str, Any] = {
        "name": slug,
        "description": description,
        "version": "1.0.0",
        "category": category,
        "tags": sorted(set(["workflow", "script-backed", "deterministic", *tags])),
        "requires_toolsets": [_script_toolset(language), "manage_skills", "manage_memory"],
        "status": status,
        "confidence": 0.8,
        "source": "learned",
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if owner:
        frontmatter["owner"] = owner

    fm = "\n".join(f"{key}: {_yaml_scalar(value)}" for key, value in frontmatter.items())
    procedure = [
        "Load this skill when the user asks for the trigger below or asks to repeat this workflow.",
        f"Run the deterministic entrypoint `{entrypoint}` instead of re-inventing the steps.",
        "Use `--check` or `--dry-run` first when the request is ambiguous or could change files/state.",
        "Return the script's structured JSON result to the user with any important stdout/stderr details.",
    ]
    procedure.extend(steps)
    verifications = verification or [
        f"Run `{entrypoint} --check` and confirm it exits 0.",
        "Confirm the script returns JSON with ok/changed fields.",
    ]
    pitfall_items = pitfalls or [
        "Do not regenerate a fresh script for the same workflow unless the user explicitly asks to revise it.",
        "Keep inputs explicit; avoid relying on ambient state, unsorted file order, randomness, or current time unless required.",
    ]

    def numbered(items: List[str]) -> str:
        return "\n".join(f"{idx + 1}. {item}" for idx, item in enumerate(items))

    def bulleted(items: List[str]) -> str:
        return "\n".join(f"- {item}" for item in items)

    return f"""---
{fm}
---

## When to Use

{trigger}

## Procedure

{numbered(procedure)}

## Pitfalls

{bulleted(pitfall_items)}

## Verification

{bulleted(verifications)}

## Deterministic Entrypoint

- Language: `{language}`
- Path: `{entrypoint}`
- Contract: the script should be idempotent, accept explicit arguments, support `--check` or `--dry-run`, and return JSON with `ok`, `changed`, `artifacts`, `warnings`, and `next_steps` when possible.
"""


class LearnWorkflowTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        try:
            args = _parse_args(content)
            name = (args.get("name") or args.get("title") or args.get("trigger") or "workflow").strip()
            slug = slugify(name, fallback="workflow")
            category = slugify(args.get("category") or "learned", fallback="learned")
            trigger = (args.get("trigger") or args.get("when_to_use") or "").strip()
            if not trigger:
                raise ValueError("trigger/when_to_use is required")

            steps = _as_list(args.get("steps") or args.get("procedure"), field="steps")
            if len(steps) <= 2:
                raise ValueError("learn_workflow only creates script-backed skills for workflows with more than 2 concrete steps")

            language = str(args.get("script_language") or args.get("language") or "python").strip().lower()
            if language not in {"python", "bash"}:
                raise ValueError("script_language must be 'python' or 'bash'")

            status = str(args.get("status") or "draft").strip().lower()
            if status not in {"draft", "published"}:
                raise ValueError("status must be 'draft' or 'published'")

            owner = None
            if isinstance(ctx, dict):
                owner = ctx.get("owner") or ctx.get("user") or ctx.get("user_id")
            owner = str(owner).strip() if owner else None

            description = (args.get("description") or f"Deterministic script-backed workflow for: {trigger[:120]}").strip()
            pitfalls = _as_list(args.get("pitfalls"), field="pitfalls")
            verification = _as_list(args.get("verification"), field="verification")
            tags = _as_list(args.get("tags"), field="tags")
            script = args.get("script_content") or args.get("script") or _default_script(language, slug, steps)
            script = str(script)
            if not script.endswith("\n"):
                script += "\n"

            skills_root = Path(DATA_DIR) / "skills" / category / slug
            ext = _script_extension(language)
            rel_entrypoint = f"scripts/{slug}.{ext}"
            script_path = skills_root / rel_entrypoint
            skill_path = skills_root / "SKILL.md"

            skill_md = _build_skill_markdown(
                slug=slug,
                description=description,
                category=category,
                trigger=trigger,
                steps=steps,
                pitfalls=pitfalls,
                verification=verification,
                tags=tags,
                language=language,
                entrypoint=rel_entrypoint,
                status=status,
                owner=owner,
            )

            _atomic_write(skill_path, skill_md)
            _atomic_write(script_path, script, mode=(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IROTH))

            memory_text = (args.get("memory_text") or f"For future requests matching '{trigger}', use the script-backed skill `{slug}` before improvising steps.").strip()
            memory_id = None
            if memory_text:
                mm = MemoryManager(DATA_DIR)
                entries = mm.load_all()
                duplicate = any(
                    str(entry.get("text", "")).strip().lower() == memory_text.lower()
                    and entry.get("owner") == owner
                    for entry in entries
                )
                if not duplicate:
                    entry = mm.add_entry(memory_text, source="learn_workflow", category="preference", owner=owner)
                    entries.append(entry)
                    mm.save(entries)
                    memory_id = entry.get("id")

            return {
                "output": "Created script-backed workflow skill",
                "skill": slug,
                "status": status,
                "category": category,
                "skill_path": str(skill_path),
                "entrypoint": str(script_path),
                "relative_entrypoint": rel_entrypoint,
                "memory_id": memory_id,
                "next_steps": [
                    f"Run {rel_entrypoint} --check from the skill directory.",
                    "Publish the skill with manage_skills once verified if it should be auto-used.",
                ],
                "exit_code": 0,
            }
        except Exception as exc:
            return {"error": f"learn_workflow: {exc}", "exit_code": 1}
