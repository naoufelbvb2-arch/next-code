"""User-facing CLI runner and interactive live demo for the Micro-Engineering Agent OS."""

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

from agent_os import AgentOS


def setup_demo_repository(demo_dir: Path) -> None:
    """Set up a realistic demo repository with a reproducible bug and unit test."""
    if demo_dir.exists():
        shutil.rmtree(demo_dir)
    demo_dir.mkdir(parents=True)

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=str(demo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "AgentOS-User"], cwd=str(demo_dir), check=True)
    subprocess.run(["git", "config", "user.email", "user@agent-os.local"], cwd=str(demo_dir), check=True)

    # 1. Buggy module: calculate_discount has a bug (adds discount instead of subtracting)
    math_file = demo_dir / "pricing.py"
    math_file.write_text(
        '"""Pricing and discount calculation module."""\n\n'
        "def calculate_total(price: float, discount: float) -> float:\n"
        '    """Calculate final price after applying discount."""\n'
        "    if discount < 0 or discount > 1:\n"
        '        raise ValueError("Discount must be between 0 and 1.")\n'
        "    # Bug: Adding discount instead of subtracting!\n"
        "    return price + (price * discount)\n",
        encoding="utf-8",
    )

    # 2. Test suite: test_pricing.py
    test_file = demo_dir / "test_pricing.py"
    test_file.write_text(
        "import pytest\n"
        "from pricing import calculate_total\n\n"
        "def test_calculate_total_discount():\n"
        "    assert calculate_total(100.0, 0.2) == 80.0\n"
        "    assert calculate_total(50.0, 0.1) == 45.0\n\n"
        "def test_calculate_total_invalid_discount():\n"
        "    with pytest.raises(ValueError):\n"
        "        calculate_total(100.0, 1.5)\n",
        encoding="utf-8",
    )

    subprocess.run(["git", "add", "."], cwd=str(demo_dir), check=True)
    subprocess.run(["git", "commit", "-m", "initial buggy commit"], cwd=str(demo_dir), check=True)


async def run_live_task(workspace: Path, goal: str, max_ticks: int = 15) -> None:
    """Run AgentOS live with real-time terminal progress."""
    print("=" * 70)
    print(" MICRO-ENGINEERING AGENT OS -- LIVE RUNNER")
    print("=" * 70)
    print(f" Workspace Root : {workspace.resolve()}")
    print(f" Objective      : {goal}")
    print(f" Max Ticks      : {max_ticks}")

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("\n[!] WARNING: DEEPSEEK_API_KEY environment variable is not set.")
        print("    To run against the live DeepSeek reasoning API, export:")
        print("      $env:DEEPSEEK_API_KEY = 'your-api-key'  # PowerShell")
        print("      export DEEPSEEK_API_KEY='your-api-key'  # Bash\n")
        sys.exit(1)

    print(f" LLM Provider   : DeepSeek API (Key configured: {api_key[:6]}...{api_key[-4:]})")
    print("-" * 70)

    agent_os = AgentOS(
        workspace_root=workspace,
        max_ticks=max_ticks,
        verbose=True,
    )

    outcome = await agent_os.execute_task(task_goal=goal)

    print("\n" + "=" * 70)
    print(" TASK EXECUTION SUMMARY")
    print("=" * 70)
    print(f" Status        : {'SUCCESS' if outcome.success else 'FAILED'}")
    print(f" Final Phase   : {outcome.final_phase.value}")
    print(f" Total Ticks   : {outcome.total_ticks}")
    print(f" Rollbacks     : {outcome.rollbacks_triggered}")
    print(f" Final Summary : {outcome.summary}")
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Micro-Engineering Agent OS - Interactive Runner & Live Demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 1. Run automated live demo on a buggy project:
  python run.py --demo

  # 2. Run on your own repository with a custom goal:
  python run.py "Find and fix the bug in pricing.py so pytest passes" --workspace ./my_repo
        """,
    )

    parser.add_argument(
        "goal",
        nargs="?",
        default=None,
        help="The engineering goal or bugfix instruction for the agent.",
    )
    parser.add_argument(
        "--workspace",
        "-w",
        default=".",
        help="Target workspace root path (default: current directory).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Create a sandbox workspace with a failing test and run a live autonomous bugfix demo.",
    )
    parser.add_argument(
        "--max-ticks",
        type=int,
        default=12,
        help="Maximum Lead Architect orchestration ticks (default: 12).",
    )

    args = parser.parse_args()

    if args.demo:
        demo_dir = Path("./demo_sandbox").resolve()
        print(f"\n[+] Setting up demo sandbox repository in: {demo_dir}")
        setup_demo_repository(demo_dir)
        print("[+] Buggy code and failing pytest created.")
        demo_goal = "Fix the bug in pricing.py so that python -m pytest test_pricing.py passes."
        asyncio.run(run_live_task(workspace=demo_dir, goal=demo_goal, max_ticks=args.max_ticks))
    elif args.goal:
        workspace_path = Path(args.workspace).resolve()
        asyncio.run(run_live_task(workspace=workspace_path, goal=args.goal, max_ticks=args.max_ticks))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
