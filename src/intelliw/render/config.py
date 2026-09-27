"""`_config.json`: design metadata and options (docs/design/04-design.md)."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from intelliw.render.layout import CONFIG_FILE


class DesignConfig(BaseModel):
    """Metadata and options of a design; never business data values."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field("", description="Display name, e.g. 'Clinic, light'.")
    description: str = ""
    options: dict[str, JsonValue] = Field(
        default_factory=dict, description="Design settings for templates."
    )


def load_config(design_dir: Path) -> DesignConfig:
    """The design's config; defaults without `_config.json`. Raises ValueError if invalid."""
    path = design_dir / CONFIG_FILE
    if not path.is_file():
        return DesignConfig()
    return DesignConfig.model_validate_json(path.read_bytes())
