"""Actual offline Engine evidence; faults are injected only in test-owned dependencies."""
import asyncio
import ctypes as c
from ctypes import wintypes as w
import json
import sys
import time

import pytest

from agent_os import AgentOS
from architect.contracts import WorkerReport
from brain.schemas import TaskPhase
from kernel.execution import CancellationControl, TaskCancelled, binding
from kernel.schemas import AgentRole, ExecutionContext
from kernel.tools.shell import run_command
from squad.chassis import StandardAgentChassis
from squad.schemas import ChassisStepDecision
from test_execution_control import OfflineGateway, Observer, decision, workspace


class VerifiedObserver(Observer):
    def phase_changed(self, tid, phase):
        assert tid == "task_fixture" and isinstance(phase, TaskPhase)
        self.records.append(("phase", phase))

    def worker_started(self, tid, ref, role):
        assert tid == "task_fixture" and isinstance(role, AgentRole)
        assert len(ref) == 32 and ref.isalnum()
        self.records.append(("worker_started", ref, role))

    def worker_completed(self, tid, ref, report):
        assert tid == "task_fixture" and isinstance(report, WorkerReport)
        self.records.append(("worker_completed", ref, report))


def finish(status="SUCCESS"):
    return ChassisStepDecision(thought="Offline report", action_type="FINISH_TASK",
        final_report=dict(status=status, summary="Actual returned report"))


async def test_repeated_roles_typed_reports_and_accepted_phase_dedup(workspace):
    observer = VerifiedObserver()
    gateway = OfflineGateway([decision("DISPATCH_SCOUT", "SCOUT"), finish("FAILED"),
        decision("DISPATCH_SCOUT", "SCOUT"), finish("BLOCKED"), decision()])
    outcome = await AgentOS(workspace, gateway=gateway).execute_task("Fixture", "task_fixture", observer=observer)
    starts = [r for r in observer.records if r[0] == "worker_started"]
    reports = [r for r in observer.records if r[0] == "worker_completed"]
    assert len(starts) == len(reports) == 2
    assert starts[0][1] != starts[1][1] and starts[0][2] == starts[1][2] == AgentRole.SCOUT
    assert [r[1] for r in starts] == [r[1] for r in reports]
    assert [r[2].status.value for r in reports] == ["FAILED", "BLOCKED"]
    assert outcome.success  # A worker report does not decide the whole task.
    assert [r[1] for r in observer.records if r[0] == "phase"] == [TaskPhase.EXECUTING, TaskPhase.COMPLETED]
    assert binding.get().observer is None


async def test_rejected_gate_one_phase_is_not_recorded(workspace):
    rejected = decision("DISPATCH_SURGEON", "SURGEON")
    rejected.new_phase = TaskPhase.PLANNING
    rejected.task_contract.target_files = []
    observer = VerifiedObserver()
    await AgentOS(workspace, gateway=OfflineGateway([rejected, decision()])).execute_task("Fixture", "task_fixture", observer=observer)
    assert [r[1] for r in observer.records if r[0] == "phase"] == [TaskPhase.COMPLETED]
    assert not any(r[0].startswith("worker") for r in observer.records)


async def test_premature_completion_is_replaced_by_actual_verification(workspace):
    observer = VerifiedObserver()
    patch = ChassisStepDecision(thought="Real patch", action_type="CALL_TOOL", tool_call=dict(
        tool_name="apply_patch", arguments=dict(path="sample.py", patches=[dict(search="return 1", replace="return 2")])))
    verified = ChassisStepDecision(thought="Actual verification report", action_type="FINISH_TASK", final_report=dict(
        status="SUCCESS", summary="Verification report", inquisitor_verdict="APPROVED"))
    gateway = OfflineGateway([decision("DISPATCH_SURGEON", "SURGEON"), patch, finish(), decision(), verified, decision()])
    await AgentOS(workspace, gateway=gateway).execute_task("Fixture", "task_fixture", observer=observer)
    phases = [r[1] for r in observer.records if r[0] == "phase"]
    assert phases == [TaskPhase.EXECUTING, TaskPhase.VERIFYING, TaskPhase.COMPLETED]
    assert observer.records[-2][0] == "phase" and observer.records[-1][0] == "outcome"


@pytest.mark.parametrize("hook", ["phase_changed", "worker_started", "worker_completed"])
async def test_callback_exception_fails_closed_after_chassis_cleanup(workspace, monkeypatch, hook):
    observer = VerifiedObserver()
    chassis = []
    original = StandardAgentChassis.__init__
    def track(self, *args, **kwargs):
        original(self, *args, **kwargs)
        chassis.append(self)
    monkeypatch.setattr(StandardAgentChassis, "__init__", track)
    def fault(*args):
        raise RuntimeError("Observer evidence unavailable")
    setattr(observer, hook, fault)
    with pytest.raises(RuntimeError, match="Observer evidence"):
        await AgentOS(workspace, gateway=OfflineGateway([decision("DISPATCH_SCOUT", "SCOUT"), finish()])).execute_task(
            "Fixture", "task_fixture", observer=observer, cancellation=CancellationControl())
    assert all(not ch.is_alive and not ch.memory.history for ch in chassis)
    assert not any(r[0] == "outcome" for r in observer.records)
    assert observer.records[-1][0] == "failed" and binding.get().observer is None


@pytest.mark.parametrize("fault", ["cancel", "exception", "setup"])
async def test_no_worker_report_on_cancel_exception_or_setup_failure(workspace, monkeypatch, fault):
    observer, control = VerifiedObserver(), CancellationControl()
    gateway = OfflineGateway([decision("DISPATCH_SCOUT", "SCOUT")], wait_at=2)
    if fault == "setup":
        def fail(*a, **kw):
            raise RuntimeError("Setup failed")
        monkeypatch.setattr(StandardAgentChassis, "__init__", fail)
    elif fault == "exception":
        gateway.wait_at = None  # exhausted offline replies raises at the real worker gateway
    task = asyncio.create_task(AgentOS(workspace, gateway=gateway).execute_task(
        "Fixture", "task_fixture", observer=observer, cancellation=control))
    if fault == "cancel":
        await asyncio.wait_for(gateway.entered.wait(), 5)
        control.request()
    with pytest.raises((TaskCancelled, RuntimeError, IndexError)):
        await asyncio.wait_for(task, 8)
    assert not any(r[0] == "worker_completed" for r in observer.records)
    assert sum(r[0] == "worker_started" for r in observer.records) == (0 if fault == "setup" else 1)
    assert not any(r[0] == "outcome" for r in observer.records)


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows handles")
@pytest.mark.parametrize("mode", ["cancel", "timeout", "failure", "forced"])
async def test_owned_parent_and_descendant_native_exit(workspace, mode):
    workspace.joinpath("sleeper.py").write_text("import time; time.sleep(60)\n")
    workspace.joinpath("parent.py").write_text(
        "import subprocess,sys,time,os,json\n"
        "p=subprocess.Popen([sys.executable,'sleeper.py'])\n"
        "from pathlib import Path;Path('owned.pending').write_text(json.dumps([os.getpid(),p.pid]));os.replace('owned.pending','owned.json')\n"
        + ("time.sleep(.3);sys.exit(7)\n" if mode == "failure" else "p.wait()\n"))
    # No test delay is used to infer exit: retained handles must already signal
    # when the controlled tool returns (including timeout and external kill).
    from kernel.execution import ExecutionBinding
    control = CancellationControl()
    ctx = ExecutionContext(task_id="task_fixture", role=AgentRole.SURGEON, workspace_root=workspace,
        allowed_files=frozenset(["sample.py"]))
    token = binding.set(ExecutionBinding(control, None))
    try:
        task = asyncio.create_task(asyncio.to_thread(run_command, ctx, '"' + sys.executable + '" parent.py', timeout_sec=2))
    finally:
        binding.reset(token)
    until = time.monotonic() + 5
    while not workspace.joinpath("owned.json").exists() and time.monotonic() < until:
        if task.done():
            await task
        await asyncio.sleep(.005)
    pids = json.loads(workspace.joinpath("owned.json").read_text())
    api = c.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]; api.OpenProcess.restype = w.HANDLE
    api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]; api.WaitForSingleObject.restype = w.DWORD
    api.TerminateProcess.argtypes = [w.HANDLE, w.UINT]; api.CloseHandle.argtypes = [w.HANDLE]
    api.IsProcessInJob.argtypes = [w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)]
    handles = [api.OpenProcess(0x100000 | 0x1000 | 1, False, pid) for pid in pids]
    try:
        assert all(handles)
        for handle in handles:
            member = w.BOOL()
            assert api.IsProcessInJob(handle, None, c.byref(member)) and member.value
            assert api.WaitForSingleObject(handle, 0) == 258
        if mode == "cancel":
            control.request()
            with pytest.raises(TaskCancelled):
                await asyncio.wait_for(task, 8)
        else:
            if mode == "forced":
                assert api.TerminateProcess(handles[0], 9)
            result = await asyncio.wait_for(task, 9)
            assert not result.success
        assert [api.WaitForSingleObject(handle, 0) for handle in handles] == [0, 0]
        import psutil
        assert all(not psutil.pid_exists(pid) for pid in pids)
    finally:
        for handle in handles:
            if handle:
                api.CloseHandle(handle)


async def test_phase_observer_cancellation_prevents_dispatch_and_outcome(workspace):
    control, observer = CancellationControl(), VerifiedObserver()
    def cancel(tid, phase):
        control.request()
    observer.phase_changed = cancel
    with pytest.raises(TaskCancelled):
        await AgentOS(workspace, gateway=OfflineGateway([decision("DISPATCH_SCOUT", "SCOUT")])).execute_task(
            "Fixture", "task_fixture", observer=observer, cancellation=control)
    assert [r[0] for r in observer.records] == ["started", "cancelled"]


async def test_role_mismatch_fails_before_worker_invocation(workspace):
    observer = VerifiedObserver()
    proposed = decision("DISPATCH_SCOUT", "SURGEON")
    with pytest.raises(ValueError, match="role does not match"):
        await AgentOS(workspace, gateway=OfflineGateway([proposed])).execute_task(
            "Fixture", "task_fixture", observer=observer)
    assert not any(r[0].startswith("worker") for r in observer.records)


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows handles")
def test_unobserved_process_cannot_be_certified_by_job_accounting_zero():
    import subprocess
    from kernel.owned_process import WindowsToolJob
    from kernel.execution import CleanupUnverified
    job = WindowsToolJob()
    proc = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"], creationflags=4)
    job.assign_and_resume(proc)
    # Deliberately discard private evidence, never alter production OS results.
    # Accounting still reaches zero but the absent handle must fail closed.
    job._stop_tracking.set(); job._tracker.join()
    handle = job._processes.pop(proc.pid)
    job._capture = lambda: None  # Test-owned loss of native evidence
    try:
        with pytest.raises(CleanupUnverified, match="native exit evidence"):
            job.stop_and_verify()
        assert job.api.WaitForSingleObject(handle, 5000) == 0
    finally:
        job.api.CloseHandle(handle)
        proc.wait(timeout=5)
