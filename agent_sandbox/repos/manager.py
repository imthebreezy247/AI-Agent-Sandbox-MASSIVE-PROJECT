"""Repository manager for cloning and managing Git repositories."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agent_sandbox.repos.models import RepoInfo, GitStatus, PushRequest

logger = logging.getLogger(__name__)


class RepoManagerError(Exception):
    """Raised when repository operations fail."""


class RepoManager:
    """Manages cloned Git repositories."""

    def __init__(self, base_dir: Path, github_token: str | None = None):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.github_token = github_token
        self._repos: dict[str, RepoInfo] = {}
        self._push_requests: dict[str, PushRequest] = {}
        self._metadata_file = self.base_dir / ".repos.json"
        self._load_metadata()

    def _load_metadata(self):
        """Load repo metadata from disk."""
        if self._metadata_file.exists():
            try:
                data = json.loads(self._metadata_file.read_text())
                for name, info in data.get("repos", {}).items():
                    self._repos[name] = RepoInfo(
                        name=info["name"],
                        url=info["url"],
                        path=Path(info["path"]),
                        branch=info.get("branch", "main"),
                        cloned_at=info.get("cloned_at", 0),
                    )
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to load repo metadata: {e}")

    def _save_metadata(self):
        """Save repo metadata to disk."""
        data = {
            "repos": {
                name: {
                    "name": info.name,
                    "url": info.url,
                    "path": str(info.path),
                    "branch": info.branch,
                    "cloned_at": info.cloned_at,
                }
                for name, info in self._repos.items()
            }
        }
        self._metadata_file.write_text(json.dumps(data, indent=2))

    def _extract_repo_name(self, url: str) -> str:
        """Extract repository name from URL."""
        # Handle various URL formats
        # https://github.com/user/repo.git
        # https://github.com/user/repo
        # git@github.com:user/repo.git
        parsed = urlparse(url)
        path = parsed.path if parsed.path else url.split(":")[-1]
        name = path.rstrip("/").rstrip(".git").split("/")[-1]
        return name or "repo"

    async def _run_git(self, *args: str, cwd: Path | None = None) -> tuple[int, str, str]:
        """Run a git command and return (returncode, stdout, stderr)."""
        env = os.environ.copy()

        # Configure git credentials if token available
        if self.github_token:
            env["GIT_ASKPASS"] = "echo"
            env["GIT_USERNAME"] = "x-access-token"
            env["GIT_PASSWORD"] = self.github_token

        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode or 0, stdout.decode(), stderr.decode()

    async def clone(self, url: str, name: str | None = None) -> RepoInfo:
        """Clone a repository."""
        name = name or self._extract_repo_name(url)

        # Sanitize name
        name = re.sub(r"[^a-zA-Z0-9_-]", "-", name)

        if name in self._repos:
            raise RepoManagerError(f"Repository '{name}' already exists")

        repo_path = self.base_dir / name

        if repo_path.exists():
            raise RepoManagerError(f"Directory '{repo_path}' already exists")

        logger.info(f"Cloning {url} to {repo_path}")

        # Clone the repo
        clone_url = url
        if self.github_token and "github.com" in url:
            # Inject token for private repos
            parsed = urlparse(url)
            if parsed.scheme in ("http", "https"):
                clone_url = f"https://x-access-token:{self.github_token}@{parsed.netloc}{parsed.path}"

        code, stdout, stderr = await self._run_git("clone", "--depth", "50", clone_url, str(repo_path))

        if code != 0:
            # Clean up on failure
            if repo_path.exists():
                shutil.rmtree(repo_path)
            raise RepoManagerError(f"Git clone failed: {stderr}")

        # Get branch name
        code, branch, _ = await self._run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_path)
        branch = branch.strip() or "main"

        # Get last commit
        code, commit, _ = await self._run_git("log", "-1", "--format=%h %s", cwd=repo_path)
        commit = commit.strip()

        # Count files
        files_count = sum(1 for _ in repo_path.rglob("*") if _.is_file() and ".git" not in str(_))

        info = RepoInfo(
            name=name,
            url=url,
            path=repo_path,
            branch=branch,
            last_commit=commit,
            files_count=files_count,
        )

        self._repos[name] = info
        self._save_metadata()

        logger.info(f"Cloned repository '{name}' ({files_count} files)")
        return info

    async def list_repos(self) -> list[RepoInfo]:
        """List all cloned repositories with updated status."""
        repos = []
        for name, info in list(self._repos.items()):
            # Verify path still exists
            if not info.path.exists():
                del self._repos[name]
                continue

            # Update status
            status = await self.get_status(name)
            info.has_changes = status.has_changes
            info.branch = status.branch
            repos.append(info)

        self._save_metadata()
        return repos

    async def get_repo(self, name: str) -> RepoInfo:
        """Get repository info."""
        if name not in self._repos:
            raise RepoManagerError(f"Repository '{name}' not found")

        info = self._repos[name]
        if not info.path.exists():
            del self._repos[name]
            self._save_metadata()
            raise RepoManagerError(f"Repository '{name}' directory no longer exists")

        # Update status
        status = await self.get_status(name)
        info.has_changes = status.has_changes
        info.branch = status.branch

        return info

    async def delete_repo(self, name: str):
        """Delete a cloned repository."""
        if name not in self._repos:
            raise RepoManagerError(f"Repository '{name}' not found")

        info = self._repos[name]
        if info.path.exists():
            shutil.rmtree(info.path)

        del self._repos[name]
        self._save_metadata()
        logger.info(f"Deleted repository '{name}'")

    async def get_status(self, name: str) -> GitStatus:
        """Get git status for a repository."""
        if name not in self._repos:
            raise RepoManagerError(f"Repository '{name}' not found")

        repo_path = self._repos[name].path

        # Get branch
        code, branch, _ = await self._run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_path)
        branch = branch.strip() or "main"

        # Get status
        code, status_output, _ = await self._run_git("status", "--porcelain", cwd=repo_path)

        staged = []
        modified = []
        untracked = []

        for line in status_output.strip().split("\n"):
            if not line:
                continue
            status_code = line[:2]
            file_path = line[3:]

            if status_code[0] in "MADRC":
                staged.append(file_path)
            if status_code[1] == "M":
                modified.append(file_path)
            elif status_code == "??":
                untracked.append(file_path)

        has_changes = bool(staged or modified or untracked)

        return GitStatus(
            branch=branch,
            staged=staged,
            modified=modified,
            untracked=untracked,
            has_changes=has_changes,
        )

    async def get_diff(self, name: str) -> str:
        """Get git diff for a repository."""
        if name not in self._repos:
            raise RepoManagerError(f"Repository '{name}' not found")

        repo_path = self._repos[name].path

        # Get diff of staged + unstaged changes
        code, diff, _ = await self._run_git("diff", "HEAD", cwd=repo_path)
        return diff

    def list_files(self, name: str, max_files: int = 500) -> list[str]:
        """List files in a repository (excluding .git)."""
        if name not in self._repos:
            raise RepoManagerError(f"Repository '{name}' not found")

        repo_path = self._repos[name].path
        files = []

        for path in repo_path.rglob("*"):
            if path.is_file() and ".git" not in str(path):
                files.append(str(path.relative_to(repo_path)))
                if len(files) >= max_files:
                    break

        return sorted(files)

    async def request_push(self, name: str, commit_message: str = "") -> PushRequest:
        """Create a push request (requires approval)."""
        if name not in self._repos:
            raise RepoManagerError(f"Repository '{name}' not found")

        status = await self.get_status(name)
        if not status.has_changes:
            raise RepoManagerError("No changes to push")

        diff = await self.get_diff(name)
        changes_summary = f"Staged: {len(status.staged)}, Modified: {len(status.modified)}, Untracked: {len(status.untracked)}"

        request = PushRequest(
            repo_name=name,
            status="pending",
            changes_summary=changes_summary,
            commit_message=commit_message or "Changes from AI Agent",
        )

        self._push_requests[name] = request
        logger.info(f"Created push request for '{name}'")
        return request

    async def approve_push(self, name: str) -> PushRequest:
        """Approve and execute a push."""
        if name not in self._push_requests:
            raise RepoManagerError(f"No pending push request for '{name}'")

        request = self._push_requests[name]
        if request.status != "pending":
            raise RepoManagerError(f"Push request is not pending (status: {request.status})")

        repo_path = self._repos[name].path

        try:
            # Stage all changes
            code, _, stderr = await self._run_git("add", "-A", cwd=repo_path)
            if code != 0:
                raise RepoManagerError(f"git add failed: {stderr}")

            # Commit
            code, _, stderr = await self._run_git(
                "commit", "-m", request.commit_message, cwd=repo_path
            )
            if code != 0 and "nothing to commit" not in stderr:
                raise RepoManagerError(f"git commit failed: {stderr}")

            # Push
            code, _, stderr = await self._run_git("push", cwd=repo_path)
            if code != 0:
                raise RepoManagerError(f"git push failed: {stderr}")

            request.status = "completed"
            request.completed_at = __import__("time").time()
            logger.info(f"Successfully pushed changes for '{name}'")

        except RepoManagerError as e:
            request.status = "failed"
            request.error = str(e)
            raise

        finally:
            del self._push_requests[name]

        return request

    def reject_push(self, name: str) -> PushRequest:
        """Reject a push request."""
        if name not in self._push_requests:
            raise RepoManagerError(f"No pending push request for '{name}'")

        request = self._push_requests[name]
        request.status = "rejected"
        del self._push_requests[name]
        return request

    def get_push_requests(self) -> list[PushRequest]:
        """Get all pending push requests."""
        return list(self._push_requests.values())
