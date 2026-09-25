import json
import sqlite3
from pathlib import Path

import pytest
from businessdata_sample import whitby
from typer.testing import CliRunner

from intelliw.businessdata import documents
from intelliw.businessdata.database import create_db_engine, init_db, session_factory
from intelliw.businessdata.schema import BusinessData
from intelliw.cli.business import app
from intelliw.config import Settings
from intelliw.workspace import Workspace

runner = CliRunner()


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    """A workspace with the sample document and every asset file present."""
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


def run(*args: str):
    return runner.invoke(app, list(args))


def test_doctor_healthy(ws):
    result = run("doctor", "-w", str(ws.root))
    assert result.exit_code == 0, result.output
    assert "Whitby Eye Care" in result.output
    assert "✔ Referential integrity" in result.output
    assert "every asset file exists" in result.output


def test_doctor_reports_missing_and_unregistered_files(ws):
    (ws.resources_dir / "logo.png").unlink()
    (ws.resources_dir / "stray.png").write_bytes(b"png")
    result = run("doctor", "-w", str(ws.root))
    assert result.exit_code == 1
    assert "Checking: active" in result.output
    assert result.output.count("asset logo → logo.png") == 1
    assert "stray.png" in result.output
    # snapshot 1 uses the same file
    result = run("doctor", "-w", str(ws.root), "--version", "1")
    assert result.exit_code == 1
    assert "Checking: snapshot 1" in result.output
    assert "asset logo → logo.png" in result.output


def test_doctor_reports_integrity_problems(ws):
    db = sqlite3.connect(ws.database_file)  # foreign keys are off on this connection
    db.execute("UPDATE services SET category = 'nope' WHERE version = 0 AND id = 'eye-exams'")
    db.execute(
        "UPDATE staff SET deleted = 1 WHERE version = 0 AND id = 'dr-andrea-chan'"
    )  # an open review targets her
    db.commit()
    db.close()
    result = run("doctor", "-w", str(ws.root))
    assert result.exit_code == 1
    out = " ".join(result.output.split())
    assert "refers to a missing service_categories row" in out
    assert "services[eye-exams].category: no service category 'nope'" in out
    assert "open review targets deleted 'dr-andrea-chan'" in out


def test_dump_active_and_snapshot(ws):
    result = run("dump", "-w", str(ws.root))
    assert result.exit_code == 0
    dumped = json.loads(result.stdout)
    assert BusinessData.model_validate(dumped) == BusinessData.model_validate(whitby())

    snap = json.loads(run("dump", "-w", str(ws.root), "--version", "1").stdout)
    assert snap["snapshot"]["number"] == 1


def test_dump_unknown_snapshot(ws):
    assert run("dump", "-w", str(ws.root), "--tag", "nope").exit_code == 2


def test_no_database(tmp_path):
    result = run("doctor", "-w", str(tmp_path))
    assert result.exit_code == 1
    assert "No database" in result.output


def test_no_workspace_configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("WORKSPACE", raising=False)
    result = run("doctor")
    assert result.exit_code == 2
    assert "No workspace" in result.output


def test_workspace_from_env_is_relative_to_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("WORKSPACE=examples/shop\n")
    sub = tmp_path / "deeper"
    sub.mkdir()
    monkeypatch.chdir(sub)
    monkeypatch.delenv("WORKSPACE", raising=False)
    assert Settings.from_env().workspace == (tmp_path / "examples" / "shop").resolve()


def test_snapshot_versions_activate(ws):
    w = ("-w", str(ws.root))
    result = run("snapshot", *w, "--tag", "reviewed")
    assert result.exit_code == 0 and "snapshot 2" in result.output
    assert run("snapshot", *w, "--tag", "reviewed").exit_code == 1  # duplicate tag

    db = sqlite3.connect(ws.database_file)
    db.execute("UPDATE staff SET name = 'Edited' WHERE version = 0 AND id = 'dr-andrea-chan'")
    db.execute("UPDATE version_state SET modified = 1")
    db.commit()
    db.close()

    listing = run("versions", *w)
    assert listing.exit_code == 0
    assert "reviewed" in listing.output and "modified" in listing.output

    result = run("activate", *w, "--tag", "reviewed")
    assert result.exit_code == 0
    assert "kept in snapshot 3" in result.output
    staff = json.loads(run("dump", *w).stdout)["staff"]
    assert staff[1]["name"] == "Dr. Andrea Chan"
    staff3 = json.loads(run("dump", *w, "--version", "3").stdout)["staff"]
    assert staff3[1]["name"] == "Edited"


def test_activate_argument_errors(ws):
    w = ("-w", str(ws.root))
    assert run("activate", *w).exit_code == 2
    assert run("activate", *w, "--version", "1", "--tag", "x").exit_code == 2
    assert run("activate", *w, "--version", "99").exit_code == 2
    assert run("dump", *w, "--tag", "nope").exit_code == 2


def test_doctor_checks_only_the_selected_version(ws):
    db = sqlite3.connect(ws.database_file)  # foreign keys are off on this connection
    db.execute("UPDATE services SET category = 'nope' WHERE version = 1 AND id = 'eye-exams'")
    db.execute("UPDATE snapshots SET tag = 'imported' WHERE number = 1")
    db.commit()
    db.close()
    w = ("-w", str(ws.root))
    assert run("doctor", *w).exit_code == 0  # the active version is fine
    for selector in (("--version", "1"), ("--tag", "imported")):
        result = run("doctor", *w, *selector)
        assert result.exit_code == 1
        out = " ".join(result.output.split())
        assert "refers to a missing service_categories row" in out
        assert "snapshot 1: services[eye-exams].category: no service category 'nope'" in out


def test_doctor_version_errors(ws):
    w = ("-w", str(ws.root))
    assert run("doctor", *w, "--version", "9").exit_code == 2
    assert run("doctor", *w, "--version", "1", "--tag", "x").exit_code == 2
