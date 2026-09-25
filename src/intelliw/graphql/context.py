"""Per-request context: a database session and DataLoaders over `businessdata.queries`."""

from collections import defaultdict
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from strawberry.dataloader import DataLoader

from intelliw.businessdata import queries
from intelliw.businessdata.schema import Entity


def _per_version[K: Hashable, V](
    fetch: Callable[[int, list[Any]], Sequence[V]],
) -> Callable[[list[tuple[int, K]]], Any]:
    """A DataLoader load function for keys `(version, key)`, one query per version."""

    async def load(keys: list[tuple[int, K]]) -> list[V]:
        by_version: dict[int, list[K]] = defaultdict(list)
        for version, key in keys:
            by_version[version].append(key)
        results: dict[tuple[int, K], V] = {}
        for version, version_keys in by_version.items():
            results.update(
                ((version, k), v)
                for k, v in zip(version_keys, fetch(version, version_keys), strict=True)
            )
        return [results[k] for k in keys]

    return load


class Loaders:
    """Batched loads for one request. Keys are `(version, ...)`; results are schema models."""

    def __init__(self, session: Session):
        self._session = session
        self._entities: dict[type[Entity], DataLoader[Any, Any]] = {}
        s = session
        self.services_by_category = DataLoader(
            _per_version(lambda v, ids: queries.services_by_category(s, v, ids))
        )
        self.faqs_by_service = DataLoader(
            _per_version(lambda v, ids: queries.faqs_by_service(s, v, ids))
        )
        self.actions_by_service = DataLoader(
            _per_version(lambda v, ids: queries.actions_by_service(s, v, ids))
        )
        self.actions_by_channel = DataLoader(
            _per_version(lambda v, ids: queries.actions_by_channel(s, v, ids))
        )
        self.reviews_by_target = DataLoader(
            _per_version(lambda v, targets: queries.reviews_by_target(s, v, targets))
        )
        self.asset_usage = DataLoader(_per_version(lambda v, ids: queries.asset_usage(s, v, ids)))

    def entity[E: Entity](self, model: type[E]) -> DataLoader[tuple[int, str], E | None]:
        """Entities of one collection by `(version, id)`; `None` if missing or deleted."""
        if model not in self._entities:
            session = self._session
            self._entities[model] = DataLoader(
                _per_version(lambda v, ids: queries.get_entities(session, model, v, ids))
            )
        return self._entities[model]


@dataclass
class Context:
    """The GraphQL context: one session per request, opened on first use."""

    sessions: sessionmaker[Session] | None
    resources_dir: Path | None = None
    _session: Session | None = field(default=None, repr=False)
    _loaders: Loaders | None = field(default=None, repr=False)

    @property
    def session(self) -> Session:
        if self._session is None:
            if self.sessions is None:
                raise RuntimeError("no workspace database: set WORKSPACE in .env")
            self._session = self.sessions()
        return self._session

    @property
    def loaders(self) -> Loaders:
        if self._loaders is None:
            self._loaders = Loaders(self.session)
        return self._loaders

    def reset_loaders(self) -> None:
        """Forget cached loads (after a mutation changed the data)."""
        self._loaders = None

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
