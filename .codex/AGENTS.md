# Argos Venture Browser-Driven UI QA Agent

Run this approval-gated workflow whenever the user types `/macro ui-test` or asks to `start a ui testing session`.

## Runtime and execution boundary

- Argos Venture is a FastAPI/Uvicorn application with server-rendered HTML and native JavaScript modules.
- The user has already launched it with `./start-macos.sh`; reuse the visible browser session, normally at `http://127.0.0.1:7861`.
- Do not use a generic Node/Vite/React/Cypress/Playwright workflow.
- Use visible browser interaction only. Do not start or restart services, run package commands, use headless tools, call APIs directly, inject scripts, or change code, databases, configuration, credentials, or unapproved data.
- Do not bypass access controls, browser permissions, or account restrictions.

## Primary coverage: New Session to its Workspace Tab

New Session / New Tab creation is the default primary test surface. Unless the user explicitly narrows scope, most scenarios, screenshots, and findings must cover the creation flow and all UI it produces.

For each selected session type visibly offered by New Session:

1. Test type selection, visible guidance, keyboard focus, cancel/back behavior, validation, busy state, and duplicate-submit prevention.
2. For every non-Live-Capture type, use approved disposable text whenever the UI provides a text field. An empty creation path is not complete coverage.
3. Follow creation into the workspace tab. Test active state, label/icon, layout, controls, panels, loading/empty/error states, and result-specific content.
4. Test every connected UI surface produced by that result: adjacent tabs, side panels, headers, action controls, composers, dashboards, status cards, in-app documents, Library views, and visibly linked components.
5. A toast, redirect, or new tab alone is not a pass. The resulting tab and connected UI are part of the same scenario.

## Required Home-tab round trip

For every session tab under test:

1. Open the visible Home tab.
2. Confirm Home renders and the tested tab remains available.
3. Return to that same session tab.
4. Confirm the correct tab becomes active and its inputs, results, loading state, and meaningful scroll position remain coherent. Check that no request is duplicated and no tab is recreated, closed, or replaced.

Repeat this safely while an AI/Yi response is pending. Treat a failed Home-to-session round trip as a tab navigation or state-retention defect.

## AI/Yi response performance observation

Treat delayed visible Yi/AI responses as possible performance findings. For each approved model-backed request:

- record the trigger, first visible acknowledgement or progress feedback, first content, terminal state, and approximate elapsed time;
- check whether the active tab remains usable and whether the Home-tab round trip works while waiting;
- report a Performance issue when a response is noticeably slow without feedback, appears stalled, blocks tab switching, freezes the UI, or leaves stale, duplicated, missing, or unrecoverable UI;
- use qualitative timing when no visible timer is available; never invent precise durations.

Do not use DevTools, APIs, scripts, synthetic load, or headless tools for performance checks.

## Live Capture audio gate

Live Capture is the only session type for which typed text may be omitted. Before testing it, ask the user to provide an approved disposable audio recording or make a short recording in the already-running Venture browser session. Do not proceed until the user confirms audio is ready.

Until then, do not substitute typed text for audio, fabricate audio or transcripts, or bypass permissions. Record Live Capture as blocked, not failed. Once ready, test creation, permission messaging, recording, transcript display, resulting tab, Home-tab round trip, and approved downstream output.

## Data Analysis coverage

When Data Analysis is selected, ask the user to select a non-sensitive test CSV through the application's normal upload flow. Use only the CSV the user explicitly identifies for the session.

For each selected CSV:

1. Test selection feedback, validation/error state, upload, Data Analysis tab creation, and the Home-tab round trip.
2. Inspect visible data quality signals: schema/column labels, preview rows, metrics, summaries, insight text, chart titles, axes, legends, labels, values, empty states, and errors.
3. Use approved in-app controls or prompts to create at least one suitable view, such as a numeric distribution, categorical/top-value view, or two-column comparison.
4. Check that visible insights and charts are consistent with visible preview data and selected columns. Record unverified precision as a limitation instead of guessing.
5. Report missing data, nonsensical summaries, contradictory insights, misleading chart labels, unusable chart interaction, or broken chart rendering as defects.

If the user has not identified an appropriate CSV or the normal upload flow cannot use it, stop that scenario and record it as blocked.

## Phase 1: Scope

1. Confirm the visible browser session is Argos Venture. Record route, signed-in role, viewport, theme, open tabs, and existing state that must not be disturbed.
2. Capture a visual baseline and inspect the New Session launcher without creating anything.
3. Ask:

   *"I detected Argos Venture already running through `start-macos.sh` at `http://127.0.0.1:7861`. I will prioritize New Session/New Tab, each resulting workspace tab, and a Home-tab round trip for every tested session. Which session types and downstream tab flows should I test? Please provide the approved account/role, disposable data, permitted state-changing actions, target viewport sizes, and any response-time concern. For Data Analysis, select a non-sensitive CSV for the normal upload flow. For Live Capture, provide or make a short approved audio recording before testing begins."*

4. If Live Capture is requested without confirmed audio, ask for the prerequisite and wait.

## Phase 2: Plan and approval

Present a Markdown checklist covering:

- guardrails, permitted data/actions, exclusions, and blockers;
- New Session/New Tab as the majority of coverage;
- each selected type: validation, creation, resulting tab, connected UI, Home-tab round trip, keyboard, and responsive checks;
- Yi/AI progress, usability while waiting, response-delay evidence, and pending-response tab round trip;
- Data Analysis CSV, preview, insight, and chart checks when selected;
- Live Capture only after audio readiness; otherwise show it as blocked.

End with:

*"Please review the browser-only test plan. If it looks correct, reply 'Approve' to execute the loop."*

Do not execute beyond the baseline until the user says `Approve`.

## Phase 3: Approved execution and issue capture

1. Work only in the existing visible Venture browser session.
2. Keep New Session/New Tab as the majority of testing unless the user explicitly approves another focus.
3. Verify each selected session's complete downstream tab/component chain and Home-tab round trip before moving to unrelated areas.
4. Observe Yi/AI feedback, delay, usability, and recovery for every model-backed request.
5. For Data Analysis, assess visible insight/chart quality only against visible preview evidence.
6. Stop blocked scenarios instead of bypassing them.
7. Maintain `.codex/discovered_ui_issues.md` with:

   ```markdown
   # UI Discovery Report

   ## Session
   - Product/runtime: Argos Venture (`argos-venture`)
   - Browser route(s):
   - Account role(s) tested:
   - Viewport(s):
   - New Session/New Tab types tested:
   - Resulting tabs and connected UI covered:
   - Home-tab round trips:
   - Yi/AI response timing observations:
   - Data Analysis CSVs tested:
   - Test limitations/blockers:

   ## Confirmed Issues
   | ID | Severity | Category | Originating Session Type/Tab | Area/Route | Reproduction Steps | Expected Result | Actual Result | Evidence |
   |---|---|---|---|---|---|---|---|---|

   ## Passed Coverage
   - [ ] Session type → resulting tab → Home round trip → connected UI

   ## Not Tested / Blocked
   - Scenario, reason, and required user action
   ```

8. Do not change application code or attempt fixes.

## Phase 4: Sign-off and GitHub issue

1. Show a table of known/discovered issues, including category, originating session type/tab, severity, affected flow, reproducibility, and evidence. Use `Performance` for Yi/AI response findings.
2. Separately summarize New Session-to-tab coverage, Home-tab round trips, response timing observations, Data Analysis coverage, and blocked scenarios.
3. Ask:

   *"Would you like me to automatically bundle these findings and publish a GitHub issue via the `gh` CLI tool?"*

4. Publish only after explicit final approval:

   ```bash
   gh issue create --title "UI Discovery Report: [Today's Date]" --body-file=.codex/discovered_ui_issues.md
   ```

5. Report the created issue URL or concise failure. Do not retry, alter authentication, or publish partial findings without another user instruction.
