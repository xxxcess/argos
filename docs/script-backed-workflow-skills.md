# Script-backed workflow skills

This document describes the first implementation slice for deterministic, script-backed workflow learning in agent mode.

## Goal

When a user explicitly asks the agent to remember how to do a repeatable workflow, the agent should avoid relying on fresh LLM improvisation next time. Instead, it should create a durable skill plus a deterministic script entrypoint.

The intended pattern is:

1. Detect that the user wants a workflow remembered.
2. Confirm the workflow has more than two concrete steps.
3. Call `learn_workflow` with the trigger, steps, script language, and optional script body.
4. Store a draft `SKILL.md` and script under `data/skills/<category>/<slug>/`.
5. Add a memory nudge so future matching requests load the skill before improvising.
6. Verify with `--check` or `--dry-run`, then publish the skill when ready.

## Tool contract

`learn_workflow` accepts a JSON object with these common fields:

```json
{
  "name": "deploy-argos-local",
  "trigger": "When the user asks to deploy Argos locally",
  "category": "devops",
  "steps": [
    "Check required environment variables",
    "Install or update dependencies",
    "Run database migrations",
    "Start the local services"
  ],
  "script_language": "python",
  "script_content": "#!/usr/bin/env python3\nprint('fill this in')\n",
  "verification": [
    "Run scripts/deploy-argos-local.py --check",
    "Confirm the script returns JSON with ok=true"
  ],
  "status": "draft"
}
```

Required behavior:

- `trigger` or `when_to_use` is required.
- `steps` or `procedure` must contain more than two concrete steps.
- `script_language` must be `python` or `bash`.
- `status` must be `draft` or `published`; default is `draft`.
- If `script_content` is omitted, the tool writes a scaffold script that supports `--check` and `--dry-run` and returns structured JSON.

## Example fenced tool call

Agent-mode models that use fenced tool calls can invoke it like this:

````markdown
```learn_workflow
{
  "name": "normalize-upload-with-tika",
  "trigger": "When the user asks to normalize a PocketBase upload with Apache Tika",
  "category": "argos-dev",
  "steps": [
    "Fetch the upload record from PocketBase",
    "Download the uploaded file bytes",
    "Send the bytes to Apache Tika /text",
    "Create a metadata record",
    "Patch the upload status to normalized"
  ],
  "script_language": "python",
  "verification": [
    "Run scripts/normalize-upload-with-tika.py --check",
    "Confirm the generated skill remains draft until manually verified"
  ]
}
```
````

## Generated files

For the example above, the tool writes:

```text
data/skills/argos-dev/normalize-upload-with-tika/SKILL.md
data/skills/argos-dev/normalize-upload-with-tika/scripts/normalize-upload-with-tika.py
```

The generated `SKILL.md` includes:

- YAML frontmatter with `workflow`, `script-backed`, and `deterministic` tags.
- `requires_toolsets` for the selected runtime plus `manage_skills` and `manage_memory`.
- Procedure, pitfalls, verification, and deterministic entrypoint sections.

The generated memory is a routing nudge, not the procedure itself. The memory should point future requests toward the skill so the agent retrieves the durable workflow.

## Testing checklist

1. Start Argos on this branch.
2. Use agent mode with tools enabled.
3. Ask the agent to remember a workflow with more than two steps.
4. Confirm `data/skills/<category>/<slug>/SKILL.md` exists.
5. Confirm `data/skills/<category>/<slug>/scripts/<slug>.py` or `.sh` exists.
6. Run the script with `--check`.
7. Inspect memory and confirm a single preference memory points future requests toward the skill.
8. Publish the skill with `manage_skills` after verification.

## Current scope

This PR adds the executable `learn_workflow` tool and registers it in the agent tool facade. That makes the workflow-learning path testable without changing the larger agent-loop prompt assembly.

Recommended follow-ups:

1. Add a native function schema for `learn_workflow` in `src/tool_schemas.py` so function-calling models can select it directly.
2. Add `learn_workflow` to `src/tool_index.py` descriptions or deterministic keyword selection for prompts containing phrases like “remember how to”, “save these steps”, “next time”, and “make this a skill”.
3. Add a small deterministic classifier in `src/agent_loop.py`: if the user explicitly asks to remember a workflow and the extracted procedure has more than two steps, force-select `learn_workflow`; if it has two or fewer steps, use `manage_memory` instead.
