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

The full text is `INSTRUCTIONS` in `intelliw/mcp/server.py`.

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

## Implementation notes

`src/intelliw/mcp/server.py` (`create_server`, `http_executor`, `INSTRUCTIONS`);
tests in `tests/test_mcp.py` run the MCP server in-process with an executor that runs the
GraphQL schema against the in-memory sample database.
