"""Built-in RoleCard definitions and dynamic specialist builder."""

from typing import Literal

from kernel.schemas import ModelProfile
from squad.schemas import RoleCard

SCOUT_ROLE_CARD = RoleCard(
    role_id="SCOUT",
    persona_title="Forensic Codebase Scout",
    system_directive=(
        "You are a Forensic Codebase Scout. Your objective is read-only reconnaissance, symbol discovery, "
        "and root-cause diagnosis. You are strictly forbidden from modifying any files or running shell commands. "
        "Always inspect exact file contents and line spans before forming conclusions. When returning findings, "
        "provide evidence-backed VerifiedFactInput records with exact file paths and start_line/end_line ranges. "
        "Never guess or hallucinate code structure."
    ),
    tools_whitelist=("read_file", "list_dir", "grep_search", "find_files"),
    model_profile=ModelProfile.FAST_WORKER,
    max_steps=10,
    sliding_window_size=4,
    require_test_execution_if_configured=False,
)

SURGEON_ROLE_CARD = RoleCard(
    role_id="SURGEON",
    persona_title="Precision AST Code Surgeon",
    system_directive=(
        "You are a Precision AST Code Surgeon. Your objective is surgical, minimal code modification and "
        "immediate verification. You can only modify files explicitly listed in target_files. Before applying any "
        "patch, always read the target lines first to ensure your search block matches exactly. Keep diffs as small "
        "as possible. If a test_command is provided, run it and inspect the output before finishing."
    ),
    tools_whitelist=(
        "read_file",
        "list_dir",
        "grep_search",
        "find_files",
        "apply_patch",
        "run_command",
    ),
    model_profile=ModelProfile.FAST_WORKER,
    max_steps=12,
    sliding_window_size=4,
    require_test_execution_if_configured=True,
)

INQUISITOR_ROLE_CARD = RoleCard(
    role_id="INQUISITOR",
    persona_title="Adversarial QA & Security Inquisitor",
    system_directive=(
        "You are an Adversarial QA & Security Inquisitor. Your objective is rigorous, skeptical verification of all code "
        "modifications. You have zero file mutation capability. Analyze the git_diff against every acceptance criterion. "
        "Execute test and verification commands to detect edge cases, regressions, or syntax flaws. Issue InquisitorVerdict.APPROVED "
        "if and only if 100% of acceptance criteria are satisfied and tests pass. Otherwise, issue InquisitorVerdict.REJECTED "
        "with concrete failure details."
    ),
    tools_whitelist=("read_file", "list_dir", "grep_search", "find_files", "run_command"),
    model_profile=ModelProfile.REASONING,
    max_steps=8,
    sliding_window_size=4,
    require_test_execution_if_configured=True,
)


def get_role_card(
    role: Literal["SCOUT", "SURGEON", "INQUISITOR"],
    custom_persona_title: str | None = None,
    extra_directive: str | None = None,
    max_steps_override: int | None = None,
) -> RoleCard:
    """Retrieve canonical RoleCard or construct an immutable customized specialist clone."""
    if role == "SCOUT":
        base = SCOUT_ROLE_CARD
    elif role == "SURGEON":
        base = SURGEON_ROLE_CARD
    elif role == "INQUISITOR":
        base = INQUISITOR_ROLE_CARD
    else:
        raise ValueError(f"Unknown role identifier: {role}")

    if not any([custom_persona_title, extra_directive, max_steps_override is not None]):
        return base

    persona_title = custom_persona_title or base.persona_title
    system_directive = base.system_directive
    if extra_directive:
        system_directive = f"{system_directive}\n\nSpecialized Directive:\n{extra_directive}"
    max_steps = max_steps_override if max_steps_override is not None else base.max_steps

    return RoleCard(
        role_id=base.role_id,
        persona_title=persona_title,
        system_directive=system_directive,
        tools_whitelist=base.tools_whitelist,
        model_profile=base.model_profile,
        max_steps=max_steps,
        sliding_window_size=base.sliding_window_size,
        require_test_execution_if_configured=base.require_test_execution_if_configured,
    )
