"""Load/save #businessdata from a workspace."""

from intelliw.businessdata.schema import BusinessData
from intelliw.workspace import Workspace


def load(ws: Workspace) -> BusinessData:
    return BusinessData.model_validate_json(ws.businessdata_file.read_text())


def save(ws: Workspace, data: BusinessData) -> None:
    ws.businessdata_dir.mkdir(parents=True, exist_ok=True)
    ws.businessdata_file.write_text(data.model_dump_json(indent=2) + "\n")
