"""Data models for repository management."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import time


@dataclass
class RepoInfo:
    """Information about a cloned repository."""

    name: str
    url: str
    path: Path
    branch: str = "main"
    last_commit: str = ""
    has_changes: bool = False
    files_count: int = 0
    cloned_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "path": str(self.path),
            "branch": self.branch,
            "last_commit": self.last_commit,
            "has_changes": self.has_changes,
            "files_count": self.files_count,
            "cloned_at": self.cloned_at,
        }


@dataclass
class GitStatus:
    """Git status for a repository."""

    branch: str
    ahead: int = 0
    behind: int = 0
    staged: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    has_changes: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "ahead": self.ahead,
            "behind": self.behind,
            "staged": self.staged,
            "modified": self.modified,
            "untracked": self.untracked,
            "has_changes": self.has_changes,
        }


@dataclass
class PushRequest:
    """A pending push request requiring approval."""

    repo_name: str
    status: str = "pending"  # pending, approved, rejected, completed
    changes_summary: str = ""
    commit_message: str = ""
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_name": self.repo_name,
            "status": self.status,
            "changes_summary": self.changes_summary,
            "commit_message": self.commit_message,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }
