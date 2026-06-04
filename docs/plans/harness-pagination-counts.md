# Harness Pagination and Counts

## Background

The Workbench Harness page currently loads every scheduled task or watch, then
filters and counts rows in React. Large inventories, especially old paused
watches, make the page pay the cost of data the user is not viewing.

## Goal

- Fetch tasks and watches by the active filter instead of loading all rows.
- Page task, watch, and run lists consistently.
- Drive tab badges and filter summaries from backend counts, not loaded arrays.

## Design

- Keep existing store `list_*` methods as internal all-row APIs for scheduler
  reconciliation.
- Add dedicated page queries for scheduled definitions, watches, and runs.
- Add aggregate count helpers that compute all/enabled/disabled buckets in SQL.
- Update UI routes to return `{items, counts, page, limit, has_more, total}`.
- Update the Harness page to keep page state per tab and refresh the active
  tab/counts through server-side filters.

## Validation

- Focused Python tests for store pagination/counts and UI route payloads.
- TypeScript build for the Workbench UI.
