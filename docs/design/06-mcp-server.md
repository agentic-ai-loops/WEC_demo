# MCP server

**Status:** implemented (#businessdata); #design and #site tools to come
**Depends on:** 00-architecture, 02-graphql-api

## Goal

Expose #businessdata to #owner agents over MCP — the only way owners reach the service
(AGENT.md) — so an agent can read and change the business data safely.

## Scope

- In scope:
  - MCP access to the #businessdata GraphQL API (02-graphql-api).
  - The guidance given to agents (server instructions).
  - Prompts: a reference to the business data, and task-oriented prompts.
- Out of scope (later):
  - Tools for #design (slot bindings, templates) and #site (render, preview, deploy).
  - Authentication (one #owner per workspace).

## Specification

### Surface

The server is a thin gateway to the GraphQL API, not one tool per query or mutation:

| MCP surface | Purpose |
| --- | --- |
| tool `graphql_query(document, variables?)` | runs a GraphQL query; annotated read-only; rejects documents containing a mutation |
| tool `graphql_mutate(document, variables?)` | runs a GraphQL mutation; annotated destructive, not idempotent; rejects documents that are not mutations |
| tool `graphql_schema()` | the GraphQL SDL, for clients that do not read resources |
| resource `graphql://schema` (`text/graphql`) | the same SDL |
| server instructions | conventions an agent cannot infer from the schema (below) |
| prompts | a business-data reference and task-oriented prompts (*Prompts*) |

Rationale:

- **Not one tool per operation.** The API has 30 query fields and 79 mutations; mirroring
  them would flood the agent's context, hurt tool selection, and duplicate the schema in
  a second place that must track every change. GraphQL already lets the agent fetch
  exactly the nested data it needs in one call, and agents write GraphQL well given the
  SDL.
- **Not one undifferentiated tool.** MCP clients decide what needs the owner's approval
  from tool annotations. Splitting reads from writes lets reads run freely while writes
  stay behind confirmation. The split is enforced by the server: the document is parsed
  (graphql-core) and the operation type checked before anything runs, so a query call
  cannot carry a mutation.
- **Variables as a separate argument**, so agents do not splice values into documents.

### Behaviour

- The document is parsed first; a syntax error, or the wrong operation type for the tool,
  is an error result and nothing is executed.
- The tool result is the GraphQL response as JSON text (`{"data": ..., "errors": [...]}`).
  If the response has errors, the result is marked as an error; each error keeps its
  `extensions.code` (02-graphql-api, *Errors*).
- Execution goes to the GraphQL server over HTTP (`GRAPHQL_HOST` / `GRAPHQL_PORT`); the
  executor is injectable, so tests run the schema in-process.
- The SDL is produced from the same code as the served API (`schema.as_str()`).

### Instructions

The server's instructions tell the agent to read the schema, use `graphql_query` /
`graphql_mutate`, and pass values in `variables`, and summarise the conventions:

- one editable active version and read-only snapshots; `version` / `snapshot` arguments;
  mutations change the active version;
- `takeSnapshot` before a batch of changes, `activateVersion` to undo (unsaved changes are
  kept in an automatic snapshot);
- patches: omitted = unchanged, `null` = clear;
- soft delete with `trash` / `restore<Entity>`; `hidden` keeps an entity off the site;
- review items are questions for the owner — ask, then resolve or dismiss;
- files are uploaded by the owner in the web interface; `createAsset` registers them;
- the error codes.

The instructions are rendered from `prompts/instructions.md.j2`, built from the same
partials as the prompts.

### Prompts

Prompts are Jinja2 templates in `src/intelliw/mcp/prompts/`, rendered on request with a
context derived from the code (`intelliw.mcp.prompts.context`):

- entity and field names, GraphQL types and descriptions come from the GraphQL schema,
  whose descriptions come from the Pydantic models (docstrings and
  `Field(description=...)`) — one source for the models, the SDL and the prompts;
- query and mutation names come from the GraphQL schema's collection table (`SPECS`);
- enum values and error codes (with their meanings, from the error classes' docstrings)
  come from the code;
- tool and resource names come from `intelliw.mcp.names`.

Shared partials keep the wording identical: `_discovery` (schema tool and resource,
query vs. mutate tools, `variables`), `_conventions` (versions, patches, soft delete,
hidden, review items, uploads, order, error codes) and `_formatting` (below).

| Prompt | Argument | Purpose |
| --- | --- | --- |
| `business_schema` | — | reference: how the entities fit together, one table per entity (field, type, meaning) with its query and mutation names, nested values, snapshots, enums |
| `business_overview` | — | keep an eye on things: one dashboard query (generated to cover every collection), then counts, open questions, recent changes, version status |
| `explore_business` | `area` (optional) | explore everything, or one area (e.g. `staff`, `services`), with generated queries selecting each entity's own fields |
| `find_information` | `question` | answer a specific question with a targeted query, then show the data behind it |
| `review_concerns` | — | open review items, missing photos and images, unused assets, unregistered files, hidden and trashed items, unsaved changes; fix one at a time |
| `update_business` | `request` | confirm, snapshot, change via `graphql_mutate`, verify before/after, offer rollback; table of mutations per entity |

Formatting guideline (every prompt): business data as tables with an `id` column, small
two-column tables for single records and status, plain language, and every answer ends
with **Next steps** (two to four options) and one **Recommended:** step.

### Future tools

Task-level tools are added where they do something GraphQL cannot — deploying the #site,
binding #design image slots, rendering a preview — not as wrappers around single queries.

## Acceptance criteria

- [x] The server lists exactly `graphql_query`, `graphql_mutate` and `graphql_schema`,
      with read-only annotations on the first and last and destructive on
      `graphql_mutate`.
- [x] `graphql_query` returns query results; `variables` are passed to the API.
- [x] `graphql_query` rejects a mutation without executing it; `graphql_mutate` rejects a
      query.
- [x] A GraphQL error response is an error result whose JSON keeps `extensions.code`.
- [x] A syntax error is an error result.
- [x] The `graphql://schema` resource and `graphql_schema` tool return the same SDL.
- [x] The instructions mention the tools, `variables`, snapshots and the error codes.
- [x] All six prompts are listed with title and description and render with their
      arguments.
- [x] `business_schema` covers every entity type and every GraphQL field; every model
      description appears in the SDL and in the prompt.
- [x] Every GraphQL example in every rendered prompt validates against the schema; the
      overview and concerns queries run on the sample data.
- [x] Every prompt includes the formatting guideline and the discovery section.

## Implementation notes

`src/intelliw/mcp/server.py` (`create_server`, `http_executor`, prompt registration),
`src/intelliw/mcp/prompts.py` (context, `render`, `PROMPTS`), templates in
`src/intelliw/mcp/prompts/`, names in `src/intelliw/mcp/names.py`. Tests:
`tests/test_mcp.py` (tools, in-process against the in-memory sample database) and
`tests/test_mcp_prompts.py` (prompts, sync with the schema, validity of examples).
