import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from businessdata_sample import whitby
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from intelliw.businessdata import documents
from intelliw.businessdata.database import create_db_engine, init_db, session_factory
from intelliw.businessdata.schema import BusinessData
from intelliw.workspace import Workspace

DEMO = Path(__file__).parent.parent / "workspaces" / "demo"


def pytest_configure(config: pytest.Config) -> None:
    """Tests compare CLI output as plain text: never let the terminal force colour codes.

    Runs before test modules are collected, i.e. before the CLI modules create their
    rich consoles (which read these variables).
    """
    for var in ("FORCE_COLOR", "CLICOLOR_FORCE", "TTY_COMPATIBLE", "TTY_INTERACTIVE"):
        os.environ.pop(var, None)
    os.environ["NO_COLOR"] = "1"


@pytest.fixture
def demo_ws(tmp_path: Path) -> Workspace:
    """A fresh copy of the demo workspace."""
    root = tmp_path / "demo"
    shutil.copytree(DEMO, root, ignore=shutil.ignore_patterns("_site"))
    return Workspace(root)


def sites_dir(ws: Workspace) -> Path:
    """The sites folder of a test workspace: `{run_dir}/sites` with run_dir next to it."""
    return ws.root.parent / "run" / "sites"


def site(ws: Workspace, design: str) -> Path:
    """Where `design`'s site is rendered: `{run_dir}/sites/{workspace}/{design}/`."""
    return sites_dir(ws) / ws.root.name / design


@pytest.fixture
def data_ws(tmp_path: Path) -> Workspace:
    """A workspace with the sample #businessdata in its database and every asset file."""
    ws = Workspace(tmp_path / "ws")
    ws.resources_dir.mkdir(parents=True)
    data = BusinessData.model_validate(whitby())
    for asset in data.assets:
        (ws.resources_dir / asset.path).write_bytes(b"png")
    engine = create_db_engine(ws.database_file)
    init_db(engine)
    with session_factory(engine)() as s:
        documents.initialize(s, data)
        s.commit()
    engine.dispose()
    return ws


@pytest.fixture
def engine() -> Engine:
    """An in-memory database with the schema."""
    engine = create_db_engine(None)
    init_db(engine)
    return engine


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A session on a database initialized with the sample document."""
    with session_factory(engine)() as s:
        documents.initialize(s, BusinessData.model_validate(whitby()))
        s.commit()
        yield s


class QueryCounter:
    """Counts SQL statements executed on an engine."""

    def __init__(self, engine: Engine):
        self.count = 0
        event.listen(engine, "before_cursor_execute", self._on_execute)

    def _on_execute(self, *_args) -> None:
        self.count += 1
