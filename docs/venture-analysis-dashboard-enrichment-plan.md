# Venture Data Analysis Dashboard Enrichment Plan

## Purpose

The Data Analysis workspace already provides a dependable baseline: Polars profiling, ECharts visualizations, objective-aware insight cards, an optional planner, and a transparent selection trail.

The next iteration should make the dashboard more useful for real uploaded datasets by **retaining the existing generic cards as the default orientation layer** and adding richer, evidence-backed findings and visuals whenever the dataset supports them.

This is a design and implementation plan. It does not change the current safety boundary: all calculations stay server-side, and any LLM may rank only server-generated candidates.

## Product rule: preserve the baseline

Do not remove or hide the current generic cards. They establish context for every CSV and must remain visible even when the dataset is sparse or semantically ambiguous.

### Dataset overview baseline

Keep these as the first-level orientation cards:

- Dataset footprint
- Field mix
- Data completeness
- Analysis readiness
- Generic data-quality, duplicate, category, numeric-relationship, and outlier cards when applicable

Label this layer **Dataset overview**. It should answer: what was uploaded, how complete it is, and what kinds of analysis are possible.

## Add a second layer: data-specific findings

Generate additional candidates on top of the baseline. These should provide different analytical approaches rather than multiple variants of the same observation.

### Display targets

- Always show the baseline cards.
- Always show at least four cards and four charts after upload.
- Rich datasets should generate at least 8 chart candidates and 10 insight-card candidates.
- For rich datasets, select 6–8 charts and 6–10 cards in total.
- Keep at least two baseline cards visible for orientation.
- Preserve at least one prominent data-quality card whenever quality issues could affect interpretation.
- Do not add filler. When a dataset supports only foundational analysis, call cards **Dataset overview** or **Data readiness**, not business findings.

### Analytical lens diversity

Avoid repeated versions of a single signal. Prefer a selected set that spans these lenses where available:

1. Overview
2. Time and change
3. Contribution and concentration
4. Segment comparison
5. Distribution
6. Relationship
7. Exceptions
8. Quality
9. Objective-specific evidence

## Semantic profiling layer

Add a conservative semantic profiler, for example `src/venture_analysis_semantics.py`, that infers analytical roles from column name, dtype, cardinality, and value shape.

### Candidate roles

- Date or datetime
- Identifier
- Metric
- Currency, revenue, sales, profit, cost, margin
- Quantity, units, volume
- Percentage or rate
- Category, product, SKU
- Customer, account, user
- Geography, region, country, state, city, territory
- Funnel stage or status
- Cohort, signup, first activity
- Price or discount
- Operational duration, latency, delivery time
- Satisfaction, rating, score
- Boolean flag
- Target or outcome

Attach confidence scores and inferred capabilities. Do not claim a semantic role when evidence is weak.

Example:

```json
{
  "fields": {
    "revenue": {
      "dtype": "Float64",
      "roles": ["metric", "currency", "revenue"],
      "confidence": 0.95,
      "usable_for": ["trend", "ranking", "distribution", "contribution", "relationship"]
    }
  },
  "primary_time_field": "order_date",
  "primary_metric_fields": ["revenue", "profit"],
  "primary_dimension_fields": ["region", "product_category"],
  "semantic_capabilities": ["time_series", "commercial_analysis", "segment_analysis"]
}
```

Use conservative synonym groups, including:

- Revenue: `revenue`, `sales`, `net_sales`, `sales_amount`, `amount`, `booking_value`
- Profit: `profit`, `gross_profit`, `margin`, `contribution_margin`
- Customer: `customer`, `client`, `account`, `user`, `member`
- Geography: `region`, `territory`, `market`, `country`, `state`, `city`
- Time: `date`, `created_at`, `order_date`, `timestamp`, `month`, `week`
- Product: `product`, `sku`, `item`, `category`, `plan`, `service`
- Funnel: `stage`, `status`, `funnel_step`, `lifecycle_stage`

## Candidate engine

Create a candidate engine, for example `src/venture_analysis_candidates.py`, that produces fact-checked chart and card candidates from semantic roles and deterministic statistical evidence.

Each candidate should include:

```json
{
  "id": "...",
  "family": "...",
  "title": "...",
  "subtitle": "...",
  "kind": "...",
  "priority": 0,
  "fields": ["..."],
  "semantic_roles": ["..."],
  "eligibility": {"reason": "...", "confidence": 0.0},
  "evidence": {"facts": [], "metrics": {}, "method": "..."},
  "selection_reasons": [],
  "insight_card": {}
}
```

All candidate values, facts, and claims must be calculated server-side. The LLM may rank candidate IDs but may not create chart specifications, values, or claims.

## Additional insight-card families

### Trend and change

- Largest increase or decrease over time
- Recent period versus prior period
- Strongest growth segment
- Largest declining segment
- Highest or lowest observed period
- Seasonality or recurring pattern
- Spike or dip anomaly

### Contribution and concentration

- Top category contribution to a metric
- Pareto concentration, such as top three categories' share of total
- Largest positive or negative contributor
- Long-tail concentration warning
- Segment share changing over time

### Segment comparison

- Highest versus lowest region, product, plan, channel, or customer segment
- Fastest-growing segment
- Highest-variance segment
- Segment with unusual missingness
- Segment disparity ratio

### Distribution and exceptions

- Mean versus median divergence
- Skewed metric
- High-variance metric
- Outlier count and proportion
- Extreme segment or period
- Unusual combinations of two metrics

### Relationships and drivers

- Strongest observed numeric association
- Metric relationship by segment
- Price-volume, discount-margin, units-revenue, duration-status, or other semantically suitable relationship
- Weak, nonlinear, or sparse relationship caveat

Every relationship finding must say that association does not establish causation.

### Data quality

- Missingness concentrated by column, segment, or time period
- Duplicate-row concentration
- Identifier-heavy or low-information columns
- Suspiciously constant columns
- High-cardinality categories that may need grouping
- Field-type mismatch, such as numeric-looking text or malformed dates

### Objective-specific evidence

Use the stated objective to add direct, evidence-backed candidates:

- Revenue drivers: contribution, region/product comparison, trend, relationship, discount/margin evidence
- Unusual delays: duration distribution, outliers, time anomalies, status or queue comparison
- Retention: cohorts, repeat activity, churn by plan or segment
- Conversion funnel: stage drop-off, conversion by channel, stage trend
- Regional performance: region ranking, regional growth, regional concentration

## Chart candidate families

Keep the current generic charts as dependable baseline candidates:

- Distribution
- Boxplot
- Top categories
- Data quality
- Correlation
- Outliers
- Field profile
- Row completeness

Add richer candidates where data supports them:

- Rolling trend and period-over-period change
- Ranked contribution or Pareto chart
- Stacked composition over time
- Growth by segment
- Segment comparison bars
- Scatter with a regression or trend line
- Heatmap of metric by time and category
- Seasonality heatmap
- Funnel chart
- Cohort or retention heatmap
- Price-volume or discount-margin scatter
- Missingness by time or segment
- Waterfall contribution chart when data supports it

### ECharts support

Extend the existing renderer only for meaningful candidates:

- Line with rolling average
- Area and stacked area
- Grouped and stacked bar
- Pareto bar with cumulative line
- Scatter with optional regression line
- Histogram
- Boxplot
- Heatmap
- Funnel
- Waterfall when commercial contribution data supports it
- Calendar or seasonality heatmap when time granularity supports it

Use Canvas for dense scatter and heatmap views. Use SVG for lighter visuals. Preserve ARIA descriptions and responsive resizing.

## Selection behavior

The planner should select from baseline and enriched candidates.

### Rules

1. Preserve at least two baseline cards for orientation.
2. Preserve at least one data-quality card if quality issues materially affect interpretation.
3. Prefer 4–6 enriched cards for rich datasets.
4. Prefer 4–6 enriched charts for rich datasets.
5. Avoid near-duplicate cards and visuals.
6. Preserve lens diversity across overview, time, segment, contribution, relationship, distribution, exception, and quality.
7. When an objective exists, reserve at least two selected cards and two selected charts for direct objective relevance when supported by data.
8. Do not select candidates merely because they are available; favor candidates with stronger evidence and clearer user value.

### Planner boundary

Keep the existing guarded planner boundary:

- Send metadata only: objective, field names, inferred roles, dtypes, missingness, dataset shape, candidate IDs, candidate family, and evidence labels.
- Do not send CSV rows, sample values, category values, numeric aggregates, chart points, or executable code.
- The model may only choose candidate IDs and optionally apply a constrained selection label:
  - `objective_match`
  - `trend_signal`
  - `segment_comparison`
  - `anomaly_detection`
  - `data_quality`
  - `relationship_analysis`
  - `concentration_analysis`
- Persist the selection plan after upload and reuse it on tab restore.

### Deterministic fallback

If no configured model is available:

- Rank direct objective matches first.
- Then rank candidates by strength of deterministic evidence.
- Keep quality findings when they affect interpretation.
- Prefer diversity over duplicate chart families.
- Preserve objective-aware selections when supported by the data.

## Dashboard layout

Keep the workspace conversation-free and organize it in layers.

### 1. Dataset overview

- Existing generic cards
- Dataset health and readiness
- Columns, profile, and preview

### 2. Key findings

- Highest-priority data-specific cards
- Strongest evidence-backed findings
- Objective-specific findings when present

### 3. Visual evidence

- Four to six primary charts for the strongest findings
- Baseline charts retained as supporting analysis where useful

### 4. Further signals

- Additional cards and charts
- Quality caveats
- Alternative analytical lenses

### 5. Why these were selected

Preserve and expand the transparent decision trail. It should explain:

- Whether an item is baseline context, objective-driven, data-signal-driven, planner-selected, or deterministic fallback
- Matched objective terms and fields
- Inferred field roles
- Triggering statistical evidence
- Method used
- Caveats and unavailable analyses

Do not expose hidden model reasoning.

### Unavailable analysis

Show a compact **Not available** section for high-value analyses that cannot be performed, for example:

- No usable date field for trend analysis
- No customer identifier plus activity date for cohorts
- Fewer than two numeric metrics for relationship analysis
- No stage field for funnel analysis

This should explain limits without fabricating a result or presenting an error state.

## Testing plan

Add fixtures for:

1. Sales: date, region, product, revenue, cost, units, discount
2. SaaS/customer: signup_date, activity_date, customer_id, plan, churned, MRR
3. Funnel: date, channel, stage, conversion, revenue
4. Operations: timestamp, status, duration, queue, region
5. Poor quality: duplicates, missing segment data, identifier-heavy columns

Assert that:

- Semantic inference is conservative and correct.
- Generic baseline cards remain visible.
- Dataset-specific candidates are added and prioritized when evidence supports them.
- At least four charts and four cards always render after upload.
- Rich datasets generate at least eight chart candidates and ten card candidates.
- A revenue-drivers objective prioritizes revenue, region, relationship, contribution, and trend candidates.
- An unusual-delays objective prioritizes duration distribution, outliers, time anomalies, and operational segments.
- The LLM cannot select unknown IDs.
- The deterministic fallback remains objective-aware.
- Raw rows and category values never appear in the planner payload.
- Every rendered card and chart has selection reasons and evidence metadata.
- Unsupported analyses appear as Not available instead of failed or fabricated.

## Delivery checklist

- Keep Polars as the analytical source of truth.
- Avoid new dependencies unless they materially improve the existing ECharts or Polars path.
- Run `python -m compileall -q app.py core routes src services scripts tests`.
- Run `node --check` on modified JavaScript files.
- Run focused tests and full pytest where practical.
- Summarize changed files, test results, and remaining limitations.
- Do not merge PR #22 automatically.
