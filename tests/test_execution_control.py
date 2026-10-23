"""Actual offline AgentOS, workers, tools and owned Windows descendants."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import psutil
import pytest

from agent_os import AgentOS
from architect.contracts import ArchitectDecision, TaskOutcome
from kernel.execution import CancellationControl, TaskCancelled, CleanupUnverified, binding
from squad.chassis import StandardAgentChassis
from squad.schemas import ChassisStepDecision


def decision(action="COMPLETE_TASK", role=None):
    value = dict(situation_assessment="Offline synthetic fixture",
        mental_simulation=dict(hypothesis="Bounded test", verification_strategy="Assertions"),
        new_phase="COMPLETED" if action == "COMPLETE_TASK" else "EXECUTING",
        next_action=action, final_response="Actual offline result")
    if role:
        value["task_contract"] = dict(contract_id="contract_fixture", target_role=role,
            objective="Synthetic fixture only", target_files=["sample.py"], context_briefing="Test")
    return ArchitectDecision.model_validate(value)


class OfflineGateway:
    def __init__(self, replies=(), wait_at=None, error=None):
        self.replies = list(replies)
        self.calls = []
        self.wait_at = wait_at
        self.entered = asyncio.Event()
        self.drained = False
        self.error = error

    async def generate_structured(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == self.wait_at:
            self.entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.drained = True
        if self.error:
            raise self.error
        reply = self.replies.pop(0)
        assert isinstance(reply, kwargs["response_model"])
        return reply, None


class Observer:
    def __init__(self):
        self.records = []

    def started(self, tid, workspace):
        # Invocation must originate in LeadArchitectLoop.run, not in the bridge.
        import inspect
        assert inspect.currentframe().f_back.f_code.co_name == "run"
        self.records.append(("started", tid, workspace))

    def outcome(self, value):
        assert isinstance(value, TaskOutcome)
        self.records.append(("outcome", value))

    def cancelled(self, tid, workspace):
        self.records.append(("cancelled", tid, workspace))

    def failed(self, tid, workspace, error):
        self.records.append(("failed", type(error).__name__))


@pytest.fixture
def workspace(tmp_path):
    # pytest base directory must be external to both implementation checkouts.
    assert not tmp_path.resolve().is_relative_to(Path(__file__).resolve().parents[1])
    tmp_path.joinpath("sample.py").write_text("def value(): return 1\n")
    git = ["git", "-c", "safe.directory=" + tmp_path.as_posix(), "-C", str(tmp_path)]
    for args in (["init"], ["config", "user.name", "Synthetic Test"],
                 ["config", "user.email", "synthetic@example.invalid"],
                 ["add", "sample.py"], ["commit", "-m", "Synthetic baseline"]):
        subprocess.run(git + args, capture_output=True, check=True)
    return tmp_path


@pytest.mark.parametrize("controlled", [False, True])
async def test_real_lifecycle_outcome_and_old_call(workspace, controlled):
    gateway, observer = OfflineGateway([decision()]), Observer()
    engine = AgentOS(workspace, gateway=gateway)
    kwargs = dict(observer=observer, cancellation=CancellationControl()) if controlled else {}
    outcome = await engine.execute_task("Exact fixture goal", "task_fixture", **kwargs)
    assert outcome.success and len(outcome.model_dump()) == 7
    assert (workspace / ".agent_os/state/task_fixture.json").exists()
    assert len(gateway.calls) == 1
    if controlled:
        assert observer.records == [("started", "task_fixture", workspace), ("outcome", outcome)]
        assert observer.records[-1][1] is outcome
    assert binding.get().control is None


@pytest.mark.parametrize("action", ["ABORT_TASK", "COMPLETE_TASK"])
async def test_actual_failed_outcome_and_late_cancel(workspace, action):
    control, observer = CancellationControl(), Observer()
    value = await AgentOS(workspace, gateway=OfflineGateway([decision(action)])).execute_task(
        "Fixture", "task_fixture", observer=observer, cancellation=control)
    control.request()
    assert value.success == (action == "COMPLETE_TASK")
    assert [r[0] for r in observer.records] == ["started", "outcome"]


async def test_precancel_does_not_start_or_write(workspace):
    control, observer = CancellationControl(), Observer()
    control.request()
    with pytest.raises(TaskCancelled):
        await AgentOS(workspace, gateway=OfflineGateway()).execute_task(
            "Fixture", "task_fixture", observer=observer, cancellation=control)
    assert not (workspace / ".agent_os").exists()
    assert [r[0] for r in observer.records] == ["cancelled"]


@pytest.mark.parametrize("worker", [False, True])
async def test_gateway_and_worker_cancel_drains_before_confirmation(workspace, monkeypatch, worker):
    control, observer = CancellationControl(), Observer()
    replies = [decision("DISPATCH_SCOUT", "SCOUT")] if worker else []
    gateway = OfflineGateway(replies, wait_at=2 if worker else 1)
    terminated = []
    original = StandardAgentChassis._on_terminate
    def terminate(self):
        original(self)
        terminated.append((self.is_alive, len(self.memory.history)))
    monkeypatch.setattr(StandardAgentChassis, "_on_terminate", terminate)
    task = asyncio.create_task(AgentOS(workspace, gateway=gateway).execute_task(
        "Fixture", "task_fixture", observer=observer, cancellation=control))
    await asyncio.wait_for(gateway.entered.wait(), 5)
    control.request()
    with pytest.raises(TaskCancelled):
        await asyncio.wait_for(task, 5)
    assert gateway.drained
    assert terminated == ([(False, 0)] if worker else [])
    assert [r[0] for r in observer.records] == ["started", "cancelled"]
    assert len(gateway.calls) == (2 if worker else 1)


async def test_exception_has_no_invented_task_outcome(workspace):
    observer = Observer()
    with pytest.raises(RuntimeError):
        await AgentOS(workspace, gateway=OfflineGateway(error=RuntimeError("synthetic"))).execute_task(
            "Fixture", "task_fixture", observer=observer, cancellation=CancellationControl())
    assert [r[0] for r in observer.records] == ["started", "failed"]


async def test_blocking_tool_is_drained_and_no_next_tool_starts(workspace):
    control, observer = CancellationControl(), Observer()
    entered, finished = threading.Event(), threading.Event()
    gateway = OfflineGateway([decision("DISPATCH_SURGEON", "SURGEON"),
        ChassisStepDecision(thought="Synthetic tool", action_type="CALL_TOOL",
            tool_call=dict(tool_name="read_file", arguments={"path":"sample.py"}))])
    engine = AgentOS(workspace, gateway=gateway)
    def blocking(ctx, **kwargs):
        entered.set()
        while not control.requested:
            time.sleep(.01)
        # A blocking operation's cleanup must finish before cancellation is seen.
        time.sleep(.1)
        finished.set()
        control.check()
    engine.tool_registry.register("read_file", blocking)
    task = asyncio.create_task(engine.execute_task("Fixture", "task_fixture",
        observer=observer, cancellation=control))
    while not entered.is_set():
        await asyncio.sleep(.01)
    control.request()
    with pytest.raises(TaskCancelled):
        await asyncio.wait_for(task, 5)
    assert finished.is_set() and len(gateway.calls) == 2
    assert observer.records[-1][0] == "cancelled"


async def test_shell_cancel_stops_owned_child_and_descendant(workspace):
    if sys.platform != "win32":
        pytest.skip("Actual Windows nested Job evidence")
    # The subprocess and its descendant both remain alive until cancellation.
    workspace.joinpath("sleeper.py").write_text("import time; time.sleep(60)\n")
    workspace.joinpath("parent.py").write_text(
        "import subprocess,sys,time,os,json\n"
        "p=subprocess.Popen([sys.executable,'sleeper.py'])\n"
        "open('owned.json','w').write(json.dumps([os.getpid(),p.pid]))\n"
        "p.wait()\n")
    command = '"' + sys.executable + '" parent.py'
    gateway = OfflineGateway([decision("DISPATCH_SURGEON", "SURGEON"),
        ChassisStepDecision(thought="Owned synthetic command", action_type="CALL_TOOL",
            tool_call=dict(tool_name="run_command", arguments={"command":command,"timeout_sec":60}))])
    control, observer = CancellationControl(), Observer()
    task = asyncio.create_task(AgentOS(workspace, gateway=gateway).execute_task(
        "Fixture", "task_fixture", observer=observer, cancellation=control))
    until = time.monotonic() + 8
    while not (workspace / "owned.json").exists() and time.monotonic() < until:
        if task.done():
            await task
        await asyncio.sleep(.02)
    pids = json.loads((workspace / "owned.json").read_text())
    owned = [psutil.Process(pid) for pid in pids]
    control.request()
    with pytest.raises(TaskCancelled):
        await asyncio.wait_for(task, 8)
    assert all(not proc.is_running() for proc in owned)
    assert observer.records[-1][0] == "cancelled"


async def test_cleanup_failure_cannot_be_cooperative_confirmation(workspace):
    observer = Observer()
    gateway = OfflineGateway([decision("DISPATCH_SCOUT", "SCOUT"),
        ChassisStepDecision(thought="Synthetic fault", action_type="CALL_TOOL",
            tool_call=dict(tool_name="read_file", arguments={"path":"sample.py"}))])
    engine = AgentOS(workspace, gateway=gateway)
    def uncertain(*args, **kwargs):
        raise CleanupUnverified("Synthetic containment fault")
    engine.tool_registry.register("read_file", uncertain)
    with pytest.raises(CleanupUnverified):
        await engine.execute_task("Fixture", "task_fixture", observer=observer,
            cancellation=CancellationControl())
    assert [r[0] for r in observer.records] == ["started", "failed"]


async def test_real_coding_patch_verification_context_and_terminal_race(workspace):
    from architect.contracts import ConversationContext
    control, observer = CancellationControl(), Observer()
    gateway = OfflineGateway([
        decision("DISPATCH_SURGEON", "SURGEON"),
        ChassisStepDecision(thought="Exact synthetic patch", action_type="CALL_TOOL",
            tool_call=dict(tool_name="apply_patch", arguments={"path":"sample.py",
                "patches":[{"search":"return 1", "replace":"return 2"}]})),
        ChassisStepDecision(thought="Mutation complete", action_type="FINISH_TASK",
            final_report=dict(status="SUCCESS",summary="Patched sample.py")),
        decision("DISPATCH_INQUISITOR", "INQUISITOR"),
        ChassisStepDecision(thought="Verify actual content", action_type="CALL_TOOL",
            tool_call=dict(tool_name="read_file",arguments={"path":"sample.py"})),
        ChassisStepDecision(thought="Verification complete", action_type="FINISH_TASK",
            final_report=dict(status="SUCCESS",summary="Verified sample.py",inquisitor_verdict="APPROVED")),
        decision()])
    context = ConversationContext(schema_version=1, conversation_id="conversation_fixture",
        workspace_root=workspace, recent_messages=[{"role":"user","content":"Exact contextual message"}])
    original = observer.outcome
    def outcome(value):
        original(value)
        control.request()  # Actual result won; a late request cannot rewrite it.
    observer.outcome = outcome
    value = await AgentOS(workspace,gateway=gateway).execute_task(
        "Only change sample.py to return 2", "task_fixture", conversation_context=context,
        observer=observer,cancellation=control)
    assert value.success and value.final_checkpoint_hash
    assert workspace.joinpath("sample.py").read_text() == "def value(): return 2\n"
    assert "Exact contextual message" in gateway.calls[0]["messages"][1].content
    assert [r[0] for r in observer.records] == ["started","outcome"]


async def test_tool_failure_does_not_invent_success(workspace):
    observer = Observer()
    gateway = OfflineGateway([decision("DISPATCH_SCOUT", "SCOUT"),
        ChassisStepDecision(thought="Missing fixture file", action_type="CALL_TOOL",
            tool_call=dict(tool_name="read_file",arguments={"path":"missing.py"})),
        ChassisStepDecision(thought="Tool failure", action_type="FINISH_TASK",
            final_report=dict(status="FAILED",summary="Read failed")),
        decision("ABORT_TASK")])
    value = await AgentOS(workspace,gateway=gateway).execute_task(
        "Fixture", "task_fixture", observer=observer,cancellation=CancellationControl())
    assert not value.success
    assert [r[0] for r in observer.records] == ["started","outcome"]


async def test_cancellation_preserves_already_written_files(workspace):
    control, observer = CancellationControl(), Observer()
    gateway = OfflineGateway([decision("DISPATCH_SURGEON", "SURGEON"),
        ChassisStepDecision(thought="Write then cancellation race", action_type="CALL_TOOL",
            tool_call=dict(tool_name="apply_patch",arguments={"path":"sample.py",
                "patches":[{"search":"return 1","replace":"return 3"}]}))])
    engine = AgentOS(workspace,gateway=gateway)
    apply = engine.tool_registry.get_tool("apply_patch")
    def write_then_cancel(ctx, **kwargs):
        result = apply(ctx, **kwargs)
        assert result.success
        control.request()
        return result
    engine.tool_registry.register("apply_patch",write_then_cancel)
    with pytest.raises(TaskCancelled):
        await engine.execute_task("Fixture", "task_fixture",observer=observer,cancellation=control)
    assert workspace.joinpath("sample.py").read_text() == "def value(): return 3\n"
    assert [r[0] for r in observer.records] == ["started","cancelled"]
