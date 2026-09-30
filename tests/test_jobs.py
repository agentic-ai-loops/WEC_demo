"""The re-render job queue (docs/notes/2026-09-29.md, Task 2)."""

import asyncio
import json
import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import fakeredis
import pytest
from conftest import site, sites_dir
from mcp import Client
from pydantic import ValidationError
from rq import SimpleWorker
from typer.testing import CliRunner

from intelliw.businessdata.database import create_db_engine, session_factory
from intelliw.config import Settings
from intelliw.graphql.context import Context
from intelliw.graphql.schema import schema
from intelliw.jobs import MutationEvent, enqueue, queue_publisher, render_queue
from intelliw.jobs.queue import JOB
from intelliw.jobs.tasks import render_designs
from intelliw.mcp import create_server
from intelliw.mcp.server import schema_executor
from intelliw.workspace import Workspace

EXAMPLE_DESIGNS = Path(__file__).parent.parent / "workspaces" / "whitby_eye_care" / "design"


# ---- the message ---------------------------------------------------------------------


def test_mutation_event_model():
    e = MutationEvent(mutation="updateService", updated_at=datetime(2026, 9, 29, 12, tzinfo=UTC))
    assert e.model_dump(mode="json") == {
        "mutation": "updateService",
        "updated_at": "2026-09-29T12:00:00Z",
    }
    assert MutationEvent.model_validate(e.model_dump(mode="json")) == e
    with pytest.raises(ValidationError):
        MutationEvent.model_validate({"mutation": "x", "updated_at": "now", "extra": 1})


# ---- every committed mutation publishes one event ------------------------------------


async def test_graphql_mutations_publish_events(engine, session):
    events: list[MutationEvent] = []

    async def run(document: str):
        ctx = Context(session_factory(engine), publish=events.append)
        return await schema.execute(document, context_value=ctx)

    before = datetime.now(UTC)
    r = await run('mutation { updateService(id: "eye-exams", patch: {summary: "x"}) { id } }')
    assert r.errors is None
    assert [e.mutation for e in events] == ["updateService"]
    assert before - timedelta(seconds=1) <= events[0].updated_at <= datetime.now(UTC)

    await run('mutation { takeSnapshot(tag: "t") { number } }')
    assert [e.mutation for e in events] == ["updateService", "takeSnapshot"]

    # a query, and a mutation that fails, publish nothing
    await run("{ business { name } }")
    failed = await run('mutation { updateService(id: "nope", patch: {summary: "x"}) { id } }')
    assert failed.errors and [e.mutation for e in events] == ["updateService", "takeSnapshot"]


async def test_mcp_mutations_publish_events(engine, session):
    events: list[MutationEvent] = []
    server = create_server(schema_executor(session_factory(engine), None, events.append))
    async with Client(server) as c:
        await c.call_tool(
            "graphql_mutate",
            {
                "document": (
                    'mutation { updateStaffMember(id: "dr-andrea-chan", patch: {role: "OD"}) '
                    "{ id } }"
                )
            },
        )
    assert [e.mutation for e in events] == ["updateStaffMember"]


# ---- the queue -----------------------------------------------------------------------


@pytest.fixture
def redis():
    return fakeredis.FakeStrictRedis()


def test_enqueue_puts_the_event_on_the_queue(redis):
    queue = render_queue(redis)
    event = MutationEvent(mutation="moveService", updated_at=datetime(2026, 9, 29, tzinfo=UTC))
    job = enqueue(queue, event)
    assert queue.count == 1
    assert job.func_name == JOB
    assert job.args == ({"mutation": "moveService", "updated_at": "2026-09-29T00:00:00Z"},)


def test_publisher_never_fails_a_mutation(caplog):
    """With Redis down the change is committed anyway; the lost re-render is logged."""
    publish = queue_publisher(Settings(redis_url="redis://127.0.0.1:1/0"))
    with caplog.at_level(logging.WARNING):
        publish(MutationEvent(mutation="updateBusiness", updated_at=datetime.now(UTC)))
    assert "re-render not queued after updateBusiness" in caplog.text


# ---- the worker re-renders every design ----------------------------------------------


@pytest.fixture
def ws(data_ws: Workspace) -> Workspace:
    for name in ("clinic", "clinic-pro"):
        shutil.copytree(EXAMPLE_DESIGNS / name, data_ws.design(name))
    return data_ws


def test_render_designs_renders_every_design(ws):
    bad = ws.design("broken")
    bad.mkdir()
    (bad / "page.html.j2").write_text("{{ nope }}")
    event = MutationEvent(mutation="manual", updated_at=datetime.now(UTC))
    result = render_designs(ws, sites_dir(ws), event)
    by_design = {s.design: s for s in result.sites}
    assert sorted(by_design) == ["broken", "clinic", "clinic-pro"]
    assert by_design["clinic"].ok and by_design["clinic-pro"].ok
    assert not by_design["broken"].ok and "undefined" in (by_design["broken"].error or "")
    assert (site(ws, "clinic") / "index.html").is_file()
    assert (site(ws, "clinic-pro") / "index.html").is_file()
    assert not result.ok


def test_worker_consumes_jobs_and_rerenders(ws, redis, monkeypatch):
    """A mutation's job, run by an rq worker, re-renders every design from the active data."""
    monkeypatch.setenv("WORKSPACE", str(ws.root))
    monkeypatch.setenv("INTELLIW_RUN_DIR", str(sites_dir(ws).parent))
    queue = render_queue(redis)

    # the owner renames the business; the mutation queues a job
    engine = create_db_engine(ws.database_file)
    events: list[MutationEvent] = []
    try:
        ctx = Context(session_factory(engine), publish=events.append)
        asyncio.run(
            schema.execute(
                'mutation { updateBusiness(patch: {name: "Whitby Eye Care & Optical"}) { name } }',
                context_value=ctx,
            )
        )
    finally:
        engine.dispose()
    for e in events:
        enqueue(queue, e)
    assert queue.count == 1

    SimpleWorker([queue], connection=redis).work(burst=True)

    after = render_queue(redis)
    assert after.count == 0
    job = after.fetch_job(after.finished_job_registry.get_job_ids()[0])
    assert job is not None
    result = job.return_value()
    assert result is not None
    assert result["event"]["mutation"] == "updateBusiness"
    assert [s["design"] for s in result["sites"]] == ["clinic", "clinic-pro"]
    assert all(s["ok"] for s in result["sites"])
    for design in ("clinic", "clinic-pro"):
        home = (site(ws, design) / "index.html").read_text()
        assert "Whitby Eye Care &amp; Optical" in home, design
        info = json.loads((site(ws, design) / "render.json").read_text())
        assert info["businessdata"]["version_name"] == "active"


# ---- the jobs CLI --------------------------------------------------------------------


def test_jobs_cli_enqueue_and_status(redis, monkeypatch, tmp_path):
    from intelliw.cli import jobs as cli

    monkeypatch.setattr(cli, "connect", lambda settings: redis)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["enqueue", "--mutation", "manual"])
    assert result.exit_code == 0, result.output
    assert "Queued" in result.output
    status = runner.invoke(cli.app, ["status", "--run-dir", str(tmp_path)])
    assert "waiting" in status.output and "1" in status.output
    assert status.exit_code == 1  # no worker running
