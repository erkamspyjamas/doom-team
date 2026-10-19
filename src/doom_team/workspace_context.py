"""Deterministic task-focused context with complete files and explicit omissions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from doom_team.deliverable import TaskWorkspace, _git

MAX_CONTEXT_FILE_CHARS = 50_000
MAX_CONTEXT_TOTAL_CHARS = 60_000
MAX_CONTEXT_INVENTORY_CHARS = 8_000


@dataclass(frozen=True)
class WorkspaceContext:
    text: str
    tracked_paths: frozenset[str]
    complete_paths: frozenset[str]


def _terms(text: str) -> set[str]:
    return {
        word.casefold() for word in re.findall(r"[^\W_]+", text)
        if len(word) > 2 and word.casefold() not in {"test", "tests"}
    }


def _priority(
    name: str, query: str, supporting_query: str,
    focus_terms: set[str], primary_terms: set[str], supporting_terms: set[str],
) -> tuple[int, int, int, int, int, str]:
    path = PurePosixPath(name)
    # Match a repository-relative reference or a package-relative suffix, with
    # token boundaries: cli.py must not match other_cli.py or cli.py.bak.
    suffixes = ["/".join(path.parts[index:]) for index in range(len(path.parts))]
    def references(text: str) -> bool:
        return any(
            re.search(r"(?<![\w./-])" + re.escape(suffix) + r"(?![\w./-])", text)
            for suffix in suffixes
        )

    stem_terms = _terms(path.stem)
    return (
        -int(references(query)), -len(stem_terms & focus_terms), -len(stem_terms & primary_terms),
        -int(references(supporting_query)), -len(stem_terms & supporting_terms), name,
    )


def _read_complete(root: Path, name: str) -> tuple[str | None, str]:
    target = root / name
    current = root
    for part in PurePosixPath(name).parts:
        current /= part
        if current.is_symlink():
            return None, "omitted: symlink"
    if not target.is_file():
        return None, "omitted: unreadable or non-file"
    try:
        # Bound reads even for a very large tracked asset. UTF-8 takes at most
        # four bytes per character; no prefix is presented as a complete file.
        with target.open("rb") as source:
            raw = source.read(4 * MAX_CONTEXT_FILE_CHARS + 1)
        if b"\x00" in raw:
            return None, "omitted: binary"
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "omitted: invalid UTF-8 or oversized file"
    except OSError:
        return None, "omitted: unreadable or non-file"
    if len(text) > MAX_CONTEXT_FILE_CHARS:
        return None, "omitted: per-file limit"
    return text, "complete"


def build_workspace_context(
    workspace: TaskWorkspace, *, query: str = "", focus_query: str = "", supporting_query: str = "",
) -> WorkspaceContext:
    """Prefer task references, then filename terms, under a fixed total bound.

    Repository file contents never influence ranking. Both the inventory and
    bodies are bounded, and bodies are either complete UTF-8 files or absent.
    The caller frames the entire result as untrusted project data.
    """
    names = [name for name in _git(workspace.path, "ls-files", "-z").split("\x00") if name]
    primary_terms = _terms(query)
    focus_terms = _terms(focus_query)
    supporting_terms = _terms(supporting_query)
    ordered = sorted(names, key=lambda name: _priority(
        name, query, supporting_query, focus_terms, primary_terms, supporting_terms,
    ))
    sections: list[str] = []
    statuses: list[str] = []
    complete: set[str] = set()
    used = 0
    body_budget = MAX_CONTEXT_TOTAL_CHARS - MAX_CONTEXT_INVENTORY_CHARS
    for name in ordered:
        text, status = _read_complete(workspace.path, name)
        if text is not None:
            section = f"--- {json.dumps(name, ensure_ascii=False)} --- (complete; {len(text)} chars)\n{text}\n"
            if used + len(section) <= body_budget:
                sections.append(section)
                used += len(section)
                complete.add(name)
            else:
                status = "omitted: total context budget"
        statuses.append(f"{json.dumps(name, ensure_ascii=False)} [{status}]\n")

    header = (
        f"tracked files: {len(names)}; complete: {len(complete)}; omitted: {len(names) - len(complete)}\n"
        "Only bodies marked complete contain the entire file. Omitted files have no body.\n"
    )
    inventory = header
    for index, line in enumerate(statuses):
        # Reserve space for the explicit inventory truncation notice.
        if len(inventory) + len(line) + 100 > MAX_CONTEXT_INVENTORY_CHARS:
            inventory += f"... inventory truncated: {len(statuses) - index} additional tracked files\n"
            break
        inventory += line
    return WorkspaceContext(inventory + "".join(sections), frozenset(names), frozenset(complete))
