# Venture Analysis Planner

Argos can use a server-configured, OpenAI-compatible model to select the most useful **four to six** charts and insight cards for each uploaded CSV.

## Configuration

Set these environment variables on the Venture server:

```bash
ARGOS_ANALYSIS_PLANNER_ENDPOINT=https://your-model-host/v1
ARGOS_ANALYSIS_PLANNER_MODEL=your-planning-model
ARGOS_ANALYSIS_PLANNER_API_KEY=optional-api-key
ARGOS_ANALYSIS_PLANNER_TIMEOUT_SECONDS=12
```

`ARGOS_ANALYSIS_PLANNER_ENDPOINT` may be either an OpenAI-compatible base URL or a full `/chat/completions` URL. The key is optional for local models that do not require authentication.

## What the planner receives

The planner receives a metadata-only candidate catalog:

- User-supplied analysis objective
- Dataset row/column counts and missingness rate
- Column names, types, inferred roles, and missingness rates
- IDs, chart types, and associated fields for server-generated visualization candidates
- IDs and titles for server-generated insight-card candidates

It does **not** receive CSV rows, sample values, top-category values, numeric aggregates, chart data, or executable code.

## Safety and fallback behavior

The model may only select IDs that the server has already generated from Polars analysis. It cannot write chart data or factual claims. Invalid, unavailable, or timed-out model responses fall back to a deterministic ranking. Both paths always produce at least four fact-checked chart candidates and four insight cards when a dataset is uploaded.

The final selection is stored with the analysis workspace. Reopening a tab reuses that selection instead of calling the planner again.
