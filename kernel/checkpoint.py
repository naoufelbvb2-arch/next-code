"""GitShadowCheckpointer for capturing and rolling back workspace states via Git plumbing."""

import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from kernel.exceptions import KernelError


class GitShadowCheckpointer:
    """Creates and restores shadow snapshots using low-level Git plumbing.

    Guarantees that user HEAD, branches, index, and stash remain completely untouched.
    """

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.git_dir = self.workspace_root / ".git"
        if not self.git_dir.exists():
            raise KernelError(
                f"Directory '{self.workspace_root}' is not a valid git repository (no .git directory found)."
            )

        # Defense-in-depth: Ensure .agent_os is excluded at the git repository level
        info_dir = self.git_dir / "info"
        try:
            info_dir.mkdir(parents=True, exist_ok=True)
            exclude_file = info_dir / "exclude"
            existing = exclude_file.read_text(encoding="utf-8", errors="ignore") if exclude_file.exists() else ""
            if ".agent_os" not in existing:
                with open(exclude_file, "a", encoding="utf-8") as f:
                    f.write("\n.agent_os\n.agent_os/**\n")
        except OSError:
            pass

    def _get_base_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("GIT_AUTHOR_NAME", "AgentOS ShadowCheckpointer")
        env.setdefault("GIT_AUTHOR_EMAIL", "shadow@agent-os.local")
        env.setdefault("GIT_COMMITTER_NAME", "AgentOS ShadowCheckpointer")
        env.setdefault("GIT_COMMITTER_EMAIL", "shadow@agent-os.local")
        return env

    def _run_git(
        self,
        args: list[str],
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        run_env = self._get_base_env()
        if env:
            run_env.update(env)

        res = subprocess.run(
            ["git"] + args,
            cwd=str(self.workspace_root),
            env=run_env,
            capture_output=True,
            text=True,
        )
        if check and res.returncode != 0:
            cmd_str = " ".join(["git"] + args)
            raise KernelError(
                f"Git plumbing command failed: {cmd_str}\nStderr: {res.stderr}\nStdout: {res.stdout}"
            )
        return res

    def _create_temp_index(self) -> Path:
        """Create a temporary index file inside .git directory so Git never tracks it."""
        temp_name = f"shadow_idx_{uuid.uuid4().hex}"
        temp_path = self.git_dir / temp_name
        user_index = self.git_dir / "index"
        if user_index.exists():
            shutil.copy2(user_index, temp_path)
        else:
            temp_path.touch()
        return temp_path

    def create_checkpoint(self, label: str = "") -> str:
        """Create a complete shadow snapshot of both tracked and untracked (non-ignored) files.

        Returns the dangling commit hash.
        """
        temp_index = self._create_temp_index()
        try:
            env = {"GIT_INDEX_FILE": str(temp_index)}
            # Stage all files (tracked + untracked non-ignored) into temporary index
            self._run_git(["add", "-A"], env=env)
            # Defense-in-depth: Unstage .agent_os from temporary index
            self._run_git(["reset", "--", ".agent_os"], env=env, check=False)

            # Write tree from temporary index
            tree_res = self._run_git(["write-tree"], env=env)
            tree_hash = tree_res.stdout.strip()

            # Commit tree to create dangling snapshot commit
            commit_msg = f"shadow-checkpoint: {label}".strip()
            commit_res = self._run_git(["commit-tree", tree_hash, "-m", commit_msg], env=env)
            commit_hash = commit_res.stdout.strip()

            # Store reference to prevent garbage collection
            self._run_git(["update-ref", f"refs/agent_checkpoints/{commit_hash}", commit_hash])

            return commit_hash
        finally:
            if temp_index.exists():
                temp_index.unlink(missing_ok=True)

    def rollback(self, checkpoint_hash: str) -> None:
        """Restore working directory to the exact state captured at checkpoint_hash."""
        # 1. Verify checkpoint object exists
        type_res = self._run_git(["cat-file", "-t", checkpoint_hash], check=False)
        if type_res.returncode != 0:
            raise KernelError(f"Checkpoint hash '{checkpoint_hash}' does not exist in git repository.")

        # Resolve checkpoint tree hash
        tree_res = self._run_git(["rev-parse", f"{checkpoint_hash}^{{tree}}"])
        checkpoint_tree = tree_res.stdout.strip()

        # 2. Capture current working directory tree to identify newly added untracked files
        curr_temp_idx = self._create_temp_index()
        try:
            curr_env = {"GIT_INDEX_FILE": str(curr_temp_idx)}
            self._run_git(["add", "-A"], env=curr_env)
            self._run_git(["reset", "--", ".agent_os"], env=curr_env, check=False)
            curr_tree_res = self._run_git(["write-tree"], env=curr_env)
            current_tree = curr_tree_res.stdout.strip()
        finally:
            if curr_temp_idx.exists():
                curr_temp_idx.unlink(missing_ok=True)

        # 3. Diff checkpoint tree vs current tree to find newly added files created *after* checkpoint
        diff_names_res = self._run_git(
            ["diff-tree", "-r", "-z", "--name-status", checkpoint_tree, current_tree]
        )
        tokens = diff_names_res.stdout.split("\0")
        idx = 0
        agent_os_dir = self.workspace_root / ".agent_os"
        while idx < len(tokens):
            status = tokens[idx]
            if not status:
                idx += 1
                continue

            # Rename (R) and Copy (C) include <status>\0<src>\0<dst>\0
            if status.startswith(("R", "C")):
                dst_path = tokens[idx + 2] if idx + 2 < len(tokens) else ""
                norm_dst = Path(dst_path).as_posix()
                if norm_dst == ".agent_os" or norm_dst.startswith(".agent_os/"):
                    idx += 3
                    continue
                if status.startswith(("R", "C")) and dst_path:
                    target_file = self.workspace_root / dst_path
                    if target_file.is_file() or target_file.is_symlink():
                        target_file.unlink(missing_ok=True)
                    parent = target_file.parent
                    while parent != self.workspace_root and parent.is_relative_to(self.workspace_root):
                        if parent == agent_os_dir or agent_os_dir in parent.parents:
                            break
                        try:
                            parent.rmdir()
                            parent = parent.parent
                        except OSError:
                            break
                idx += 3
            else:
                rel_path = tokens[idx + 1] if idx + 1 < len(tokens) else ""
                norm_rel = Path(rel_path).as_posix()
                if norm_rel == ".agent_os" or norm_rel.startswith(".agent_os/"):
                    idx += 2
                    continue
                if status.startswith("A") and rel_path:
                    target_file = self.workspace_root / rel_path
                    if target_file.is_file() or target_file.is_symlink():
                        target_file.unlink(missing_ok=True)
                    # Clean up empty parent directories up to workspace_root (never delete .agent_os)
                    parent = target_file.parent
                    while parent != self.workspace_root and parent.is_relative_to(self.workspace_root):
                        if parent == agent_os_dir or agent_os_dir in parent.parents:
                            break
                        try:
                            parent.rmdir()
                            parent = parent.parent
                        except OSError:
                            break
                idx += 2

        # 4. Restore files from checkpoint tree using a temporary index
        restore_temp_idx = self._create_temp_index()
        try:
            restore_env = {"GIT_INDEX_FILE": str(restore_temp_idx)}
            self._run_git(["read-tree", checkpoint_tree], env=restore_env)
            self._run_git(["checkout-index", "-a", "-f"], env=restore_env)
        finally:
            if restore_temp_idx.exists():
                restore_temp_idx.unlink(missing_ok=True)

    def get_diff(self, checkpoint_hash: str) -> str:
        """Return a unified diff between checkpoint_hash and the current working state."""
        # Verify checkpoint exists
        tree_res = self._run_git(["rev-parse", f"{checkpoint_hash}^{{tree}}"])
        checkpoint_tree = tree_res.stdout.strip()

        # Capture current working tree
        temp_index = self._create_temp_index()
        try:
            env = {"GIT_INDEX_FILE": str(temp_index)}
            self._run_git(["add", "-A"], env=env)
            self._run_git(["reset", "--", ".agent_os"], env=env, check=False)
            curr_tree_res = self._run_git(["write-tree"], env=env)
            current_tree = curr_tree_res.stdout.strip()

            diff_res = self._run_git(["diff-tree", "-p", checkpoint_tree, current_tree])
            return diff_res.stdout
        finally:
            if temp_index.exists():
                temp_index.unlink(missing_ok=True)
