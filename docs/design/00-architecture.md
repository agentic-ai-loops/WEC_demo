# Architecture overview

**Status:** draft
**Depends on:** AGENT.md

## Goal

Establish the module boundaries so later increments can be built independently.

## Specification

A *workspace* is a directory owned by one #owner:

```
<workspace>/
  businessdata/
    business.json     # #businessdata content, validated against the fixed schema
    resources/        # raw files (documents, images, ...)
  design/
    templates/        # jinja2 templates; `*.html.j2` become pages
    assets/           # static assets copied verbatim
  _site/              # generated #site (never edited directly)
```

Python package `intelliw`:

| Module                  | Responsibility                                             |
| ----------------------- | ---------------------------------------------------------- |
| `intelliw.workspace`    | Resolve paths within a workspace                           |
| `intelliw.businessdata` | Schema (pydantic) + load/save of #businessdata             |
| `intelliw.design`       | Discover templates/assets, build jinja2 environment        |
| `intelliw.site`         | Render #businessdata x #design into `_site/`               |
| `intelliw.graphql`      | Strawberry GraphQL API over #businessdata                  |
| `intelliw.mcp`          | MCP server; `query(gql)` forwards to the GraphQL API       |
| `intelliw.config`       | Settings from environment / `.env`                         |
| `intelliw.cli`          | `intelliw` (site), `svr` (servers), `client-cli` (testing) |

Request flow: #owner agent → MCP (`query` tool) → GraphQL → #businessdata.

## Acceptance criteria

- [x] `uv run intelliw build examples/demo` produces `examples/demo/_site/index.html`.
- [x] `uv run pytest` passes.
