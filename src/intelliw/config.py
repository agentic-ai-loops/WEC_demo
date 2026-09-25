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
    graphql_host: str = "0.0.0.0"
    graphql_port: int = 9001
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 9002
    run_dir: Path = Path(".run")
    workspace: Path | None = None  # WORKSPACE; relative paths are relative to the .env file

    @classmethod
    def from_env(cls) -> "Settings":
        dotenv = find_dotenv(usecwd=True)
        load_dotenv(dotenv)
        base = Path(dotenv).parent if dotenv else Path.cwd()
        workspace = os.getenv("WORKSPACE")
        return cls(
            graphql_host=os.getenv("GRAPHQL_HOST", cls.graphql_host),
            graphql_port=int(os.getenv("GRAPHQL_PORT", cls.graphql_port)),
            mcp_host=os.getenv("MCP_HOST", cls.mcp_host),
            mcp_port=int(os.getenv("MCP_PORT", cls.mcp_port)),
            run_dir=Path(os.getenv("INTELLIW_RUN_DIR", cls.run_dir)),
            workspace=(base / workspace).resolve() if workspace else None,
        )

    @property
    def graphql_url(self) -> str:
        return f"http://{_client_host(self.graphql_host)}:{self.graphql_port}/graphql"

    @property
    def mcp_url(self) -> str:
        return f"http://{_client_host(self.mcp_host)}:{self.mcp_port}/mcp"
