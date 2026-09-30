"""Service configuration, read from the environment (and `.env` if present)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


def _client_host(host: str) -> str:
    """Address a local client should use to reach a server bound to `host`."""
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


@dataclass(frozen=True)
class Settings:
    """Settings of the single intelliw server (`svr start`) and its clients.

    One server serves `/mcp`, `/graphql` and `/health` on `host`:`port`.
    """

    host: str = "0.0.0.0"  # SVR_HOST
    port: int = 8000  # SVR_PORT
    # INTELLIW_RUN_DIR: svr.pid, worker.pid, logs and the rendered sites (relative paths
    # are relative to the .env file)
    run_dir: Path = Path("run")
    workspace: Path | None = None  # WORKSPACE; relative paths are relative to the .env file
    redis_url: str = "redis://localhost:6379/0"  # REDIS_URL: the re-render job queue

    @classmethod
    def from_env(cls) -> "Settings":
        dotenv = find_dotenv(usecwd=True)
        load_dotenv(dotenv)
        base = Path(dotenv).parent if dotenv else Path.cwd()
        workspace = os.getenv("WORKSPACE")
        return cls(
            host=os.getenv("SVR_HOST", cls.host),
            port=int(os.getenv("SVR_PORT", cls.port)),
            run_dir=base / os.getenv("INTELLIW_RUN_DIR", str(cls.run_dir)),
            workspace=(base / workspace).resolve() if workspace else None,
            redis_url=os.getenv("REDIS_URL", cls.redis_url),
        )

    @property
    def sites_dir(self) -> Path:
        """Where rendered sites go: `{run_dir}/sites/{workspace}/{design}/`."""
        return self.run_dir / "sites"

    @property
    def base_url(self) -> str:
        """Where a local client reaches the server."""
        return f"http://{_client_host(self.host)}:{self.port}"

    @property
    def graphql_url(self) -> str:
        return f"{self.base_url}/graphql"

    @property
    def mcp_url(self) -> str:
        return f"{self.base_url}/mcp"

    @property
    def health_url(self) -> str:
        return f"{self.base_url}/health"
