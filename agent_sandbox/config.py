"""Configuration management for Agent Sandbox."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SandboxConfig:
    """Global configuration loaded from environment / .env file."""

    max_agents: int = 10
    sandbox_timeout: int = 300  # seconds per task
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    sandbox_mode: str = "subprocess"  # "subprocess" or "docker"
    log_level: str = "INFO"
    base_dir: Path = field(default_factory=lambda: Path.cwd())

    @classmethod
    def from_env(cls) -> SandboxConfig:
        """Load config from environment variables."""
        # Try loading .env file if python-dotenv is available
        env_path = Path.cwd() / ".env"
        if env_path.exists():
            try:
                for line in env_path.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, value = line.partition("=")
                        os.environ.setdefault(key.strip(), value.strip())
            except OSError:
                pass

        return cls(
            max_agents=int(os.getenv("MAX_AGENTS", "10")),
            sandbox_timeout=int(os.getenv("SANDBOX_TIMEOUT", "300")),
            api_host=os.getenv("API_HOST", "0.0.0.0"),
            api_port=int(os.getenv("API_PORT", "8000")),
            sandbox_mode=os.getenv("SANDBOX_MODE", "subprocess"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )


# Singleton
_config: SandboxConfig | None = None


def get_config() -> SandboxConfig:
    global _config
    if _config is None:
        _config = SandboxConfig.from_env()
    return _config
