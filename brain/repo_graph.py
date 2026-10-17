"""Deterministic AST and dependency graph world model for Micro-Engineering Agent OS."""

import ast
import hashlib
import json
import os
import tempfile
from pathlib import Path

from brain.schemas import FileNode, ImpactReport, SymbolKind, SymbolNode
from kernel.guard import WorkspaceGuard


class RepoGraph:
    """Incremental, AST-driven structural world model of the repository."""

    def __init__(
        self,
        workspace_root: Path,
        cache_file: Path | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.guard = WorkspaceGuard(self.workspace_root)
        self.cache_file = (
            cache_file.resolve()
            if cache_file is not None
            else self.workspace_root / ".agent_os" / "cache" / "repo_graph.json"
        )
        self.graph: dict[str, FileNode] = {}
        self._cache_meta: dict[str, dict[str, int | str]] = {}
        self.parse_count: int = 0
        self.cache_hits: int = 0
        self._load_cache()

    def _load_cache(self) -> None:
        """Load cached graph and metadata from disk if available."""
        if not self.cache_file.exists():
            return
        try:
            raw = self.cache_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            nodes_data = data.get("nodes", {})
            self.graph = {
                k: FileNode.model_validate(v) for k, v in nodes_data.items()
            }
            self._cache_meta = data.get("meta", {})
        except Exception:
            self.graph.clear()
            self._cache_meta.clear()

    def _save_cache(self) -> None:
        """Persist graph and metadata atomically to cache_file."""
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "nodes": {k: v.model_dump() for k, v in self.graph.items()},
            "meta": self._cache_meta,
        }
        raw_json = json.dumps(payload, indent=2)

        temp_fd, temp_path = tempfile.mkstemp(
            prefix="tmp_graph_",
            dir=self.cache_file.parent,
            text=True,
        )
        try:
            with open(temp_fd, "w", encoding="utf-8") as f:
                f.write(raw_json)
            os.replace(temp_path, self.cache_file)
        except Exception:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

    def refresh(self, force: bool = False) -> dict[str, FileNode]:
        """Scan workspace, incrementally re-parsing only changed files using mtime and sha256."""
        current_files: set[str] = set()

        for root, dirs, files in os.walk(self.workspace_root):
            curr_dir = Path(root)

            # Filter out blacklisted directories in-place (e.g. .git, .agent_os, __pycache__)
            dirs[:] = [
                d for d in dirs if self.guard._is_blacklisted(curr_dir / d) is None
            ]

            for f in sorted(files):
                fp = curr_dir / f
                if self.guard._is_blacklisted(fp) is not None:
                    continue

                try:
                    rel_path = fp.resolve().relative_to(self.workspace_root).as_posix()
                except ValueError:
                    continue

                try:
                    st = fp.stat()
                except OSError:
                    continue

                mtime_ns = st.st_mtime_ns
                size = st.st_size

                # Fast check: mtime_ns and size
                if not force and rel_path in self.graph and rel_path in self._cache_meta:
                    meta = self._cache_meta[rel_path]
                    if meta.get("mtime_ns") == mtime_ns and meta.get("size") == size:
                        self.cache_hits += 1
                        current_files.add(rel_path)
                        continue

                # Content hash check
                try:
                    content_bytes = fp.read_bytes()
                except OSError:
                    continue

                sha256 = hashlib.sha256(content_bytes).hexdigest()

                if not force and rel_path in self.graph and rel_path in self._cache_meta:
                    meta = self._cache_meta[rel_path]
                    if meta.get("sha256") == sha256:
                        meta["mtime_ns"] = mtime_ns
                        meta["size"] = size
                        self.cache_hits += 1
                        current_files.add(rel_path)
                        continue

                # Modified or new file: re-parse
                self.parse_count += 1
                node = self._parse_file(rel_path, fp, content_bytes, mtime_ns, size, sha256)
                self.graph[rel_path] = node
                self._cache_meta[rel_path] = {
                    "mtime_ns": mtime_ns,
                    "size": size,
                    "sha256": sha256,
                }
                current_files.add(rel_path)

        # Remove deleted files from graph
        removed = set(self.graph.keys()) - current_files
        for r in removed:
            self.graph.pop(r, None)
            self._cache_meta.pop(r, None)

        self._save_cache()
        return self.graph

    def _detect_language(self, rel_path: str) -> str:
        lower = rel_path.lower()
        if lower.endswith(".py"):
            return "python"
        if lower.endswith(".json"):
            return "json"
        if lower.endswith((".md", ".markdown")):
            return "markdown"
        if lower.endswith((".ts", ".tsx")):
            return "typescript"
        if lower.endswith((".js", ".jsx")):
            return "javascript"
        return "other"

    def _parse_file(
        self,
        rel_path: str,
        abs_path: Path,
        content_bytes: bytes,
        mtime_ns: int,
        size: int,
        sha256: str,
    ) -> FileNode:
        language = self._detect_language(rel_path)
        content_str = content_bytes.decode("utf-8", errors="replace")
        line_count = len(content_str.splitlines()) if content_str else 0

        if language == "python":
            return self._parse_python_file(
                rel_path=rel_path,
                content_str=content_str,
                line_count=line_count,
                mtime_ns=mtime_ns,
                sha256=sha256,
            )

        # Non-Python or fallback files
        return FileNode(
            rel_path=rel_path,
            language=language,
            line_count=line_count,
            sha256=sha256,
            mtime_ns=mtime_ns,
            imports=[],
            symbols=[],
        )

    def _resolve_import(
        self,
        curr_rel: str,
        module: str | None,
        level: int,
        name: str | None = None,
    ) -> str:
        """Resolve import reference to a workspace-relative POSIX file path or module name."""
        curr_dir = Path(curr_rel).parent

        # 1. Relative import (e.g. from .base import X or from ..schemas import Y)
        if level > 0:
            target_dir = curr_dir
            for _ in range(level - 1):
                target_dir = target_dir.parent

            parts = module.split(".") if module else []
            base = target_dir.joinpath(*parts)

            py_cand = (self.workspace_root / base).with_suffix(".py")
            if py_cand.is_file():
                return py_cand.relative_to(self.workspace_root).as_posix()

            init_cand = self.workspace_root / base / "__init__.py"
            if init_cand.is_file():
                return init_cand.relative_to(self.workspace_root).as_posix()

            if name:
                sub_cand = (self.workspace_root / base / name).with_suffix(".py")
                if sub_cand.is_file():
                    return sub_cand.relative_to(self.workspace_root).as_posix()

            return base.as_posix()

        # 2. Absolute import (e.g. from kernel.guard import WorkspaceGuard)
        if module:
            parts = module.split(".")
            mod_path = Path(*parts)

            # Check if module path itself is a file (e.g. kernel/guard.py)
            py_cand = (self.workspace_root / mod_path).with_suffix(".py")
            if py_cand.is_file():
                return py_cand.relative_to(self.workspace_root).as_posix()

            # Check if module is a package (e.g. kernel/tools/__init__.py)
            init_cand = self.workspace_root / mod_path / "__init__.py"
            if init_cand.is_file():
                return init_cand.relative_to(self.workspace_root).as_posix()

            # Check if imported name is a submodule (e.g. from kernel import guard -> kernel/guard.py)
            if name:
                sub_path = Path(*(parts + [name]))
                sub_py = (self.workspace_root / sub_path).with_suffix(".py")
                if sub_py.is_file():
                    return sub_py.relative_to(self.workspace_root).as_posix()
                sub_init = self.workspace_root / sub_path / "__init__.py"
                if sub_init.is_file():
                    return sub_init.relative_to(self.workspace_root).as_posix()

            return module

        return name or ""

    def _parse_python_file(
        self,
        rel_path: str,
        content_str: str,
        line_count: int,
        mtime_ns: int,
        sha256: str,
    ) -> FileNode:
        """Parse Python source into FileNode using AST, handling broken syntax gracefully."""
        try:
            tree = ast.parse(content_str, filename=rel_path)
        except SyntaxError:
            # Graceful fallback on broken syntax
            return FileNode(
                rel_path=rel_path,
                language="python",
                line_count=line_count,
                sha256=sha256,
                mtime_ns=mtime_ns,
                imports=[],
                symbols=[],
            )

        imports: list[str] = []
        symbols: list[SymbolNode] = []

        # 1. Extract imports and resolve dependencies
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    resolved = self._resolve_import(rel_path, alias.name, level=0)
                    if resolved and resolved not in imports:
                        imports.append(resolved)
            elif isinstance(node, ast.ImportFrom):
                if node.names:
                    for alias in node.names:
                        resolved = self._resolve_import(
                            rel_path, node.module, level=node.level, name=alias.name
                        )
                        if resolved and resolved not in imports:
                            imports.append(resolved)
                else:
                    resolved = self._resolve_import(rel_path, node.module, level=node.level)
                    if resolved and resolved not in imports:
                        imports.append(resolved)

        # 2. Extract top-level symbols and methods
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = (
                    SymbolKind.ASYNC_FUNCTION
                    if isinstance(node, ast.AsyncFunctionDef)
                    else SymbolKind.FUNCTION
                )
                prefix = "async def " if kind == SymbolKind.ASYNC_FUNCTION else "def "
                args_str = ast.unparse(node.args)
                ret_str = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
                signature = f"{prefix}{node.name}({args_str}){ret_str}"

                doc = ast.get_docstring(node)
                doc_summary = doc.strip().splitlines()[0] if doc and doc.strip() else None

                symbols.append(
                    SymbolNode(
                        name=node.name,
                        qualified_name=node.name,
                        kind=kind,
                        start_line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        signature=signature,
                        docstring_summary=doc_summary,
                    )
                )

            elif isinstance(node, ast.ClassDef):
                bases_str = (
                    f"({', '.join(ast.unparse(b) for b in node.bases)})"
                    if node.bases
                    else ""
                )
                signature = f"class {node.name}{bases_str}:"
                doc = ast.get_docstring(node)
                doc_summary = doc.strip().splitlines()[0] if doc and doc.strip() else None

                symbols.append(
                    SymbolNode(
                        name=node.name,
                        qualified_name=node.name,
                        kind=SymbolKind.CLASS,
                        start_line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        signature=signature,
                        docstring_summary=doc_summary,
                    )
                )

                # Extract class methods
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        m_prefix = "async def " if isinstance(item, ast.AsyncFunctionDef) else "def "
                        m_args = ast.unparse(item.args)
                        m_ret = f" -> {ast.unparse(item.returns)}" if item.returns is not None else ""
                        method_sig = f"{m_prefix}{item.name}({m_args}){m_ret}"

                        m_doc = ast.get_docstring(item)
                        m_doc_summary = m_doc.strip().splitlines()[0] if m_doc and m_doc.strip() else None

                        symbols.append(
                            SymbolNode(
                                name=item.name,
                                qualified_name=f"{node.name}.{item.name}",
                                kind=SymbolKind.METHOD,
                                start_line=item.lineno,
                                end_line=item.end_lineno or item.lineno,
                                signature=method_sig,
                                docstring_summary=m_doc_summary,
                            )
                        )

            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                target_names: list[str] = []
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id.isupper():
                            target_names.append(target.id)
                elif isinstance(node, ast.AnnAssign):
                    if isinstance(node.target, ast.Name) and node.target.id.isupper():
                        target_names.append(node.target.id)

                for vname in target_names:
                    symbols.append(
                        SymbolNode(
                            name=vname,
                            qualified_name=vname,
                            kind=SymbolKind.GLOBAL_VAR,
                            start_line=node.lineno,
                            end_line=node.end_lineno or node.lineno,
                            signature=vname,
                            docstring_summary=None,
                        )
                    )

        return FileNode(
            rel_path=rel_path,
            language="python",
            line_count=line_count,
            sha256=sha256,
            mtime_ns=mtime_ns,
            imports=sorted(imports),
            symbols=symbols,
        )

    def get_impact_radius(self, rel_path: str) -> ImpactReport:
        """Compute blast radius: workspace dependencies, direct dependents, and transitive dependents."""
        target = Path(rel_path).as_posix()

        # 1. Direct dependencies: workspace files imported by target
        target_node = self.graph.get(target)
        dependencies: list[str] = []
        if target_node:
            for imp in target_node.imports:
                if imp in self.graph and imp != target:
                    dependencies.append(imp)

        # 2. Direct dependents: workspace files that directly import target
        direct_dependents: list[str] = []
        for path, node in self.graph.items():
            if path != target and target in node.imports:
                direct_dependents.append(path)

        # 3. Transitive dependents: workspace files importing direct dependents (up to 2 hops away)
        transitive_set: set[str] = set()
        for direct in direct_dependents:
            for path, node in self.graph.items():
                if path != target and path not in direct_dependents:
                    if direct in node.imports:
                        transitive_set.add(path)

        return ImpactReport(
            target_file=target,
            direct_dependents=sorted(direct_dependents),
            transitive_dependents=sorted(transitive_set),
            dependencies=sorted(dependencies),
        )

    def get_file_skeleton(self, rel_path: str) -> str:
        """Render a concise, human-and-LLM-readable skeleton of a file showing imports and symbols."""
        norm_path = Path(rel_path).as_posix()
        node = self.graph.get(norm_path)
        if not node:
            return f"# File: {norm_path} (Not indexed in repo graph)"

        lines = [f"# File: {norm_path} ({node.language.capitalize()}, {node.line_count} lines)"]

        if node.imports:
            lines.append("Imports:")
            for imp in sorted(node.imports):
                lines.append(f"  - {imp}")

        if node.symbols:
            lines.append("Symbols:")
            for sym in node.symbols:
                indent = "    " if sym.kind == SymbolKind.METHOD else "  "
                doc_part = f" -- {sym.docstring_summary}" if sym.docstring_summary else ""
                lines.append(
                    f"{indent}[{sym.start_line:4d}-{sym.end_line:4d}] {sym.signature}{doc_part}"
                )

        return "\n".join(lines)

    def render_repo_map(
        self,
        max_chars: int = 8000,
        focus_files: list[str] | None = None,
    ) -> str:
        """Render a compact repository skeleton ranked by graph centrality within a character budget."""
        if not self.graph:
            return "(Empty repository graph)"

        focus_set = {Path(f).as_posix() for f in (focus_files or [])}

        # Calculate centrality score for each file
        file_scores: list[tuple[float, str]] = []
        for path, node in self.graph.items():
            inbound = sum(1 for o in self.graph.values() if path in o.imports)
            outbound = sum(1 for imp in node.imports if imp in self.graph)
            score = float(inbound * 2 + outbound)
            if path in focus_set:
                score += 1000.0  # Boost focus files to the top
            file_scores.append((score, path))

        file_scores.sort(key=lambda item: (-item[0], item[1]))

        rendered_sections: list[str] = [
            f"# Repository Structure Map ({len(self.graph)} indexed files)\n"
        ]
        curr_chars = len(rendered_sections[0])

        for _, path in file_scores:
            skeleton = self.get_file_skeleton(path) + "\n\n"
            if curr_chars + len(skeleton) > max_chars:
                rendered_sections.append("... [REMAINDER OF REPO MAP TRUNCATED FOR CONTEXT BUDGET] ...")
                break
            rendered_sections.append(skeleton)
            curr_chars += len(skeleton)

        return "".join(rendered_sections).strip()
