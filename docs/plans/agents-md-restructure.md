# AGENTS.md Restructure Plan

> Status: Completed

## Background

`AGENTS.md` currently contains most of the right rules, but the document is hard to scan during real work:

- reusable workflow rules and project-specific rules are split in ways that force agents to jump around,
- architecture, structure, and workflow guidance overlap,
- some operational guidance is outdated or underspecified,
- and the distinction between local `vibe` and the Docker-based three-end regression environment is not explicit enough.

This causes avoidable mistakes, especially around environment selection, UI verification, and where changes should be made in a multi-platform codebase.

## Goal

Rewrite `AGENTS.md` so it becomes a practical operating manual for coding agents working in Vibe Remote. The new version should clearly communicate:

1. the project's purpose and current operating model,
2. the architecture and design philosophy,
3. the expected development and review workflow,
4. the difference between local runtime and three-end regression,
5. and the key safety constraints that must not be violated.

## Solution

1. Reorganize the document around how agents actually make decisions: context → architecture → environments → workflow → coding/testing → safety.
2. Merge overlapping sections such as architecture and structure into a single codebase map.
3. Add an explicit environment model that distinguishes local `vibe` from Docker three-end regression.
4. Add a hard rule: do not restart the local `vibe` service for verification; use the regression Docker environment unless the user explicitly asks otherwise.
5. Update configuration and routing guidance so it reflects the current V2 config model rather than legacy assumptions.
6. Keep low-frequency release notes brief and move them to the end.

## Todo

- [x] Audit the current `AGENTS.md` and extract rules that must be preserved.
- [x] Design a cleaner section structure with clearer priorities.
- [x] Rewrite `AGENTS.md` with the new structure and updated guidance.
- [x] Verify the new document still preserves critical workflow, testing, and safety rules.
