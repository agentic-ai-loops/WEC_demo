"""MCP prompts: Jinja2 templates (in `prompts/`) rendered with context derived from code.

The entity documentation comes from the GraphQL schema — itself generated from the
Pydantic models — so prompt text about the data cannot drift from the API: entity and
field names, types and descriptions, query and mutation names, enum values and error
codes are all read from the code at render time. Shared partials (`_discovery`,
`_conventions`, `_formatting`) keep the wording identical across prompts.
"""

import re
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from graphql import (
    GraphQLEnumType,
    GraphQLObjectType,
    GraphQLSchema,
    build_schema,
    get_named_type,
    is_enum_type,
    is_object_type,
)
from jinja2 import Environment, PackageLoader, StrictUndefined

from intelliw.businessdata import mutations, queries
from intelliw.graphql import types as t
from intelliw.graphql.schema import SPECS, schema
from intelliw.mcp import names

# name -> (title, description); the MCP server registers these.
PROMPTS: dict[str, tuple[str, str]] = {
    "business_schema": (
        "Business data reference",
        "What each kind of business record is, its fields and how to query and change it.",
    ),
    "business_overview": (
        "Business overview",
        "A dashboard of the whole business: counts, open questions, recent changes.",
    ),
    "explore_business": (
        "Explore the business",
        "Walk through the business, or one area of it (e.g. services, staff), in detail.",
    ),
    "find_information": (
        "Find information",
        "Answer a specific question about the business with the data behind it.",
    ),
    "review_concerns": (
        "Review concerns",
        "Find and work through problems: open questions, missing photos, hidden items.",
    ),
    "update_business": (
        "Update the business",
        "Make a change safely: confirm, snapshot, change, verify, offer to undo.",
    ),
}

# How the reference prompt groups the entities (GraphQL type names).
SECTIONS: list[tuple[str, list[str]]] = [
    ("The business", ["Business"]),
    ("Contact points and locations", ["ContactPoint", "Location"]),
    ("Services and products", ["ServiceCategory", "Service", "ProductCategory"]),
    ("Team and reputation", ["StaffMember", "Faq", "SocialLink", "Affiliation"]),
    ("Customer actions", ["CustomerAction"]),
    ("Images", ["Asset"]),
    ("Questions for the owner", ["ReviewItem"]),
]
VALUE_TYPES = ["Address", "GeoPoint", "OpeningHours"]
VERSION_TYPES = ["Snapshot"]

# Mutations beyond the six standard ones, per entity (kept only if in the schema).
_EXTRA_MUTATIONS: dict[str, list[str]] = {
    "Business": ["updateBusiness"],
    "Location": ["setOpeningHours"],
    "ReviewItem": ["createReview", "resolveReview", "dismissReview", "reopenReview"],
}
_META_FIELDS = ("hidden", "createdAt", "updatedAt", "version")


@dataclass(frozen=True)
class FieldDoc:
    name: str
    type: str  # GraphQL type, e.g. `[String!]!`
    description: str
    kind: str  # scalar | enum | value | entity


@dataclass(frozen=True)
class EntityDoc:
    name: str
    description: str
    fields: list[FieldDoc]
    list_query: str | None = None
    by_id_query: str | None = None
    mutations: list[str] = field(default_factory=list)

    @property
    def data_fields(self) -> list[FieldDoc]:
        return [f for f in self.fields if f.kind != "entity" and f.name not in _META_FIELDS]

    @property
    def hideable(self) -> bool:
        return any(f.name == "hidden" for f in self.fields)

    def selection(self) -> str:
        """A GraphQL selection of the entity's own scalar fields (a valid example query)."""
        picked = [f.name for f in self.data_fields if f.kind in ("scalar", "enum")]
        return " ".join(picked + (["hidden"] if self.hideable else []))


def _lower_first(name: str) -> str:
    return name[0].lower() + name[1:]


def _order(f: FieldDoc) -> tuple[int, str]:
    if f.name == "id":
        return (0, "")
    if f.name in _META_FIELDS:
        return (3, str(_META_FIELDS.index(f.name)))
    return (2 if f.kind == "entity" else 1, "")


def _entity_names() -> set[str]:
    return {cls.__name__ for cls in t.TYPES.values()}


def _describe(gql: GraphQLSchema, name: str, entities: set[str]) -> EntityDoc:
    typ = gql.type_map[name]
    if not isinstance(typ, GraphQLObjectType):
        raise TypeError(f"{name} is not an object type")
    docs = []
    for fname, f in typ.fields.items():
        named = get_named_type(f.type)
        if named.name in entities:
            kind = "entity"
        elif is_object_type(named):
            kind = "value"
        elif is_enum_type(named):
            kind = "enum"
        else:
            kind = "scalar"
        docs.append(FieldDoc(fname, str(f.type), f.description or "", kind))
    stable = sorted(enumerate(docs), key=lambda p: (_order(p[1]), p[0]))
    return EntityDoc(name, typ.description or "", [d for _, d in stable])


@cache
def context() -> dict[str, Any]:
    """Everything the templates may use; derived from the running code."""
    gql = build_schema(schema.as_str())
    query_fields = set(gql.query_type.fields) if gql.query_type else set()
    mutation_fields = set(gql.mutation_type.fields) if gql.mutation_type else set()
    entity_names = _entity_names()

    specs = {t.TYPES[spec.model].__name__: spec for spec in SPECS}
    entities: dict[str, EntityDoc] = {}
    for _title, type_names in SECTIONS:
        for name in type_names:
            doc = _describe(gql, name, entity_names)
            spec = specs.get(name)
            list_query = by_id = None
            muts: list[str] = []
            if spec is not None:
                list_query = _lower_first(spec.plural)
                by_id = "reviewItem" if spec.collection == "reviews" else _lower_first(spec.entity)
                muts = [
                    f"{verb}{spec.entity}"
                    for verb in ("create", "update", "delete", "restore", "move")
                ] + [f"reorder{spec.plural}"]
            if name == "Business":
                list_query = "business"
            muts += _EXTRA_MUTATIONS.get(name, [])
            entities[name] = EntityDoc(
                name=doc.name,
                description=doc.description,
                fields=doc.fields,
                list_query=list_query if list_query in query_fields else None,
                by_id_query=by_id if by_id in query_fields else None,
                mutations=sorted({m for m in muts if m in mutation_fields}, key=muts.index),
            )

    enums = [
        {
            "name": typ.name,
            "description": typ.description or "",
            "values": list(typ.values),
        }
        for typ in sorted(gql.type_map.values(), key=lambda x: x.name)
        if isinstance(typ, GraphQLEnumType) and not typ.name.startswith("__")
    ]
    error_codes = {
        "NOT_FOUND": _first_line(queries.NotFound.__doc__),
        **{
            cls.code: _first_line(cls.__doc__)
            for cls in (
                mutations.Invalid,
                mutations.InUse,
                mutations.Conflict,
                mutations.StaleOrder,
            )
        },
        "READ_ONLY": "A mutation named a snapshot; snapshots are read-only.",
    }
    return {
        "tools": {
            "query": names.TOOL_QUERY,
            "mutate": names.TOOL_MUTATE,
            "schema": names.TOOL_SCHEMA,
        },
        "schema_uri": names.SCHEMA_URI,
        "sections": [(title, [entities[n] for n in type_names]) for title, type_names in SECTIONS],
        "entities": entities,
        "collections": [e for e in entities.values() if e.list_query and e.name != "Business"],
        "value_types": [_describe(gql, n, entity_names) for n in VALUE_TYPES],
        "version_types": [_describe(gql, n, entity_names) for n in VERSION_TYPES],
        "enums": enums,
        "error_codes": error_codes,
        "prompts": PROMPTS,
    }


def _first_line(doc: str | None) -> str:
    return (doc or "").strip().splitlines()[0] if doc else ""


def _cell(value: Any) -> str:
    """Make text safe for a Markdown table cell."""
    return re.sub(r"\s+", " ", str(value)).replace("|", "\\|").strip()


@cache
def environment() -> Environment:
    env = Environment(
        loader=PackageLoader("intelliw.mcp", "prompts"),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["cell"] = _cell
    env.filters["tick"] = lambda value: f"`{value}`"
    return env


def select_area(area: str) -> list[EntityDoc]:
    """Entities matching an area name (a collection, entity or section), e.g. `staff`."""
    key = area.strip().lower().replace(" ", "")
    if not key:
        return []
    found = []
    for title, members in context()["sections"]:
        section_hit = key in title.lower().replace(" ", "")
        for e in members:
            names_ = {e.name.lower(), (e.list_query or "").lower()}
            if section_hit or any(key in n or n.startswith(key.rstrip("s")) for n in names_ if n):
                found.append(e)
    return found


def render(name: str, **arguments: Any) -> str:
    """Render prompt `name` (or `instructions`) with the code-derived context."""
    return environment().get_template(f"{name}.md.j2").render(**context(), **arguments)
