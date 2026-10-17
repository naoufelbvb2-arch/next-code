"""System and tick prompt builders for the Lead Architect."""

from architect.contracts import WorkerReport
from brain.schemas import ImpactReport


def build_architect_system_prompt() -> str:
    """Construct the immutable system prompt defining the Lead Architect's role and rules."""
    return (
        "You are the Principal Lead Architect of the Micro-Engineering Agent OS.\n\n"
        "Your sole responsibility is executive orchestration, rigorous mental simulation, "
        "and dispatching isolated, scoped TaskContracts to ephemeral worker agents (SCOUT, SURGEON, INQUISITOR).\n\n"
        "IRONCLAD ARCHITECTURAL INVARIANTS:\n"
        "1. ZERO DIRECT TOOL ACCESS: You do not have filesystem, search, or shell tools. "
        "You cannot read or mutate files directly. You MUST dispatch workers to perform operations.\n"
        "2. ANTI-AMNESIA: NEVER repeat any hypothesis or mutation strategy recorded in the "
        "'⚠️ GRAVEYARD OF FAILED HYPOTHESES'. Every attempt must be structurally distinct.\n"
        "3. SCOUT BEFORE SURGERY: Never blind-guess code or line numbers. Always dispatch SCOUT to verify "
        "exact line numbers, symbols, and factual evidence before modifying code.\n"
        "4. NO BLIND SURGERY: Every DISPATCH_SURGEON decision MUST include explicit target_files and clear acceptance_criteria.\n"
        "5. MANDATORY VERIFICATION BEFORE COMPLETION: You are strictly forbidden from choosing COMPLETE_TASK "
        "if code has been modified without receiving an APPROVED verdict from an INQUISITOR.\n"
        "6. BLAST RADIUS AWARENESS: Always evaluate the ImpactReport of target files in your mental_simulation "
        "to anticipate side effects on dependent modules before dispatching surgery.\n"
        "7. STRUCTURED DECISIONS ONLY: You must respond strictly conforming to the ArchitectDecision JSON schema."
    )


def build_architect_tick_prompt(
    playbooks_prompt: str,
    context_digest: str,
    repo_map: str,
    impact_reports: list[ImpactReport],
    last_worker_report: WorkerReport | None = None,
    gate_warning: str | None = None,
    tick_number: int = 1,
    max_ticks: int = 15,
) -> str:
    """Assemble clean-room context and operational directives for the current decision tick."""
    sections: list[str] = [
        f"### ORCHESTRATION CYCLE — TICK {tick_number} / {max_ticks}\n"
    ]

    # 1. Gate Override / Safety Warnings
    if gate_warning:
        sections.append(
            "================================================================================\n"
            "🚨 SAFETY GATE INTERCEPTION WARNING:\n"
            f"{gate_warning}\n"
            "You MUST adjust your plan and address this gate violation immediately.\n"
            "================================================================================\n"
        )

    # 2. Latest Worker Report (if previous tick dispatched a worker)
    if last_worker_report:
        facts_summary = (
            f"\n- Discovered Facts: {len(last_worker_report.discovered_facts)}"
            if last_worker_report.discovered_facts
            else ""
        )
        mod_summary = (
            f"\n- Modified Files: {', '.join(last_worker_report.modified_files)}"
            if last_worker_report.modified_files
            else ""
        )
        err_summary = (
            f"\n- Error Details: {last_worker_report.error_details}"
            if last_worker_report.error_details
            else ""
        )
        verdict_str = (
            f"\n- Inquisitor Verdict: {last_worker_report.inquisitor_verdict.value}"
            if last_worker_report.worker_role == "INQUISITOR"
            else ""
        )

        sections.append(
            "## 📋 LATEST WORKER REPORT (From Previous Tick)\n"
            f"- Contract ID: `{last_worker_report.contract_id}`\n"
            f"- Worker Role: `{last_worker_report.worker_role}`\n"
            f"- Status: `{last_worker_report.status.value}`\n"
            f"- Summary: {last_worker_report.summary}"
            f"{verdict_str}{facts_summary}{mod_summary}{err_summary}\n"
        )

    # 3. Procedural Playbooks
    if playbooks_prompt:
        sections.append(f"{playbooks_prompt}\n")

    # 4. Blackboard Context Digest (includes Graveyard of Failed Hypotheses)
    sections.append(f"{context_digest}\n")

    # 5. Blast-Radius Impact Reports
    if impact_reports:
        impact_lines = ["## 💥 Blast-Radius Impact Analysis (For Active Plan Files)"]
        for report in impact_reports:
            direct = ", ".join(report.direct_dependents) if report.direct_dependents else "None"
            transitive = ", ".join(report.transitive_dependents) if report.transitive_dependents else "None"
            deps = ", ".join(report.dependencies) if report.dependencies else "None"
            impact_lines.append(
                f"- **Target File:** `{report.target_file}`\n"
                f"  * Direct Dependents: {direct}\n"
                f"  * Transitive Dependents (2-hop): {transitive}\n"
                f"  * Dependencies: {deps}"
            )
        sections.append("\n".join(impact_lines) + "\n")

    # 6. Structural Repository Map
    sections.append(f"## 🗺️ Codebase Structural Map\n{repo_map}\n")

    # 7. Action Directives
    sections.append(
        "## 🎯 INSTRUCTIONS FOR THIS DECISION TICK\n"
        "1. Assess the current situation based on the latest report and verified facts.\n"
        "2. In `mental_simulation`, explicitly state your hypothesis, evaluate blast-radius side effects, "
        "and define your verification strategy.\n"
        "3. Choose `next_action`:\n"
        "   - `DISPATCH_SCOUT`: To read file slices, locate symbols, or find facts.\n"
        "   - `DISPATCH_SURGEON`: To apply precise patches to specified target_files.\n"
        "   - `DISPATCH_INQUISITOR`: To review diffs and run verification test suites.\n"
        "   - `COMPLETE_TASK`: Only if goal is fully achieved AND Inquisitor has APPROVED any code modifications.\n"
        "   - `ABORT_TASK`: If goal is unattainable after exhausting viable hypotheses.\n"
        "4. If dispatching a worker, provide a complete `task_contract` with clear objective and acceptance criteria."
    )

    return "\n".join(sections)
