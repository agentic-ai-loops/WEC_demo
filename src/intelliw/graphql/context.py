"""Per-request context: a database session and DataLoaders over `businessdata.queries`."""

from collections import defaultdict
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from sqlalchemy.orm import Session, sessionmaker
from strawberry.dataloader import DataLoader

from intelliw.businessdata import queries
from intelliw.businessdata.schema import Entity
from intelliw.businessdata.tables import ACTIVE


class View(NamedTuple):
    """What a query reads: a version, with or without hidden entities.

    Every GraphQL object carries the view it was read in, so nested fields resolve in
    their parent's version and with their parent's `includeHidden`.
    """

    version: int = ACTIVE
    include_hidden: bool = False


def _per_view[K: Hashable, V](
    fetch: Callable[[View, list[Any]], Sequence[V]],
) -> Callable[[list[tuple[View, K]]], Any]:
    """A DataLoader load function for keys `(view, key)`, one query per view."""

    async def load(keys: list[tuple[View, K]]) -> list[V]:
        by_view: dict[View, list[K]] = defaultdict(list)
        for view, key in keys:
            by_view[view].append(key)
        results: dict[tuple[View, K], V] = {}
        for view, view_keys in by_view.items():
            results.update(
                ((view, k), v) for k, v in zip(view_keys, fetch(view, view_keys), strict=True)
            )
        return [results[k] for k in keys]

    return load


class Loaders:
    """Batched loads for one request. Keys are `(view, ...)`; results are schema models."""

    def __init__(self, session: Session):
        self._session = session
        self._entities: dict[type[Entity], DataLoader[Any, Any]] = {}
        s = session

        def batched(fetch: Callable[..., Any]) -> DataLoader[Any, Any]:
            return DataLoader(
                _per_view(
                    lambda w, keys: fetch(s, w.version, keys, include_hidden=w.include_hidden)
                )
            )

        self.services_by_category = batched(queries.services_by_category)
        self.faqs_by_service = batched(queries.faqs_by_service)
        self.actions_by_service = batched(queries.actions_by_service)
        self.actions_by_channel = batched(queries.actions_by_channel)
        self.asset_usage = batched(queries.asset_usage)
        # review items cannot be hidden
        self.reviews_by_target = DataLoader(
            _per_view(lambda w, targets: queries.reviews_by_target(s, w.version, targets))
        )

    def entity[E: Entity](self, model: type[E]) -> DataLoader[tuple[View, str], E | None]:
        """Entities of one collection by `(view, id)`; `None` if missing, deleted or hidden."""
        if model not in self._entities:
            session = self._session
            self._entities[model] = DataLoader(
                _per_view(
                    lambda w, ids: queries.get_entities(
                        session, model, w.version, ids, include_hidden=w.include_hidden
                    )
                )
            )
        return self._entities[model]


@dataclass
class Context:
    """The GraphQL context: one session per request, opened on first use."""

    sessions: sessionmaker[Session] | None
    resources_dir: Path | None = None
    # used by query fields whose version / snapshot / includeHidden arguments are omitted
    default_view: View = field(default_factory=View)
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
