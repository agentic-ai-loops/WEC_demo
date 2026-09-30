"""Messages and results of the re-render job queue."""

from pydantic import BaseModel, ConfigDict, Field

from intelliw.businessdata.schema import UtcDatetime


class MutationEvent(BaseModel):
    """A committed #businessdata change: the argument of every re-render job."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mutation: str = Field(
        description="The GraphQL mutation that made the change, e.g. updateService."
    )
    updated_at: UtcDatetime = Field(description="When the change was committed (UTC).")


class SiteRender(BaseModel):
    """The outcome of re-rendering one design."""

    design: str
    ok: bool
    output_dir: str | None = None  # the site, if it was written
    pages: int = 0
    error: str | None = None  # what went wrong, if not ok


class RenderAllResult(BaseModel):
    """What a re-render job did: one entry per design of the workspace."""

    event: MutationEvent
    workspace: str
    sites: list[SiteRender]

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.sites)
