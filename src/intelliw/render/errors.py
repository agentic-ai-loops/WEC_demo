"""Render failures; each names the design (or output) file it is about."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Problem:
    """One problem found by the design check or the link check."""

    file: str
    message: str
    line: int | None = None

    def __str__(self) -> str:
        where = f"{self.file}:{self.line}" if self.line is not None else self.file
        return f"{where}: {self.message}"


class RenderFailure(Exception):
    """Base of everything that stops a render; nothing is written."""


class DesignNotFound(RenderFailure):
    pass


class CheckFailed(RenderFailure):
    """The design check (or the link check) found problems."""

    def __init__(self, stage: str, problems: list[Problem]):
        super().__init__(f"{stage}: {len(problems)} problem(s)")
        self.stage = stage
        self.problems = problems


class RenderError(RenderFailure):
    """A failure while rendering one file (a query, a template, a copy)."""

    def __init__(self, file: str, message: str, line: int | None = None):
        self.problem = Problem(file, message, line)
        super().__init__(str(self.problem))
