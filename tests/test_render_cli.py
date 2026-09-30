"""`uv run render` (docs/notes/2026-09-26.md, Task 1)."""

import pytest
from conftest import site, sites_dir
from typer.testing import CliRunner

from intelliw.businessdata import documents
from intelliw.businessdata.database import create_db_engine, session_factory
from intelliw.cli.render import app
from intelliw.workspace import Workspace

runner = CliRunner()


@pytest.fixture
def ws(data_ws: Workspace) -> Workspace:
    root = data_ws.design("d")
    root.mkdir(parents=True)
    (root / "site.gql").write_text("{ business { name } }")
    (root / "page.html.j2").write_text("{{ site.business.name }}")
    return data_ws


def run(ws: Workspace, *args: str):
    return runner.invoke(app, [str(ws.root), "--run-dir", str(sites_dir(ws).parent), *args])


def test_render_writes_the_site(ws):
    result = run(ws, "--design", "d")
    assert result.exit_code == 0, result.output
    assert "Rendered design d with the active version" in result.output
    assert (site(ws, "d") / "index.html").read_text() == "Whitby Eye Care"


def test_template_alias_and_dryrun(ws):
    result = run(ws, "--template", "d", "--dryrun")
    assert result.exit_code == 0, result.output
    assert "Dry run of design d" in result.output and "nothing written" in result.output
    assert not sites_dir(ws).exists()


def test_snapshot_and_hidden_options(ws):
    engine = create_db_engine(ws.database_file)
    with session_factory(engine)() as s:
        documents.take_snapshot(s, "v2")
        s.commit()
    engine.dispose()
    result = run(ws, "--design", "d", "--snapshot", "v2", "--include-hidden")
    assert result.exit_code == 0, result.output
    assert "snapshot 2 (v2), hidden entities included" in result.output
    assert '"snapshot": "v2"' in (site(ws, "d") / "render.json").read_text()
    assert run(ws, "--design", "d", "--version", "1").exit_code == 0
    assert '"version": 1,' in (site(ws, "d") / "render.json").read_text()


@pytest.mark.parametrize(
    "args",
    [
        ["--design", "missing"],
        ["--design", "d", "--version", "1", "--snapshot", "x"],
        ["--design", "d", "--snapshot", "nope"],
        ["--design", "d", "--version", "99"],
    ],
)
def test_usage_errors_exit_2(ws, args):
    result = run(ws, *args)
    assert result.exit_code == 2, result.output


def test_no_database_exits_2(tmp_path):
    result = runner.invoke(app, [str(tmp_path), "--design", "d"])
    assert result.exit_code == 2
    assert "No #businessdata database" in result.output


def test_check_problems_exit_1(ws):
    (ws.design("d") / "page.gql").write_text("{ staff(includeHidden: true) { id } }")
    result = run(ws, "--design", "d")
    assert result.exit_code == 1
    assert "Design check failed" in result.output
    assert "page.gql:1: argument includeHidden is set by the render options" in result.output


def test_render_errors_exit_1(ws):
    (ws.design("d") / "page.html.j2").write_text("{{ nope }}")
    result = run(ws, "--design", "d")
    assert result.exit_code == 1
    assert "Render failed: page.html.j2:1: 'nope' is undefined" in result.output


def test_workspace_defaults_to_the_env_setting(ws, tmp_path, monkeypatch):
    """No workspace argument: WORKSPACE from .env, relative to the .env file."""
    (tmp_path / ".env").write_text(f"WORKSPACE={ws.root.relative_to(tmp_path)}\n")
    sub = tmp_path / "elsewhere"
    sub.mkdir()
    monkeypatch.chdir(sub)
    monkeypatch.delenv("WORKSPACE", raising=False)
    monkeypatch.delenv("INTELLIW_RUN_DIR", raising=False)
    result = runner.invoke(app, ["--design", "d"])
    assert result.exit_code == 0, result.output
    # the default run dir is relative to the .env file, not to where the command runs
    assert (tmp_path / "run" / "sites" / ws.root.name / "d" / "index.html").is_file()


def test_no_workspace_configured_exits_2(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .env here or above
    monkeypatch.delenv("WORKSPACE", raising=False)
    result = runner.invoke(app, ["--design", "d"])
    assert result.exit_code == 2
    assert "No workspace" in result.output
