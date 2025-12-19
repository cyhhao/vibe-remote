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
        chat_dir = self._get_chat_dir(chat_id)
        return chat_dir / ".topics" / "topics.json"

    def _ensure_chat_structure(self, chat_id: str):
        """Ensure directory structure exists for a chat"""
        chat_dir = self._get_chat_dir(chat_id)
        chat_dir.mkdir(parents=True, exist_ok=True)

        topics_dir = chat_dir / ".topics"
        topics_dir.mkdir(exist_ok=True)

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
        topic_id: str,
        git_url: str,
        project_name: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Clone a Git repository and create worktree

        Args:
            chat_id: Telegram chat ID
            topic_id: Topic ID
            git_url: Git repository URL
            project_name: Optional project name (derived from URL if not provided)

        Returns:
            Tuple of (main_repo_path, worktree_path)

        Raises:
            ValueError: If clone fails
        """
        self._ensure_chat_structure(chat_id)

        # Validate git URL
        if not self._is_valid_git_url(git_url):
            raise ValueError(f"Invalid Git URL: {git_url}")

        # Derive project name from URL if not provided
        if not project_name:
            project_name = self._derive_project_name_from_url(git_url)

        chat_dir = self._get_chat_dir(chat_id)
        worktrees_dir = chat_dir / "worktrees"

        # Sanitize project name
        sanitized_name = self._sanitize_project_name(project_name)
        short_topic_id = self._shorten_topic_id(topic_id)

        # Create paths
        main_repo_path = chat_dir / sanitized_name
        worktree_path = worktrees_dir / f"{sanitized_name}-{short_topic_id}"

        # Check if main repo already exists
        if main_repo_path.exists():
            logger.warning(f"Main repository already exists: {main_repo_path}")
            # Create worktree from existing repo
            return self._create_worktree_from_existing(chat_id, topic_id, str(main_repo_path), project_name)

        try:
            # Clone repository
            logger.info(f"Cloning repository: {git_url} to {main_repo_path}")
            subprocess.run(
                ["git", "clone", git_url, str(main_repo_path)],
                check=True,
                capture_output=True,
                text=True
            )

            # Get default branch
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=main_repo_path,
                check=True,
                capture_output=True,
                text=True
            )
            default_branch = result.stdout.strip() or "main"

            # Create worktree
            logger.info(f"Creating worktree: {worktree_path}")
            subprocess.run(
                ["git", "worktree", "add", str(worktree_path), default_branch],
                cwd=main_repo_path,
                check=True,
                capture_output=True,
                text=True
            )

            # Save metadata
            self._save_topic_metadata(chat_id, topic_id, project_name)

            logger.info(f"Cloned project: {project_name}")
            return str(main_repo_path), str(worktree_path)

        except subprocess.CalledProcessError as e:
            logger.error(f"Git command failed: {e}")
            # Cleanup on failure
            if worktree_path.exists():
                shutil.rmtree(worktree_path, ignore_errors=True)
            if main_repo_path.exists():
                shutil.rmtree(main_repo_path, ignore_errors=True)
            raise ValueError(f"Failed to clone project: {e}")

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

        worktrees_dir = self._get_chat_dir(chat_id) / "worktrees"
        sanitized_name = self._sanitize_project_name(project_name)
        short_topic_id = self._shorten_topic_id(topic_id)
        worktree_path = worktrees_dir / f"{sanitized_name}-{short_topic_id}"

        # Check if worktree already exists
        if worktree_path.exists():
            logger.warning(f"Worktree already exists: {worktree_path}")
            return main_repo_path, str(worktree_path)

        try:
            # Get default branch
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=main_repo_path,
                check=True,
                capture_output=True,
                text=True
            )
            default_branch = result.stdout.strip() or "main"

            # Create worktree
            logger.info(f"Creating worktree: {worktree_path}")
            subprocess.run(
                ["git", "worktree", "add", str(worktree_path), default_branch],
                cwd=main_repo_path,
                check=True,
                capture_output=True,
                text=True
            )

            # Save metadata if not already saved
            self._save_topic_metadata(chat_id, topic_id, project_name)

            logger.info(f"Created worktree for existing project: {project_name}")
            return main_repo_path, str(worktree_path)

        except subprocess.CalledProcessError as e:
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
            try:
                subprocess.run(
                    ["git", "worktree", "remove", str(worktree_path)],
                    check=True,
                    capture_output=True,
                    text=True
                )
                logger.info(f"[TOPIC] ✅ Successfully removed worktree")
            except subprocess.CalledProcessError as e:
                logger.error(f"[TOPIC] ❌ Failed to remove worktree: {e}")
                # Continue with cleanup even if worktree removal fails

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
        parsed = urlparse(git_url)
        path = parsed.path.strip('/')

        # Get last part of path without .git extension
        project_name = path.split('/')[-1]
        if project_name.endswith('.git'):
            project_name = project_name[:-4]

        return project_name
