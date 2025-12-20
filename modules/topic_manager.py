"""Topic Manager for Telegram Topics - Git worktree management"""

import os
import re
import shutil
import subprocess
import logging
from pathlib import Path
from typing import Optional, Dict, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class RepositoryExistsError(ValueError):
    """Raised when attempting to clone a repository that already exists"""

    def __init__(self, path: Path):
        super().__init__(str(path))
        self.path = str(path)


class TopicManager:
    """Manages Git worktrees for Telegram Topics"""

    def __init__(self, workspaces_root: str = "./workspaces"):
        """Initialize TopicManager

        Args:
            workspaces_root: Root directory for all workspaces
        """
        self.workspaces_root = Path(workspaces_root)
        self.workspaces_root.mkdir(parents=True, exist_ok=True)
        logger.info(f"TopicManager initialized with workspaces root: {self.workspaces_root}")

    def _get_chat_dir(self, chat_id: str) -> Path:
        """Get directory path for a chat"""
        return self.workspaces_root / str(chat_id)

    def _get_topics_metadata_file(self, chat_id: str) -> Path:
        """Get path to topics metadata file"""
        chat_dir = self._get_chat_dir(chat_id).resolve()
        return chat_dir / ".topics" / "topics.json"

    def _ensure_chat_structure(self, chat_id: str):
        """Ensure directory structure exists for a chat"""
        chat_dir = self._get_chat_dir(chat_id)
        chat_dir.mkdir(parents=True, exist_ok=True)

        topics_dir = chat_dir / ".topics"
        topics_dir.mkdir(exist_ok=True)

        repo_dir = chat_dir / "repo"
        repo_dir.mkdir(exist_ok=True)

        worktrees_dir = chat_dir / "worktrees"
        worktrees_dir.mkdir(exist_ok=True)

        # Initialize topics metadata if it doesn't exist
        metadata_file = self._get_topics_metadata_file(chat_id)
        if not metadata_file.exists():
            import json
            with open(metadata_file, 'w') as f:
                json.dump({}, f)

    def _sanitize_project_name(self, name: str) -> str:
        """Sanitize project name for use as directory name"""
        # Remove special characters and replace with hyphens
        sanitized = re.sub(r'[^a-zA-Z0-9_-]', '-', name)
        # Remove multiple consecutive hyphens
        sanitized = re.sub(r'-+', '-', sanitized)
        # Remove leading/trailing hyphens
        return sanitized.strip('-').lower()

    def _shorten_topic_id(self, topic_id: str) -> str:
        """Create short version of topic_id for directory naming"""
        # Use first 8 characters of topic_id
        return str(topic_id)[:8] if len(str(topic_id)) > 8 else str(topic_id)

    def _parse_owner_repo(self, git_url: str) -> Tuple[str, str]:
        """Extract owner and repo name from git URL for namespacing"""
        parsed = urlparse(git_url)
        parts = [p for p in parsed.path.split("/") if p]

        if len(parts) >= 2:
            owner, repo = parts[-2], parts[-1]
        elif len(parts) == 1:
            owner, repo = "default", parts[0]
        else:
            owner, repo = "default", "repo"

        if repo.endswith(".git"):
            repo = repo[:-4]

        owner = self._sanitize_project_name(owner) or "default"
        repo = self._sanitize_project_name(repo) or "repo"
        return owner, repo

    def _determine_branch_candidates(self, repo_path: Path) -> list:
        """Determine branch candidates (local + remote) for creating worktrees."""
        candidates = []
        seen = set()

        def add(ref: Optional[str]):
            if ref and ref != "HEAD" and ref not in seen:
                candidates.append(ref)
                seen.add(ref)

        # Current branch (if any)
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=repo_path,
                check=True,
                capture_output=True,
                text=True,
            )
            add(result.stdout.strip())
        except subprocess.CalledProcessError:
            pass

        # Local branches
        try:
            result = subprocess.run(
                ["git", "branch", "--format=%(refname:short)"],
                cwd=repo_path,
                check=True,
                capture_output=True,
                text=True,
            )
            for line in result.stdout.splitlines():
                add(line.strip())
        except subprocess.CalledProcessError:
            pass

        # Remote HEAD if available
        try:
            result = subprocess.run(
                ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
                cwd=repo_path,
                check=True,
                capture_output=True,
                text=True,
            )
            ref = result.stdout.strip()
            if ref.startswith("refs/remotes/"):
                add(ref.replace("refs/remotes/", ""))
        except subprocess.CalledProcessError:
            pass

        # Common fallbacks (remote names work too)
        for ref in ["origin/main", "origin/master", "main", "master", "develop"]:
            add(ref)

        return candidates or ["main", "master"]

    def _get_head_commit(self, repo_path: Path) -> Optional[str]:
        """Return HEAD commit hash for detached fallback."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                check=True,
                capture_output=True,
                text=True,
            )
            commit = result.stdout.strip()
            return commit or None
        except subprocess.CalledProcessError:
            return None

    def _branch_exists(self, repo_path: Path, branch: str) -> bool:
        """Check if branch exists locally."""
        try:
            subprocess.run(
                ["git", "show-ref", "--verify", f"refs/heads/{branch}"],
                cwd=repo_path,
                check=True,
                capture_output=True,
                text=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def _add_worktree_with_fallback(
        self,
        main_repo_path: Path,
        worktree_path: Path,
        base_refs: list,
        branch_name: str,
        commit_fallback: Optional[str] = None,
    ) -> str:
        """Try to add a worktree using base_refs; fallback to detached HEAD."""
        last_error: Optional[Exception] = None
        branch_exists = self._branch_exists(main_repo_path, branch_name)

        for base_ref in base_refs:
            try:
                if branch_exists:
                    cmd = ["git", "worktree", "add", str(worktree_path), branch_name]
                else:
                    cmd = [
                        "git",
                        "worktree",
                        "add",
                        "-b",
                        branch_name,
                        str(worktree_path),
                        base_ref,
                    ]

                logger.info(f"Creating worktree {worktree_path} from {base_ref} as {branch_name}")
                subprocess.run(
                    cmd,
                    cwd=main_repo_path,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return branch_name
            except subprocess.CalledProcessError as e:
                last_error = e
                stderr = e.stderr.strip() if getattr(e, "stderr", None) else ""
                logger.warning(
                    f"Worktree creation failed on {base_ref}: {e}. stderr={stderr}"
                )
                if worktree_path.exists():
                    shutil.rmtree(worktree_path, ignore_errors=True)
                continue

        # Fallback: detached worktree on HEAD commit
        if commit_fallback:
            try:
                logger.info(f"Creating detached worktree {worktree_path} at {commit_fallback}")
                subprocess.run(
                    ["git", "worktree", "add", "--detach", str(worktree_path), commit_fallback],
                    cwd=main_repo_path,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return commit_fallback
            except subprocess.CalledProcessError as e:
                last_error = e
                if worktree_path.exists():
                    shutil.rmtree(worktree_path, ignore_errors=True)

        detail = ""
        if last_error and getattr(last_error, "stderr", None):
            detail = last_error.stderr.strip()
        raise ValueError(f"Failed to create worktree: {last_error}. {detail}")

    def create_empty_project(
        self,
        chat_id: str,
        topic_id: str,
        project_name: str,
    ) -> Tuple[str, str]:
        """Create a new empty Git project with worktree

        Args:
            chat_id: Telegram chat ID
            topic_id: Topic ID
            project_name: Name of the project

        Returns:
            Tuple of (main_repo_path, worktree_path)

        Raises:
            ValueError: If project creation fails
        """
        logger.info(f"[TOPIC] Creating empty project - chat={chat_id}, topic={topic_id}, project={project_name}")

        self._ensure_chat_structure(chat_id)

        chat_dir = self._get_chat_dir(chat_id)
        worktrees_dir = chat_dir / "worktrees"

        # Sanitize project name
        sanitized_name = self._sanitize_project_name(project_name)
        short_topic_id = self._shorten_topic_id(topic_id)

        # Create paths
        main_repo_path = chat_dir / sanitized_name
        worktree_path = worktrees_dir / f"{sanitized_name}-{short_topic_id}"

        logger.info(f"[TOPIC] Paths - main_repo={main_repo_path}, worktree={worktree_path}")

        # Check if main repo already exists
        if main_repo_path.exists():
            logger.info(f"[TOPIC] Main repository already exists, creating worktree from existing repo")
            # Create worktree from existing repo
            return self._create_worktree_from_existing(chat_id, topic_id, str(main_repo_path), project_name)

        try:
            # Create main repository
            logger.info(f"[TOPIC] Initializing git repository")
            os.makedirs(main_repo_path, exist_ok=True)

            # Initialize git repo
            subprocess.run(
                ["git", "init"],
                cwd=main_repo_path,
                check=True,
                capture_output=True,
                text=True
            )

            # Create initial commit
            readme_path = main_repo_path / "README.md"
            with open(readme_path, 'w') as f:
                f.write(f"# {project_name}\n\nCreated by Vibe Remote.\n")

            subprocess.run(
                ["git", "add", "README.md"],
                cwd=main_repo_path,
                check=True,
                capture_output=True,
                text=True
            )

            subprocess.run(
                ["git", "commit", "-m", "Initial commit"],
                cwd=main_repo_path,
                check=True,
                capture_output=True,
                text=True
            )

            # Create worktree
            logger.info(f"[TOPIC] Creating git worktree")
            subprocess.run(
                ["git", "worktree", "add", str(worktree_path), "main"],
                cwd=main_repo_path,
                check=True,
                capture_output=True,
                text=True
            )

            # Save metadata
            self._save_topic_metadata(chat_id, topic_id, project_name)

            logger.info(f"[TOPIC] ✅ Successfully created empty project - chat={chat_id}, topic={topic_id}, project={project_name}")
            return str(main_repo_path), str(worktree_path)

        except subprocess.CalledProcessError as e:
            logger.error(f"[TOPIC] ❌ Git command failed - chat={chat_id}, topic={topic_id}, error={e}")
            # Cleanup on failure
            if worktree_path.exists():
                shutil.rmtree(worktree_path, ignore_errors=True)
            if main_repo_path.exists() and not any(main_repo_path.iterdir()):
                main_repo_path.rmdir()
            raise ValueError(f"Failed to create project: {e}")
    def clone_project(
        self,
        chat_id: str,
        git_url: str,
        project_name: Optional[str] = None,
        topic_id: Optional[str] = None,
    ) -> Tuple[str, Optional[str]]:
        """Clone a Git repository. If topic_id provided, also create worktree; otherwise clone only."""
        self._ensure_chat_structure(chat_id)

        if not self._is_valid_git_url(git_url):
            raise ValueError(f"Invalid Git URL: {git_url}")

        if not project_name:
            project_name = self._derive_project_name_from_url(git_url)

        chat_dir = self._get_chat_dir(chat_id)
        owner, repo = self._parse_owner_repo(git_url)

        # Main repo lives under repo/{owner}/{repo} to avoid collisions
        repo_root = (chat_dir / "repo" / owner).resolve()
        repo_root.mkdir(parents=True, exist_ok=True)

        worktrees_dir = (chat_dir / "worktrees").resolve()

        sanitized_name = self._sanitize_project_name(project_name)
        if not sanitized_name:
            sanitized_name = "repo"

        main_repo_path = (repo_root / repo).resolve()
        worktree_path: Optional[Path] = None

        if main_repo_path.exists():
            if (main_repo_path / ".git").exists():
                logger.warning(f"Main repository already exists: {main_repo_path}")
                if topic_id:
                    return self._create_worktree_from_existing(chat_id, topic_id, str(main_repo_path), project_name)
                raise RepositoryExistsError(main_repo_path)
            raise ValueError(f"Path already exists and is not a git repo: {main_repo_path}")

        try:
            logger.info(f"Cloning repository: {git_url} to {main_repo_path}")
            subprocess.run(
                ["git", "clone", git_url, str(main_repo_path)],
                check=True,
                capture_output=True,
                text=True
            )

            if topic_id:
                short_topic_id = self._shorten_topic_id(topic_id)
                worktree_path = (worktrees_dir / f"{sanitized_name}-{short_topic_id}").resolve()
                candidates = self._determine_branch_candidates(main_repo_path)
                branch_name = f"topic/{sanitized_name}-{short_topic_id}"
                commit_fallback = self._get_head_commit(main_repo_path)
                used_branch = self._add_worktree_with_fallback(
                    main_repo_path, worktree_path, candidates, branch_name, commit_fallback
                )
                self._save_topic_metadata(chat_id, topic_id, project_name)
                logger.info(f"Cloned project: {project_name} (branch used: {used_branch})")
                return str(main_repo_path), str(worktree_path)

            logger.info(f"Cloned repository: {project_name}")
            return str(main_repo_path), None

        except subprocess.CalledProcessError as e:
            logger.error(f"Git command failed: {e}")
            if worktree_path and isinstance(worktree_path, Path) and worktree_path.exists():
                shutil.rmtree(worktree_path, ignore_errors=True)
            if main_repo_path.exists():
                shutil.rmtree(main_repo_path, ignore_errors=True)
            raise ValueError(f"Failed to clone project: {e}")

    def list_repositories(self, chat_id: str) -> Dict[str, Dict[str, Optional[str]]]:
        """List all cloned repositories for a chat with path and git url"""
        self._ensure_chat_structure(chat_id)
        chat_dir = self._get_chat_dir(chat_id)
        repo_root = chat_dir / "repo"

        repos: Dict[str, Dict[str, Optional[str]]] = {}
        for owner_dir in repo_root.iterdir():
            if not owner_dir.is_dir():
                continue
            for repo_dir in owner_dir.iterdir():
                if not repo_dir.is_dir():
                    continue
                if not (repo_dir / ".git").exists():
                    continue

                repo_name = f"{owner_dir.name}/{repo_dir.name}"
                git_url: Optional[str] = None

                try:
                    result = subprocess.run(
                        ["git", "config", "--get", "remote.origin.url"],
                        cwd=repo_dir,
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    git_url = result.stdout.strip() or None
                except subprocess.CalledProcessError as e:
                    logger.warning(f"Failed to read remote for {repo_dir}: {e}")

                repos[repo_name] = {"path": str(repo_dir), "git_url": git_url}

        return repos

    def _create_worktree_from_existing(
        self,
        chat_id: str,
        topic_id: str,
        main_repo_path: str,
        project_name: str,
    ) -> Tuple[str, str]:
        """Create a new worktree from existing repository

        Args:
            chat_id: Telegram chat ID
            topic_id: Topic ID
            main_repo_path: Path to main repository
            project_name: Project name

        Returns:
            Tuple of (main_repo_path, worktree_path)
        """
        self._ensure_chat_structure(chat_id)

        worktrees_dir = (self._get_chat_dir(chat_id) / "worktrees").resolve()
        sanitized_name = self._sanitize_project_name(project_name)
        short_topic_id = self._shorten_topic_id(topic_id)
        worktree_path = (worktrees_dir / f"{sanitized_name}-{short_topic_id}").resolve()
        branch_name = f"topic/{sanitized_name}-{short_topic_id}"

        # Check if worktree already exists
        if worktree_path.exists():
            logger.warning(f"Worktree already exists: {worktree_path}")
            return main_repo_path, str(worktree_path)

        try:
            repo_path = Path(main_repo_path)
            candidates = self._determine_branch_candidates(repo_path)
            commit_fallback = self._get_head_commit(repo_path)
            used_branch = self._add_worktree_with_fallback(
                repo_path, worktree_path, candidates, branch_name, commit_fallback
            )

            # Save metadata if not already saved
            self._save_topic_metadata(chat_id, topic_id, project_name)

            logger.info(f"Created worktree for existing project: {project_name} (branch used: {used_branch})")
            return main_repo_path, str(worktree_path)

        except (subprocess.CalledProcessError, ValueError) as e:
            logger.error(f"Git command failed: {e}")
            if worktree_path.exists():
                shutil.rmtree(worktree_path, ignore_errors=True)
            raise ValueError(f"Failed to create worktree: {e}")

    def _save_topic_metadata(self, chat_id: str, topic_id: str, project_name: str):
        """Save topic metadata to JSON file"""
        import json

        metadata_file = self._get_topics_metadata_file(chat_id)

        # Load existing metadata
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)

        # Update metadata
        metadata[str(topic_id)] = {
            "name": project_name,
            "sanitized_name": self._sanitize_project_name(project_name),
        }

        # Save metadata
        with open(metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)

    def list_topics(self, chat_id: str) -> Dict[str, Dict[str, str]]:
        """List all topics for a chat

        Args:
            chat_id: Telegram chat ID

        Returns:
            Dictionary mapping topic_id to topic info
        """
        metadata_file = self._get_topics_metadata_file(chat_id)

        if not metadata_file.exists():
            return {}

        import json
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)

        return metadata

    def get_worktree_branch(self, worktree_path: str) -> Optional[str]:
        """Get current branch (or HEAD) for a worktree"""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=worktree_path,
                check=True,
                capture_output=True,
                text=True,
            )
            branch = result.stdout.strip()
            return branch or None
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to get branch for worktree {worktree_path}: {e}")
            return None

    def get_worktree_for_topic(self, chat_id: str, topic_id: str) -> Optional[str]:
        """Get worktree path for a topic

        Args:
            chat_id: Telegram chat ID
            topic_id: Topic ID

        Returns:
            Worktree path or None if not found
        """
        metadata = self.list_topics(chat_id)

        if str(topic_id) not in metadata:
            return None

        topic_info = metadata[str(topic_id)]
        sanitized_name = topic_info["sanitized_name"]
        short_topic_id = self._shorten_topic_id(topic_id)

        worktree_path = (
            self._get_chat_dir(chat_id) / "worktrees" / f"{sanitized_name}-{short_topic_id}"
        )

        return str(worktree_path) if worktree_path.exists() else None

    def delete_topic(self, chat_id: str, topic_id: str) -> bool:
        """Delete a topic and its worktree

        Args:
            chat_id: Telegram chat ID
            topic_id: Topic ID

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"[TOPIC] Deleting topic - chat={chat_id}, topic={topic_id}")

        import json

        metadata_file = self._get_topics_metadata_file(chat_id)

        if not metadata_file.exists():
            logger.warning(f"[TOPIC] ⚠️ Topics metadata not found: {metadata_file}")
            return False

        # Load metadata
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)

        if str(topic_id) not in metadata:
            logger.warning(f"[TOPIC] ⚠️ Topic {topic_id} not found in metadata")
            return False

        topic_info = metadata[str(topic_id)]
        sanitized_name = topic_info["sanitized_name"]
        short_topic_id = self._shorten_topic_id(topic_id)

        # Remove worktree
        worktree_path = (
            self._get_chat_dir(chat_id) / "worktrees" / f"{sanitized_name}-{short_topic_id}"
        )
        if worktree_path.exists():
            logger.info(f"[TOPIC] Removing git worktree: {worktree_path}")
            removed = False
            main_repo_path = self._get_chat_dir(chat_id) / sanitized_name
            if main_repo_path.exists():
                try:
                    subprocess.run(
                        ["git", "worktree", "remove", str(worktree_path)],
                        cwd=main_repo_path,
                        check=True,
                        capture_output=True,
                        text=True
                    )
                    removed = True
                    logger.info(f"[TOPIC] ✅ Successfully removed worktree via git")
                except subprocess.CalledProcessError as e:
                    logger.error(f"[TOPIC] ❌ Failed to remove worktree via git: {e}")
                    # Fallback: prune stale entries then force remove directory
                    try:
                        subprocess.run(
                            ["git", "worktree", "prune"],
                            cwd=main_repo_path,
                            check=False,
                            capture_output=True,
                            text=True,
                        )
                    except Exception as prune_err:
                        logger.debug(f"[TOPIC] worktree prune skipped: {prune_err}")
            else:
                logger.warning(f"[TOPIC] ⚠️ Main repo not found for {sanitized_name}, skipping git worktree remove")
            # Ensure directory is gone
            if worktree_path.exists():
                try:
                    shutil.rmtree(worktree_path, ignore_errors=True)
                    removed = True
                    logger.info(f"[TOPIC] ✅ Force-removed worktree directory {worktree_path}")
                except Exception as rm_err:
                    logger.error(f"[TOPIC] ❌ Failed to delete worktree directory {worktree_path}: {rm_err}")
            if not removed:
                logger.warning(f"[TOPIC] ⚠️ Worktree path still present: {worktree_path}")

        # Remove metadata entry
        del metadata[str(topic_id)]

        # Save metadata
        with open(metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)

        logger.info(f"[TOPIC] ✅ Successfully deleted topic - chat={chat_id}, topic={topic_id}, project={topic_info.get('name', 'unknown')}")
        return True

    def _is_valid_git_url(self, url: str) -> bool:
        """Validate if URL is a valid Git URL"""
        try:
            parsed = urlparse(url)
            # Basic validation: must have scheme and netloc
            if not parsed.scheme or not parsed.netloc:
                return False
            # Accept common protocols
            valid_schemes = ['http', 'https', 'git', 'ssh']
            return parsed.scheme in valid_schemes
        except Exception:
            return False

    def _derive_project_name_from_url(self, git_url: str) -> str:
        """Derive project name from Git URL"""
        owner, repo = self._parse_owner_repo(git_url)
        # Use owner/repo for uniqueness across different owners
        return f"{owner}/{repo}"
