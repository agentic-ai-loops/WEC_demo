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

DEMO = Path(__file__).parent.parent / "examples" / "demo"


@pytest.fixture
def demo_ws(tmp_path: Path) -> Workspace:
    """A fresh copy of the demo workspace."""
    root = tmp_path / "demo"
    shutil.copytree(DEMO, root, ignore=shutil.ignore_patterns("_site"))
    return Workspace(root)


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
