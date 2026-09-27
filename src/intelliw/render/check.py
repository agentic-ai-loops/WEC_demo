"""The design check (docs/design/04-design.md, *Design check*).

Runs before every render and on its own; reports every problem, each with its file.
"""

from pathlib import Path

from graphql import (
    ArgumentNode,
    DocumentNode,
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    GraphQLError,
    GraphQLList,
    GraphQLNonNull,
    GraphQLSchema,
    InlineFragmentNode,
    OperationDefinitionNode,
    OperationType,
    SelectionSetNode,
    TypeInfo,
    TypeInfoVisitor,
    Visitor,
    get_named_type,
    parse,
    validate,
    visit,
)
from jinja2 import TemplateSyntaxError
from pydantic import ValidationError

from intelliw.graphql.schema import schema
from intelliw.render.config import load_config
from intelliw.render.env import environment
from intelliw.render.errors import Problem
from intelliw.render.layout import (
    CONFIG_FILE,
    PAGE_QUERY,
    PAGE_TEMPLATE,
    PARAMS_QUERY,
    QUERY_FILES,
    RESERVED,
    SITE_QUERY,
    TEMPLATE_SUFFIX,
    list_designs,
    relative,
    segment_name,
)
from intelliw.workspace import Workspace

# Arguments the renderer owns (render options), never written into a design.
RENDERER_ARGUMENTS = ("includeHidden", "version", "snapshot")
# Fields every Asset selection needs: to publish the image and to link it.
ASSET_FIELDS = ("__typename", "id", "path")


def gql_schema() -> GraphQLSchema:
    return schema._schema


def check_workspace(ws: Workspace) -> dict[str, list[Problem]]:
    """The design check of every design in a workspace, by design name."""
    return {name: check_design(ws.design(name)) for name in list_designs(ws)}


def check_design(design_dir: Path) -> list[Problem]:
    problems: list[Problem] = []
    _check_config(design_dir, problems)
    _check_templates(design_dir, problems)
    _check_folder(design_dir, design_dir, [], problems)
    return problems


# ---- files ---------------------------------------------------------------------------


def _check_config(design_dir: Path, problems: list[Problem]) -> None:
    try:
        load_config(design_dir)
    except (ValidationError, ValueError) as exc:
        problems.append(Problem(CONFIG_FILE, f"invalid: {exc}"))


def _check_templates(design_dir: Path, problems: list[Problem]) -> None:
    env = environment(design_dir)
    for path in sorted(design_dir.rglob(f"*{TEMPLATE_SUFFIX}")):
        rel = relative(design_dir, path)
        if any(part.startswith(".") for part in Path(rel).parts):
            continue
        try:
            env.parse(path.read_text(encoding="utf-8"), name=rel)
        except TemplateSyntaxError as exc:
            problems.append(Problem(rel, exc.message or "syntax error", exc.lineno))
        except UnicodeDecodeError:
            problems.append(Problem(rel, "not UTF-8 text"))


def _check_folder(
    design_dir: Path, folder: Path, segments: list[str], problems: list[Problem]
) -> None:
    top = folder == design_dir
    names = {p.name for p in folder.iterdir()}
    own_segment = segment_name(folder.name) if not top else None

    for path in sorted(folder.iterdir()):
        name = path.name
        rel = relative(design_dir, path)
        if name.startswith("."):
            continue
        if top and name in RESERVED:
            problems.append(Problem(rel, "reserved name in the output (resources/, render.json)"))
        if name.startswith("_"):
            continue
        if path.is_dir():
            if name.startswith("["):
                seg = segment_name(name)
                if seg is None:
                    problems.append(
                        Problem(rel, "bad segment name: use [name], name like a GraphQL variable")
                    )
                    continue
                if seg in segments:
                    problems.append(Problem(rel, f"segment [{seg}] repeats an enclosing segment"))
                    continue
                if not (path / PARAMS_QUERY).is_file():
                    problems.append(Problem(rel, f"dynamic segment without {PARAMS_QUERY}"))
                _check_folder(design_dir, path, [*segments, seg], problems)
            else:
                _check_folder(design_dir, path, segments, problems)
            continue
        if not name.endswith(".gql"):
            continue
        if name not in QUERY_FILES:
            problems.append(
                Problem(rel, f"unknown query file; queries are {', '.join(QUERY_FILES)}")
            )
            continue
        if name == PAGE_QUERY and PAGE_TEMPLATE not in names:
            problems.append(Problem(rel, f"{PAGE_QUERY} without a sibling {PAGE_TEMPLATE}"))
        if name == PARAMS_QUERY and own_segment is None:
            problems.append(Problem(rel, f"{PARAMS_QUERY} outside a [name] folder"))
            continue
        if name == SITE_QUERY and not top:
            problems.append(Problem(rel, f"{SITE_QUERY} is only allowed at the top level"))
            continue
        variables = [] if name == SITE_QUERY else segments
        problems.extend(
            check_query(
                rel,
                path.read_text(encoding="utf-8"),
                variables=variables,
                params_key=own_segment if name == PARAMS_QUERY else None,
            )
        )


# ---- queries -------------------------------------------------------------------------


def check_query(
    file: str, source: str, *, variables: list[str], params_key: str | None = None
) -> list[Problem]:
    """Problems with one design query; `params_key` is set for `params.gql`."""
    try:
        doc = parse(source)
    except GraphQLError as exc:
        return [Problem(file, f"syntax error: {exc.message}", _line(exc))]

    ops = [d for d in doc.definitions if isinstance(d, OperationDefinitionNode)]
    if len(ops) != 1 or ops[0].operation != OperationType.QUERY:
        return [Problem(file, "must contain exactly one query operation")]
    op = ops[0]

    problems = [Problem(file, e.message, _line(e)) for e in validate(gql_schema(), doc)]
    if problems:
        return problems

    for var in op.variable_definitions or ():
        name = var.variable.name.value
        if name not in variables:
            allowed = ", ".join(f"${v}" for v in variables) or "none"
            problems.append(
                Problem(file, f"variable ${name} is not a segment value (allowed: {allowed})")
            )
    problems.extend(_renderer_arguments(file, doc))
    problems.extend(_asset_selections(file, doc))
    if params_key is not None:
        problems.extend(_params_shape(file, op, params_key))
    return problems


def _line(exc: GraphQLError) -> int | None:
    return exc.locations[0].line if exc.locations else None


def _renderer_arguments(file: str, doc: DocumentNode) -> list[Problem]:
    found: list[Problem] = []

    class Args(Visitor):
        def enter_argument(self, node: ArgumentNode, *_: object) -> None:
            if node.name.value in RENDERER_ARGUMENTS:
                line = node.loc.source.get_location(node.loc.start).line if node.loc else None
                found.append(
                    Problem(
                        file,
                        f"argument {node.name.value} is set by the render options, "
                        "not by the design",
                        line,
                    )
                )

    visit(doc, Args())
    return found


def _response_names(
    selection_set: SelectionSetNode | None, fragments: dict[str, FragmentDefinitionNode]
) -> set[str]:
    """Unaliased field names selected, following inline fragments and fragment spreads."""
    names: set[str] = set()
    if selection_set is None:
        return names
    for sel in selection_set.selections:
        if isinstance(sel, FieldNode):
            if sel.alias is None or sel.alias.value == sel.name.value:
                names.add(sel.name.value)
        elif isinstance(sel, InlineFragmentNode):
            names |= _response_names(sel.selection_set, fragments)
        elif isinstance(sel, FragmentSpreadNode) and sel.name.value in fragments:
            names |= _response_names(fragments[sel.name.value].selection_set, fragments)
    return names


def _asset_selections(file: str, doc: DocumentNode) -> list[Problem]:
    """Every selection on an Asset includes `__typename id path`."""
    fragments = {d.name.value: d for d in doc.definitions if isinstance(d, FragmentDefinitionNode)}
    type_info = TypeInfo(gql_schema())
    found: list[Problem] = []

    def report(node: FieldNode | InlineFragmentNode, names: set[str], what: str) -> None:
        missing = [f for f in ASSET_FIELDS if f not in names]
        if missing:
            line = node.loc.source.get_location(node.loc.start).line if node.loc else None
            found.append(
                Problem(
                    file,
                    f"{what} must select {' '.join(ASSET_FIELDS)} (missing: {' '.join(missing)})",
                    line,
                )
            )

    class Assets(Visitor):
        def enter_field(self, node: FieldNode, *_: object) -> None:
            field_type = type_info.get_type()
            if node.selection_set is None or field_type is None:
                return
            if get_named_type(field_type).name == "Asset":
                report(node, _response_names(node.selection_set, fragments), node.name.value)

        def enter_inline_fragment(self, node: InlineFragmentNode, *_: object) -> None:
            # `... on Asset` inside a union (e.g. ReviewTarget.entity)
            parent = type_info.get_parent_type()
            if node.type_condition is None or node.type_condition.name.value != "Asset":
                return
            if parent is not None and parent.name == "Asset":
                return
            report(node, _response_names(node.selection_set, fragments), "... on Asset")

    for definition in doc.definitions:
        if isinstance(definition, OperationDefinitionNode):
            visit(definition, TypeInfoVisitor(type_info, Assets()))
    return found


def _params_shape(file: str, op: OperationDefinitionNode, key: str) -> list[Problem]:
    """`params.gql`: one root list field whose items have the segment name as a key."""
    roots = op.selection_set.selections
    if len(roots) != 1 or not isinstance(roots[0], FieldNode):
        return [Problem(file, "params.gql must select exactly one root field (a list)")]
    root = roots[0]
    query_type = gql_schema().query_type
    field = query_type.fields.get(root.name.value) if query_type is not None else None
    field_type = field.type if field is not None else None
    if isinstance(field_type, GraphQLNonNull):
        field_type = field_type.of_type
    if not isinstance(field_type, GraphQLList):
        return [Problem(file, f"params.gql root field {root.name.value} is not a list")]
    keys = set()
    for sel in root.selection_set.selections if root.selection_set else ():
        if isinstance(sel, FieldNode):
            keys.add(sel.alias.value if sel.alias else sel.name.value)
    if key not in keys:
        return [
            Problem(
                file,
                f"params.gql items must have the key {key!r}, e.g. "
                f"{root.name.value} {{ {key}: id }}",
            )
        ]
    return []
