# P7 optional lifecycle and cancellation implementation

Owner authority: P7 ENGINE INTEGRATION FIX approving Desktop's existing
ENGINE-CHANGE-REQUEST. Separate engine branch `feature/p7-desktop-lifecycle-control`
starts at verified origin/main `834d0ca62cc9b2c03e530bafe4090024d98f31d3`.
No main merge, Desktop source copy, paid provider calls or P8/P9 work.

AgentOS accepts optional keyword-only `observer` and `cancellation`. The real
LeadArchitectLoop emits `started(task_id, workspace_root)` on entering run, before
its baseline checkpoint. AgentOS passes the actual returned TaskOutcome object
to `outcome`; failures without an outcome call `failed` and preserve the exception.
`cancelled` occurs only after a TaskCancelled unwinds the awaited execution path.
TaskOutcome retains its seven fields; TaskPhase and old caller signatures remain
compatible. Observer methods are synchronous, task scoped, and must not block.
An observer error fails execution rather than inventing delivery or completion.

The optional thread-safe control travels through a task-local ContextVar and
asyncio.to_thread's copied context. Architect/worker Gateway waits cancel and drain
their awaited coroutine. Tool threads are awaited through completion and never
abandoned by cancelling a Future. Checks prevent new worker/tool dispatch after
observing a request. Chassis finally still wipes memory. Existing checkpoint and
rollback gates remain in place; cancellation does not roll back existing writes.
A tool that cannot finish prevents cooperative confirmation; Desktop's independent
15-second Job escalation is still required and remains separate evidence.

Controlled Windows shell commands start suspended, enter a private non-breakaway
Job, then resume. On cancellation, timeout and normal return, Job termination and
active-process accounting must establish zero owned processes before returning.
Cleanup uncertainty propagates past the ToolResult error wrappers and cannot
produce `cancelled`. Desktop must still verify its own outer Job and workspace
identity independently before releasing a writer. Windows ownership evidence is
platform specific; no equivalent POSIX descendant-containment certification is
claimed. The omitted-control shell path retains its previous behavior.

## Actual verification

Windows CPython 3.14.4, isolated local `.venv`, exact packages recorded in
`requirements-p7-test.lock`. No new runtime requirement: pydantic/httpx/psutil
already exist in pyproject. No global/admin installation. Tests used new OS temp
Git workspaces outside both production checkouts, with synthetic baseline files,
explicit patch targets, and offline Gateways. Provider credentials were removed;
external socket connections were rejected (Windows asyncio loopback remained
available). No actual coding task targeted either source repository.

Final full regression: **53 PASS / 3 SKIPPED / 0 FAIL**. The three original live
DeepSeek tests skipped because no key was passed; they are NOT_RUN, not PASS.
The 14 new cases cover old/no-control and controlled calls, real lifecycle source
and identity, same-object seven-field outcome, failed outcome, pre-cancel/no-write,
architect and worker Gateway cancellation/draining/memory wipe, exception without
invented outcome, a drained blocking tool, actual Windows shell+descendant stop,
unverified-cleanup failure, actual narrow file patch+Inquisitor+C-01+terminal race,
tool failure and preservation of already-written files after cancellation.
The 39 original offline regressions pass unchanged.

Reproduce: install `requirements-p7-test.lock` into a local venv, then run
`python tests/run_offline.py -q`. The runner creates a fresh external task root,
strips provider credentials and prohibits external sockets. Verification records
contain sanitized test identities and code hashes, not raw machine paths or logs.

## Delivery, limits and recovery

Review the exact source/test/doc paths and bytes before a normal feature-branch
commit/push. Baseline and final commit provenance belong in the later Desktop
receipt/final response, never a self-referential hash. Main is unchanged.
Reverting this optional patch is a reviewed new commit, not destructive cleanup;
retain any external fixture and checkpoint evidence. Do not re-execute a task
automatically. Desktop compatibility, real bridge, outer Job, SQLite lease and
crash/restart evidence are separate obligations and were not certified by this
engine-only suite. No provider egress, installer or detailed P8 events were added.
