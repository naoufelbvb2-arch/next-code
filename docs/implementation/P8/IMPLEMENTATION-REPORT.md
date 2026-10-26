# P8 verified observers and Windows native exit evidence

Authority: owner P8 FINAL CORRECTIONS (2026-10-25), Parts A/B/E/F. Branch
`feature/p8-verified-worker-observers`, starting from verified
`5e7179b9d528e7ef97e18f91ec284e4da514ca0b`. No main merge, paid requests,
Desktop vendoring, production workspace or public TaskOutcome/signature change.

Three optional synchronous callbacks use the existing task-local ExecutionBinding:
phase_changed(task_id, phase), worker_started(task_id, worker_ref, role), and
worker_completed(task_id, worker_ref, report). Legacy four-method observers and
observer=None remain compatible. VERIFIED_OBSERVER_VERSION=1 is separately pinned
by Desktop; EXECUTION_CONTROL_VERSION remains 1. Exceptions propagate safely.

LeadArchitectLoop records phases only after safety-gate acceptance, deduplicates
unchanged phases, and publishes after durable Blackboard transition. Gate 4
records actual VERIFYING instead of rejected COMPLETED; Gate 1 rejected dispatch
records no proposed phase. Workers receive UUID invocation identities after setup
and role validation, immediately before awaited execution. Only a typed matching
WorkerReport after chassis termination/memory wipe publishes completion. FAILED
and BLOCKED reports retain their status without deciding TaskOutcome. Cancel,
setup failure, gateway error or missing cleanup evidence never fabricates a report.

## Windows correction and limits

The P8 historical held-handle probe observed Job active-zero before a process
handle signaled (WAIT_TIMEOUT=258). No permanent orphan was established. The old
accounting-only check was therefore insufficient for immediate native exit proof.
WindowsToolJob now duplicates the suspended root handle before resume and retains
creation-bound handles to actual Job members while they run. It terminates the
non-breakaway Job, waits on retained handles under one shared five-second deadline,
then reconciles TotalProcesses against the complete retained inventory and checks
ActiveProcesses=0. A process missed by polling, membership/read/access failure,
inventory over 1024 or unsignaled handle raises CleanupUnverified. Polling is only
for membership discovery; no fixed delay is used to infer exit. PID disappearance,
psutil, termination acceptance and completion-port hints cannot substitute for a
handle. Kill-on-close remains active on failure. Desktop must still prove its
independent outer Job/identity cleanup before lease release; no lease policy changed.

Microsoft documents process-handle waits separately from Job accounting and warns
that ordinary Job completion notifications are not guaranteed:
[Job completion notifications](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_associate_completion_port),
[WaitForSingleObject](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject).

## Actual verification

Full isolated Windows offline suite: **69 PASS / 0 FAIL / 3 paid NOT_RUN**.
This is 53 prior cases plus 16 new cases, counted once. The new suite uses actual
AgentOS, real ephemeral chassis and Offline Gateway: repeated roles with distinct
IDs, typed SUCCESS/FAILED/BLOCKED reports, phase dedup/Gate 1/Gate 4, callback
exceptions, phase cancellation, worker cancellation/exception/setup failure,
role mismatch, and parent/descendant native handles under cancellation, timeout,
failure and forced parent termination. A missing-handle fault cannot be certified
by active-zero. Existing test_shell_cancel_stops_owned_child_and_descendant and
its immediate psutil assertion are unchanged. Native tests retain parent and child
handles before cancellation, check real Job membership and require both already
signaled on tool return, independently of psutil. Old no-observer tests still run.

Historical Desktop native PASS/PASS/FAIL and WAIT_TIMEOUT evidence is retained,
not relabeled. During this correction, a new fault-test harness initially
recaptured its intentionally discarded handle, and a new readiness-file fixture
could be read before its write completed. Those test setup defects were corrected
(explicit evidence loss and atomic readiness publication); failed raw runs remain
in Desktop's ignored evidence cache. Production assertions were not weakened.

No full Desktop G5, full-system acceptance, P6 G6, packaging or owner acceptance is
implied by this Engine subset. Rollback is a reviewed branch revert; do not alter
main or erase prior evidence. Changed files: kernel/execution.py,
architect/lead_loop.py, squad/dispatcher.py, kernel/owned_process.py,
tests/test_verified_observers.py and this report, plus a later commit receipt.

Release JUnit SHA-256: `e8e4654d2bd002ae1d159e67261118826002a7dfc760f1759f275cbea8c62108`.
