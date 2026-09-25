"""Filesystem layout of an owner's workspace (see docs/design/00-architecture.md)."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Workspace:
    root: Path

    @property
    def businessdata_dir(self) -> Path:
        return self.root / "businessdata"

    @property
    def businessdata_file(self) -> Path:
        return self.businessdata_dir / "business.json"

    @property
    def database_file(self) -> Path:
        return self.businessdata_dir / "business.db"

    @property
    def resources_dir(self) -> Path:
        return self.businessdata_dir / "resources"

    @property
    def design_dir(self) -> Path:
        return self.root / "design"

    @property
    def templates_dir(self) -> Path:
        return self.design_dir / "templates"

    @property
    def assets_dir(self) -> Path:
        return self.design_dir / "assets"

    @property
    def site_dir(self) -> Path:
        return self.root / "_site"
