"""Asset paths stay inside businessdata/resources/ (01-businessdata, *Assets*)."""

import sqlite3

import pytest
from pydantic import ValidationError

from intelliw.businessdata.database import create_db_engine, session_factory
from intelliw.businessdata.doctor import check
from intelliw.businessdata.resources import UnsafePath, check_path, resource_file
from intelliw.businessdata.schema import Asset

GOOD = ["logo.png", "Dr-Raniero-Fernando.jpg", "_x.png", "staff/dr jane.jpg", "a/b/c.v2.png"]
BAD = [
    "",
    "/etc/passwd",
    "../secret.png",
    "a/../../b.png",
    "a/./b.png",
    ".hidden.png",
    "a/.git/config",
    "a//b.png",
    "a/",
    "a\\b.png",
    "C:/x.png",
    "~/x.png",
    "x" * 256,
]


@pytest.mark.parametrize("path", GOOD)
def test_good_paths(path):
    check_path(path)
    Asset.model_validate(_asset(path))


@pytest.mark.parametrize("path", BAD)
def test_bad_paths_are_refused(path):
    with pytest.raises(UnsafePath):
        check_path(path)
    with pytest.raises(ValidationError):
        Asset.model_validate(_asset(path))


def test_symlinks_out_of_the_folder_are_refused(tmp_path):
    resources = tmp_path / "resources"
    resources.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    (resources / "link.png").symlink_to(secret)
    (resources / "inner.png").write_text("png")
    (resources / "ok.png").symlink_to(resources / "inner.png")
    with pytest.raises(UnsafePath, match="outside"):
        resource_file(resources, "link.png")
    assert resource_file(resources, "ok.png").is_file()


def test_doctor_reports_an_unsafe_path_in_the_data(data_ws):
    db = sqlite3.connect(data_ws.database_file)
    db.execute("UPDATE assets SET path = '../../etc/passwd' WHERE version = 0 AND id = 'logo'")
    db.commit()
    db.close()
    engine = create_db_engine(data_ws.database_file)
    with session_factory(engine)() as s:
        report = check(s, data_ws.resources_dir)
    engine.dispose()
    # entities are validated on reading: the version is reported as unreadable
    assert any("cannot be read" in p and "../../etc/passwd" in p for p in report.problems)


def _asset(path: str) -> dict:
    t = "2026-09-24T14:05:12Z"
    return {
        "id": "a",
        "position": 0,
        "created_at": t,
        "updated_at": t,
        "type": "photo",
        "path": path,
    }
