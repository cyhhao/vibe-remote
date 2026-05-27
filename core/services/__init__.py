"""Domain service layer.

A thin, named seam between the storage layer and every caller (UI server,
CLI, IM adapter, controller, internal endpoints). Each module here is the
**single business API** for one domain — callers should import from
``core.services.<domain>`` and never reach into ``storage.*`` directly so
field semantics stay aligned across processes.

See ``docs/plans/workbench-dispatch-architecture.md`` §6 for the
conventions: free functions, take ``Connection`` as first arg, no engine
ownership, no side effects (SSE / logging belongs in the caller).
"""
