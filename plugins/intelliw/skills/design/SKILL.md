---
name: design
description: >-
  Generate an intelliw #design bundle (Jinja2 templates + GraphQL queries + static files)
  in a workspace from one or more design documents, then check and render it with
  `uv run render`. Use this whenever the user wants a new site design, template, theme,
  page layout or static site for an intelliw workspace (e.g. `examples/whitby_eye_care`),
  asks to turn a design brief / mock-up description / notes file into a design, or wants
  to add pages or routes to an existing design — even if they don't say "bundle".
argument-hint: <workspace> <design-doc> [more design docs…]
---

# /intelliw:design — build a #design bundle from design documents

Arguments: `$ARGUMENTS`

- The **first** argument is the workspace: a directory path, or a name resolved as
  `examples/<name>` in the intelliw repository (e.g. `whitby_eye_care`).
- **Every further** argument is a design document (Markdown, text, notes, a mock-up
  description…) to read in full. Together they say what the site should look like and
  contain.

If the workspace is missing or has no `businessdata/business.db`, or no design document
was given, ask for it before doing anything else.

The result is a new folder `<workspace>/design/<name>/` that passes the design check and
renders with `uv run render <workspace> --design <name>`. The spec below is the contract
the renderer (`src/intelliw/render/`) enforces — it is the same as
`docs/design/04-design.md`, which wins if the two ever disagree.

---

## Workflow

### 1. Understand the request

Read every design document completely. Extract: the pages and their URLs, what data each
page shows, layout/visual style, required behaviours (filters, menus, mobile), and
anything the docs say must *not* happen. When a document is silent, prefer the simplest
thing that works; when it is ambiguous in a way that changes the structure (e.g. one page
vs. one page per entity), ask.

Pick the design name: kebab-case from the document's title or file name, matching
`[a-z0-9][a-z0-9-]*`. List existing designs (`ls <workspace>/design`). If
`design/<name>/` already exists, ask before changing it — it is someone's work.

### 2. Learn the data

The schema is the source of truth for field names; don't guess them.

```bash
# the SDL, straight from the code (no server needed)
uv run python -c "from intelliw.graphql.schema import schema; print(schema.as_str())"
```

Look at real values before designing around them (lengths of texts, which references are
null, how many records, hidden ones). Run queries in-process against the workspace
database, in the same view the renderer uses:

```bash
uv run python - <<'EOF'
import asyncio, json
from pathlib import Path
from intelliw.businessdata.database import create_db_engine, session_factory
from intelliw.graphql.context import Context, View
from intelliw.graphql.schema import schema
from intelliw.workspace import Workspace

ws = Workspace(Path("examples/whitby_eye_care"))    # the workspace argument
query = "{ services { id name summary image { __typename id path } } }"
engine = create_db_engine(ws.database_file)
ctx = Context(session_factory(engine), ws.resources_dir, default_view=View(0, False))
r = asyncio.run(schema.execute(query, context_value=ctx))
print(json.dumps(r.data, indent=2)[:4000], r.errors)
EOF
```

(`uv run client-cli graphql --gql '…'` does the same through the running server, but a
long-running server can serve a stale database after a reimport; the in-process way
always reads the file.)

### 3. Plan the bundle

Map each page in the documents to a folder (routing below). Decide for each page which
`page.gql` it needs, and what every page needs (`site.gql`: business name, logo, nav
data). Prefer a shared layout (`_layouts/base.html.j2`) and partials over repetition.
Write down the tree before writing files.

### 4. Write the files

Create the folder and files per the spec. Keep business values out of the design: every
name, phone number, hour or text comes from a query (principle 1). Design-owned things —
wording of headings, colours, a decorative hero image — belong in the design.

### 5. Check, render, fix — until clean

```bash
uv run render <workspace> --design <name> --dryrun          # check + render, writes nothing
uv run render <workspace> --design <name> --dryrun --include-hidden
```

`--dryrun` runs the design check, every query and every template, and the link check,
without writing. Exit code 1 lists problems as `file:line: message`; fix them all and
rerun. When clean, render for real (`uv run render <workspace> --design <name>`) — output
goes to `<workspace>/outputs/active/` (snapshots: `--version N` / `--snapshot TAG` →
`outputs/N[_tag]/`).

### 6. Look at it

Serve the output from a sub-folder (this proves relative links work) and view it — in a
browser tool if you have one, at desktop and phone widths if the documents care:

```bash
uv run python -m http.server 8130 -d <workspace>/outputs    # then open /active/
# or live reload while iterating:  uv run livereload <workspace>/outputs/active --port 8130
```

Check that images load, links resolve, and nothing overflows horizontally. Stop any
server you started.

### 7. Report

Tell the user: the design name and folder, the page tree (routes → what they show), how
to render it, what you verified, and any deviations from the documents or open choices.

---

## The #design bundle spec

### Principles

1. **No business data values in a design.** Names, phones, hours, bios come from queries.
   A design may refer to the schema (fields, types, ids used for routing) and carry its
   own wording and images.
2. **One source of data**: templates see only what their queries return, through the
   GraphQL API with its rules (deleted entities never, hidden ones only when rendered with
   `--include-hidden`, lists in the owner's `position` order).
3. **The files are the routes**: every output file comes from a design file; no manifest.
4. **Every link is relative**, so the site works in any sub-folder of any web server.
5. **The renderer decides what data is shown** (version, hidden); **the design decides
   how**.

### Location

`<workspace>/design/<name>/` is the design `<name>`. Designs are self-contained: templates
load only from their own folder (a template or file symlinked from outside is refused),
nothing is shared between designs. Entries in `design/` starting with `_` or `.` are not
designs.

### Directory structure (Next.js-style routing)

```
design/<name>/
  _config.json            # optional metadata/options (DesignConfig)
  _layouts/base.html.j2   # layouts, not rendered on their own
  _partials/nav.html.j2   # fragments for {% include %} / {% import %}
  site.gql                # top level only: data every page needs → `site`
  page.html.j2            # the page at / → index.html, data from page.gql
  page.gql
  404.html.j2             # → 404.html (any other *.j2 → the name without .j2)
  style.css.j2            # → style.css
  hero.jpg                # copied as is (design image)
  services/
    page.html.j2          # /services/
    page.gql
    [slug]/               # dynamic segment `slug`: one page per value
      params.gql          # lists the values
      page.gql            # uses $slug
      page.html.j2        # /services/<slug>/
```

| Entry | Rule |
| --- | --- |
| `page.html.j2` | the folder's page → `index.html`, with `data` from the sibling `page.gql` |
| `page.gql` | only next to a `page.html.j2` |
| `params.gql` | required in every `[name]` folder, allowed nowhere else |
| `site.gql` | top level only; its result is `site` in every template |
| other `*.j2` | rendered to the name without `.j2` |
| other files | copied unchanged (in a `[name]` folder: once per value) |
| `[name]/` | dynamic segment, `name` a GraphQL variable name (`[A-Za-z_][A-Za-z0-9_]*`), distinct from enclosing segment names |
| `_*` | never rendered or copied; usable by templates and the renderer |
| `.*` | ignored |
| top-level `resources`, `render.json`, `render.json.j2` | reserved (business images, render metadata) — an error |
| any other `*.gql` | an error (only `site.gql`, `page.gql`, `params.gql`) |

Two outputs landing on the same path (e.g. a value `about` from `[slug]` next to a static
`about/` folder) is an error.

### Queries

Every `.gql` file is exactly **one `query` operation** (fragments allowed), validated
against the schema.

- **Variables**: `page.gql` / `params.gql` may declare the enclosing segment values
  (`query Service($slug: ID!)`), nothing else. `site.gql` has none. Only declared
  variables are passed.
- **Forbidden arguments**: `includeHidden`, `version`, `snapshot` — the render options
  set them for every field.
- **Assets**: every selection of an `Asset` — anywhere, including `params.gql` and
  fragments — must include `__typename id path`. The renderer publishes exactly the assets
  it sees (objects with `__typename: "Asset"` and an `id`) to `resources/<path>`, and
  `asset_url()` needs `path`. Aliasing any of those three fields doesn't count.
- **`params.gql`**: one root field of list type; each item has the segment name as a key,
  usually via an alias; values must be path-safe (`[A-Za-z0-9][A-Za-z0-9._-]*` — entity
  ids already are); duplicates fail; an empty list renders no pages.

```graphql
# services/[slug]/params.gql
query { services { slug: id } }

# services/[slug]/page.gql
query Service($slug: ID!) {
  service(id: $slug) {
    name summary description
    category { name }
    image { __typename id path alt }
    faqs { question answer }
    actions { label href }
  }
}
```

- A GraphQL error at render time fails the render. Nested segments see enclosing values
  (`categories/[category]/[slug]/params.gql` can use `$category`).
- Useful query facts: list fields are `contactPoints locations serviceCategories services
  productCategories staff faqs socialLinks affiliations actions assets reviews`; by-id
  fields are the singular (`service(id:)`, `staffMember(id:)`, `customerAction(id:)`,
  `reviewItem(id:)`, …); `business` is a singleton; `reviews` defaults to
  `status: open` (pass `status: null` for all); `ReviewTarget.entity` is a union — select
  with `... on Type { … }`.

### Hidden entities

By default hidden records are absent: not in lists (so no page from `params.gql`),
optional references to them are `null`. Required references (`Service.category`,
`CustomerAction.channel`) still resolve — check their `hidden` field if it matters.
Hiding doesn't cascade: `services` lists a visible service in a hidden category; nest
under `serviceCategories { services { … } }` to drop those. With `--include-hidden`,
everything appears with `hidden: true`.

### Template contract

Jinja2, loader rooted at the design folder (`{% extends "_layouts/base.html.j2" %}` works
from any depth). Context of every template:

| Variable | Value |
| --- | --- |
| `config` | `_config.json` as a dict (`title`, `description`, `options`) |
| `site` | the `site.gql` result (`{}` without one) |
| `params` | segment values, e.g. `{"slug": "eye-exams"}` |
| `data` | `page.html.j2` only: the sibling `page.gql` result (`{}` without one) |
| `path` | this output file's path from the site root, e.g. `services/eye-exams/index.html` |
| `render` | the render metadata (same as `render.json`, below) |

Helpers:

- `url(p)` — relative link from the current file to site path `p` (no leading `/`; end
  with `/` for a folder's page): from `services/eye-exams/index.html`, `url("")` →
  `../../`, `url("services/")` → `../`, `url("style.css")` → `../../style.css`.
- `asset_url(a)` — relative link to a business image (`a` selected with `path`).

Link rules (the link check fails the render otherwise): every link to the site's own
files goes through `url()`/`asset_url()` — in `href`, `src`, `srcset`, `action` and CSS
`url()`; root-relative links (`/x`) are errors; relative links must point to files that
exist in the output. External links (`https:`, `tel:`, `mailto:`, business `href`s) are
not checked. Scripts: pass links in from the template (`data-render="{{
url('render.json') }}"`), since JS resolves against the page URL.

Values reach templates as plain dicts/lists with GraphQL field names
(`data.service.faqs[0].question`). Autoescape is on for `.html.j2`, off for others.

### `_config.json`

```json
{ "title": "Clinic, light", "description": "…", "options": { "accent": "#1f6f8b" } }
```

All optional; unknown keys are an error. `options` is free-form for design choices
(colours, switches) — never business data.

### Render metadata (`render.json`, and `render` in templates)

```json
{
  "rendered_at": "…Z", "generator": "intelliw 0.1.0",
  "design": { "name": "clinic", "title": "Clinic, light" },
  "businessdata": { "version_name": "active", "version": null, "snapshot": null,
                    "based_on": { "number": 1, "tag": null, "created_at": "…" },
                    "modified": false, "updated_at": "…" },
  "include_hidden": false
}
```

Use it for "last updated", or a preview banner when `render.include_hidden` or
`render.businessdata.version` is set.

### The design check (runs before every render)

Reports all of: misplaced `page.gql` / `params.gql` / `site.gql`, `[name]` folders
without `params.gql`, bad or repeated segment names, reserved names, invalid
`_config.json`, template syntax errors, and per `.gql`: parse errors, not exactly one
query, schema validation errors, undeclared/unknown variables, forbidden arguments, Asset
selections missing `__typename id path`, and a bad `params.gql` shape. Runtime errors
(bad params values, collisions, template errors, missing asset files, broken links) are
reported by the render itself.

---

## Authoring notes (things that bite)

- **StrictUndefined**: a missing key is an error, not an empty string. For fields a
  record may lack, use `row.get('x')`, `x is defined`, or `x or ''`. Optional references
  can be `null` — guard `{% if data.service.image %}`.
- **Immutable sandbox**: lists and dicts can't be mutated (`append`, `update`, `{% set
  d.x = … %}`). Build with filters (`selectattr`, `map`, `groupby`, `sort`, `join`) and
  use `loop.previtem` / `loop.nextitem` / `loop.index` for neighbours and counters.
- **Macro files**: import with context so helpers and `site`/`path` work inside them:
  `{% import "_partials/ui.html.j2" as ui with context %}`. Macros return strings; use
  `| trim` before testing them.
- **Child templates**: put `{% set %}` and `{% import %}` inside the `{% block %}` that
  uses them — top-level sets in a child are not visible in its blocks.
- **Links to entity pages**: an entity only has a page if `params.gql` listed it (hidden
  ones only with `--include-hidden`). If templates link to entity pages from references,
  fetch an index of existing ids in `site.gql` and link only when the id is listed —
  otherwise the link check fails on a hidden entity's reference.
- **Texts**: `description`-style fields are Markdown source; there is no markdown filter,
  so render them as text (`white-space: pre-line`) unless the docs say otherwise.
- **Images**: business images come only through queries (`asset_url`); a decorative image
  the documents ask for goes in the design folder and is linked with `url()`.
- **CSS `url()` in custom properties** resolves against the stylesheet that *uses* the
  variable, not the page that set it — `style="--bg: url(...)"` consumed by
  `assets/site.css` requests `assets/assets/...` (the link check can't see this). Use a
  real `<img>` (e.g. an absolutely positioned hero image) or set `background-image`
  inline.
- **Adding markup to data**: in `.html.j2`, `v | e` is a `Markup` string and its
  `replace()` escapes the replacement too — `(v | e) | replace("@", "@<wbr>")` prints a
  literal `&lt;wbr&gt;`. Split the value and write the tag as template text instead
  (`{% for p in v.split("@") %}{{ p }}{% if not loop.last %}@<wbr>{% endif %}{% endfor %}`),
  and grep the output for `&lt;` to catch this.
- **JSON for scripts**: `| tojson` escapes `<>&'` but not `"` — put it in a
  single-quoted attribute (`data-x='{{ v | tojson }}'`) or a
  `<script type="application/json">` block.

## Examples in the repository

- `examples/whitby_eye_care/design/clinic/` — minimal public site: home, one page per
  service, 404, stylesheet template, a script reading `render.json`.
- `examples/whitby_eye_care/design/clinic-pro/` — a full public site (shared layout,
  vendored front-end libraries, theme options in `_config.json`, contacts/actions
  selected by id in `site.gql`, a page per service and staff member).
- `examples/whitby_eye_care/design/inspector/` — generic developer view: one page with
  every field of every collection, an `[id]` route per collection, a site-wide entity
  index in `site.gql`, generic macros in `_partials/values.html.j2`.

They are examples, not rules: every design chooses its own look, libraries, layout and
behaviour. Only the spec above is enforced.
