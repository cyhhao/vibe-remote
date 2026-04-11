# Routing Current Backend And Claude Catalog

## Background

Slack Agent Settings currently gathers backend data before opening the modal.
In practice this can trigger expensive Claude model discovery even when the
user is currently routed to OpenCode, causing the Slack trigger to expire.

## Goal

1. Only load backend-specific data for the backend the user is currently using
   when the routing modal first opens.
2. Stop scanning the local Claude installation during normal runtime model
   discovery.
3. Ship a repository-owned Claude model catalog plus a script that regenerates
   that catalog from the current Claude installation when maintainers want to
   refresh it for a release.

## Plan

1. Add a tracked Claude model catalog file and make runtime model discovery read
   from it, while still merging explicit user-configured model values.
2. Add a maintainer script that scans the current Claude installation bundle,
   infers model ids, and rewrites the tracked catalog file.
3. Update routing modal data collection so initial modal open fetches only the
   current backend, while modal refresh fetches only the currently selected
   backend.
4. Add focused tests for the new catalog behavior and backend-scoped routing
   modal data loading.
