# #design templates and rendering

**Status:** implemented (the `render` CLI and `intelliw.render`)
**Depends on:** 00-architecture, 01-businessdata, 02-graphql-api

## Goal

Define what a #design is, a named directory of templates, queries and static files, and
how the #site is rendered from #businessdata with a chosen design. A workspace can hold
several designs. A design is written once and works for any business: it holds layout,
styling and design images, never business data values. All business data comes from
GraphQL queries at render time.

## Scope

- In scope:
  - where designs live and how they are named; choosing the design to render;
  - the design directory: file kinds, directory-based routing, dynamic segments;
  - the queries that feed templates (`site.gql`, `page.gql`, `params.gql`);
  - the template contract: context variables and helpers; relative links;
  - render options: the #businessdata version, and whether hidden entities are included;
  - `_config.json`;
  - rendering into `{run_dir}/sites/{workspace}/{design}/`: the walk, output paths, publishing
    business images, the published render metadata (`render.json`),
    atomic replacement, sandboxing;
  - the design check.
- Out of scope:
  - client-side only output: html, js, css and files; no server-side code, no build
    step (no bundlers or web-app frameworks);
  - versions of a design (one working tree per design);
  - sharing files between designs (each design is self-contained);
  - preview, verification and deployment (05-site);
  - MCP tools for editing designs and triggering renders (06-mcp-server, later).

## Specification

### Principles

1. **Designs contain no business data values.** Names, phone numbers, hours, bios and
   the like come from queries. A design may refer to the schema, meaning fields, types
   and ids used in routing. It may also carry fixed wording that belongs to the design
   (headings, button labels).
2. **One source of data.** Templates see only what their queries return; queries go
   through the GraphQL API (02-graphql-api) with its rules. Deleted entities are left
   out, hidden ones unless the render includes them, and ordering follows `position`.
3. **What the files say is what gets rendered.** Every output file comes from a file in
   the design, by the rules below. There is no route table or manifest.
4. **Every link is relative.** Links between the site's own files are relative to the
   file that contains them, so a rendered site works unchanged wherever it is placed:
   at a domain's root, in a sub-folder of another website (`example.com/clinic/`), or
   served from any path.
5. **The renderer decides what data is shown; the design decides how.** The version of
   #businessdata and whether hidden entities are included are render options, never
   written into a design.

### Designs in a workspace

A workspace holds any number of designs. `<workspace>/design/<name>/` is the design
bundle named `<name>`:

```
<workspace>/
  businessdata/
  design/
    clinic-light/         # design "clinic-light"
      _config.json
      page.html.j2
      ...
    clinic-dark/          # design "clinic-dark"
      ...
```

The rendered sites live outside the workspace, in the server's run directory:

```
{run_dir}/sites/            # run_dir: INTELLIW_RUN_DIR, default ./run
  whitby_eye_care/          # the workspace's folder name
    clinic-light/           # the site of design "clinic-light"
    clinic-dark/
```

- A design name matches `[a-z0-9][a-z0-9-]*`, and the folder name is the design's name.
  Entries in `design/` starting with `_` or `.` are not designs; they are ignored and
  reserved.
- Each design is self-contained. Templates load only from their own design folder, and
  nothing is shared or inherited between designs. To start a new design from an
  existing one, copy its folder.
- **The site is rendered with one design**, named on every render:
  `build(workspace, design, ...)` renders `design/<design>/` into that design's output
  folder (options in *Render options*). A name with no design folder is an error.
- This replaces the `templates/` + `assets/` split and `_site/` in 00-architecture.

### Output folders

A render writes to `{run_dir}/sites/{workspace}/{design}/`: one folder per design of each
workspace, so every design's site exists side by side.

- `run_dir` is the server's run directory (`INTELLIW_RUN_DIR`, default `./run`; the `render`
  CLI also takes `--run-dir`). `workspace` is the workspace folder's name.
- A render replaces its design's folder completely. Other designs' folders are untouched.
- The folder holds whatever was rendered last: normally the active version without hidden
  records (the automatic re-render after every mutation, below). A render of a snapshot
  (`--version` / `--snapshot`) or with `--include-hidden` goes to the same folder;
  `render.json` records which data and options it shows, and the next automatic
  re-render restores the active version.
- `<version_name>` (`active`, `3`, `4_Spring-2026`: the tag made safe for a folder name)
  names the rendered data in `render.json`.
- The *output folder* in the rest of this document is `{run_dir}/sites/{workspace}/{design}/`.

### Automatic re-rendering

Every committed GraphQL mutation (through `/graphql` or MCP's `graphql_mutate`) queues a
job on the re-render queue (rq on Redis, `intelliw.jobs`); a worker (`jobs worker`) runs
it and re-renders **every** design of the workspace from the active version.

- The job's argument is a `MutationEvent`: the GraphQL mutation's name and when it was
  committed (UTC).
- A failing design is reported in the job's result and doesn't stop the others.
- Queueing never fails a mutation: without Redis the change is kept and the missing
  re-render is logged. `/health` reports the queue as `jobs`.

In the rest of this document, the *design folder* is `design/<name>/`, and paths are
relative to it.

### Directory structure

Routing follows the Next.js convention of folders as URL paths. A design folder:

```
design/clinic-light/
  _config.json            # design metadata and options (DesignConfig)
  _layouts/base.html.j2   # layouts: Jinja2 templates, not rendered on their own
  _partials/nav.html.j2   # fragments for {% include %} / {% import %}
  site.gql                # data every page needs, run once
  page.html.j2            # the page at /  -> <output folder>/index.html
  page.gql                # its data
  404.html.j2             # -> 404.html
  style.css.j2            # -> style.css
  hero.jpg                # -> hero.jpg (a design image)
  services/
    page.html.j2          # /services/
    page.gql
    [slug]/               # dynamic segment named `slug`
      params.gql          # the list of `slug` values
      page.html.j2        # /services/<slug>/
      page.gql            # uses $slug
  staff/
    page.html.j2
    page.gql
```

| Entry | Rule |
| --- | --- |
| `page.html.j2` | the folder's page, rendered to `index.html` in the output folder, with `data` from the sibling `page.gql` |
| `page.gql` | query for the sibling `page.html.j2`; only allowed next to one |
| `params.gql` | query listing the values of a dynamic segment; required in every `[name]` folder, allowed nowhere else |
| `site.gql` | top level only; its result is `site` in every template |
| other `<file>.j2` | rendered to `<file>` (`style.css.j2` → `style.css`, `404.html.j2` → `404.html`) |
| other files | copied unchanged |
| `[name]/` | dynamic segment: rendered once per value from its `params.gql` |
| other folders | a static path segment |
| `_*` (files and folders) | not rendered, not copied; available to templates (`_layouts`, `_partials`) and to the renderer (`_config.json`) |
| `.*` (dotfiles) | ignored |

Names:

- A segment name in `[name]` is a GraphQL variable name (`[A-Za-z_][A-Za-z0-9_]*`). It
  must differ from the names of enclosing segments.
- Two top-level output names are reserved: `resources/` for business images (*Images*)
  and `render.json` for the render metadata (*Render metadata*). A top-level design file
  or folder named `resources`, `render.json` or `render.json.j2` is an error.
- Design images (a hero picture, a clinic photo used as decoration, icons) are ordinary
  files in the design. Business images (a staff photo, a service image) are #businessdata
  assets, reached through queries.

### Queries

Every `.gql` file holds one GraphQL `query` operation, run through the same schema as the
API (02-graphql-api), in the version and with the hidden-entity setting chosen for the
render (*Render options*).

- **Variables.** `page.gql` and `params.gql` receive the current segment values
  (`params`) as variables, for example `$slug`. An operation declares only the variables
  it uses (`query Service($slug: ID!)`); only declared variables are passed. `site.gql`
  has no variables.
- **Arguments the renderer owns.** `includeHidden`, `version` and `snapshot` are not
  allowed in design queries (principle 5). The render options supply them for every
  field, so one design renders any version, with or without hidden entities.
- **Errors.** A GraphQL error in any query fails the render.

`params.gql` returns one root list field. Each item has a key named after the segment,
given with an alias:

```graphql
# services/[slug]/params.gql
query { services { slug: id } }
```

```json
{ "services": [ { "slug": "eye-exams" }, { "slug": "dry-eye-testing" } ] }
```

- Every item must have the key as a non-empty string that is a safe path segment
  (`[A-Za-z0-9][A-Za-z0-9._-]*`); entity ids already are. Other keys are ignored.
- Duplicate values are an error. An empty list renders no pages for that folder; this is
  not an error.
- A nested segment's `params.gql` receives the enclosing values: in
  `categories/[category]/[slug]/params.gql`, `$category` is available.

The page query for a dynamic page:

```graphql
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

### Render options

```
build(workspace, design, *, version=None, snapshot=None, include_hidden=False)
```

| Option | Default | Effect |
| --- | --- | --- |
| `version` / `snapshot` | active version | the #businessdata version every query reads; at most one of the two (02-graphql-api, *Versions*) |
| `include_hidden` | `false` | whether queries return hidden entities, in lists, references and nested fields |

- The defaults give the public site: the active version, hidden entities left out.
- Other settings serve the owner: rendering a snapshot to see or restore an earlier
  site, or including hidden entities to preview records before showing them. Whether
  such a render may be deployed is decided in 05-site.
- Templates see the options, with the rest of the render metadata, as `render`
  (*Render metadata*). For example, a template can show a preview banner, or mark
  hidden records by their `hidden` field.
- A snapshot's assets refer to files in the shared `businessdata/resources/`. A file
  that has since been removed fails the render (*Images*).

### Hidden entities

With `include_hidden` off (the default), hidden entities never reach templates
(02-graphql-api, *Queries*):

- a hidden entity has no entry in lists, so `params.gql` gives it no page;
- optional references to it are `null`, so templates must allow `null` for every
  optional reference, as they already must for unset ones;
- hiding does not cascade: `services` still lists a visible service in a hidden
  category. A design that should drop these nests its lists under
  `serviceCategories { services { ... } }`;
- required references (`Service.category`, `CustomerAction.channel`) resolve even to a
  hidden entity. A template that shows one checks its `hidden` field.

With `include_hidden` on, hidden entities appear everywhere with `hidden: true`.

### Template contract

Templates are Jinja2, with full template features. The loader is rooted at the design
folder, so `{% extends "_layouts/base.html.j2" %}` and
`{% include "_partials/nav.html.j2" %}` work from any folder.

Context of every template:

| Variable | Value |
| --- | --- |
| `config` | the validated `_config.json` (`DesignConfig`) |
| `site` | the `site.gql` result (`data` of the response); `{}` without `site.gql` |
| `params` | the current segment values, e.g. `{"slug": "eye-exams"}`; `{}` outside dynamic folders |
| `data` | `page.html.j2` only: the sibling `page.gql` result; `{}` without `page.gql` |
| `path` | the output file's path from the site root, e.g. `services/eye-exams/index.html`, `style.css` |
| `render` | the render metadata, the same object that is published as `render.json` (*Render metadata*) |

Helpers:

| Helper | Returns |
| --- | --- |
| `url(p)` | relative URL from the current output file to the site path `p` (a path from the site root, without a leading `/`) |
| `asset_url(a)` | relative URL of a business image: `url("resources/" ~ a.path)` |

Relative links (principle 4):

- `url()` computes the link from the directory of `path` to `p`. From
  `services/eye-exams/index.html`: `url("")` → `../../`, `url("services/")` → `../`,
  `url("style.css")` → `../../style.css`. From `index.html`: `url("")` → `./`,
  `url("services/")` → `services/`.
- A path ending in `/` names a folder's page. The link ends in `/` too, and the web
  server serves the folder's `index.html`.
- Templates build every link to the site's own files with `url()` or `asset_url()`.
  This applies to html (`href`, `src`, `srcset`, `action`) and to CSS (`url(...)`;
  `style.css.j2` links relative to `style.css`). Plain relative links written by hand
  (`href="../"`) are allowed but fragile. Root-relative links (`/...`) are errors
  (*Link check*).
- Absolute URLs to other sites (`https:`, `mailto:`, `tel:`), including those in
  #businessdata such as `CustomerAction.href`, are left as they are.

- Query results reach templates as plain dicts and lists, with GraphQL field names
  (`data.service.faqs[0].question`, `site.business.name`).
- `StrictUndefined`: a misspelled variable or field is a render error, not an empty
  string.
- Autoescaping is on for `.html.j2`, off for other templates.

Example:

```jinja
{# services/[slug]/page.html.j2 #}
{% extends "_layouts/base.html.j2" %}
{% block title %}{{ data.service.name }} · {{ site.business.name }}{% endblock %}
{% block main %}
  <h1>{{ data.service.name }}</h1>
  {% if data.service.image %}
    <img src="{{ asset_url(data.service.image) }}" alt="{{ data.service.image.alt or '' }}">
  {% endif %}
  {% for a in data.service.actions %}
    <a class="button" href="{{ a.href }}">{{ a.label }}</a>
  {% endfor %}
{% endblock %}
```

### `_config.json`

Validated by a Pydantic model; optional (defaults apply):

```python
class DesignConfig(BaseModel):
    title: str = ""                # display name, e.g. "Clinic, light"; the folder name is the id
    description: str = ""
    options: dict[str, JsonValue] = {}   # design settings for templates: colours, layout switches
```

`options` is for design choices only (principle 1).

### Rendering

```
build(workspace, name, options):
  design = workspace/design/<name>               # error if missing
  out = new temporary directory                  # *Atomic*
  check(design)                                  # *Design check*; stops on any problem
  site = run(site.gql, {})                       # every run() uses the render options
  walk(design, out, params={})
  publish business images (*Images*)
  write out/render.json (*Render metadata*)
  check links in out (*Link check*)
  replace {run_dir}/sites/{workspace}/{design} with out

walk(folder, out, params):
  for each file in folder (not _*, not .*):
    page.html.j2  -> out/index.html with data = run(page.gql, params)
    *.gql         -> skipped (used by the rules around them)
    other *.j2    -> out/<name without .j2>
    other file    -> copied to out/<name>
  for each subfolder (not _*, not .*):
    if subfolder is [name]:
      for each item in run(subfolder/params.gql, params):
        walk(subfolder, out/item[name], params + {name: item[name]})
    else:
      walk(subfolder, out/<subfolder>, params)
```

A dynamic segment is expanded by the parent folder's walk. A `[name]` folder is only
ever rendered with its value set.

- Every template gets `config`, `site`, `params`, `path` and `render`, and
  `page.html.j2` also gets `data`.
- Files in a `[name]` folder are rendered and copied once per value.
- Two sources writing the same output path is an error. For example, a value `about`
  from `[slug]` next to a static `about/` folder.
- Output is written as UTF-8. Directories are created as needed.

**Images.** While rendering, the renderer collects every object in a query result with
`__typename: "Asset"` and an `id`. Each collected asset is looked up by id in the
rendered version, and its file is copied from `businessdata/resources/<path>` to
`resources/<path>` in the output folder. Only business images the site uses are
published. An asset whose file is missing fails the render, and so does one whose path
could leave `businessdata/resources/`. Such a path cannot pass validation, so this is a
second line of defence. Every file written is checked to land inside the output folder.

**Render metadata.** Every render writes `render.json` at the top of the output folder.
It is published with the site, so a script in the browser can load it, for example to
show "last updated" or to identify a preview:

```json
{
  "rendered_at": "2026-09-26T14:03:12.418Z",
  "generator": "intelliw 0.1.0",
  "design": { "name": "clinic-light", "title": "Clinic, light" },
  "businessdata": {
    "version_name": "active",
    "version": null,
    "snapshot": null,
    "based_on": { "number": 4, "tag": "Spring 2026", "created_at": "2026-09-20T10:00:00Z" },
    "modified": true,
    "updated_at": "2026-09-25T16:40:02.117Z"
  },
  "include_hidden": false
}
```

Validated by a Pydantic model (`RenderInfo`):

| Field | Meaning |
| --- | --- |
| `rendered_at` | when the render started (UTC) |
| `generator` | the intelliw package and version that rendered it |
| `design.name` / `design.title` | the design folder's name and its `_config.json` title |
| `businessdata.version_name` | the output folder's name (*Output folders*) |
| `businessdata.version` / `snapshot` | the rendered snapshot's number and tag; both `null` for the active version |
| `businessdata.based_on` | for the active version: the snapshot it is based on (number, tag, time taken); for a snapshot: the snapshot itself |
| `businessdata.modified` | for the active version: whether it had unsaved changes since `based_on`; `false` for a snapshot |
| `businessdata.updated_at` | the latest `updatedAt` across the rendered data (business and every non-deleted entity) |
| `include_hidden` | whether hidden entities were rendered |

- The metadata describes where the data came from, never the data itself: no business
  values, no workspace paths, no host or user names.
- The same object is `render` in every template, so templates and scripts agree.
- A page's script loads it relative to the page. Scripts run with the page's URL as
  base, so the page passes the link: `<body data-render="{{ url('render.json') }}">`,
  then `fetch(document.body.dataset.render)`.
- 05-site can read it to tell what an output folder contains before deploying it.

**Link check.** After the walk, every `.html` and `.css` output is scanned for links to
the site's own files. A root-relative link (starting with a single `/`, in `href`, `src`,
`srcset`, `action` or CSS `url()`) fails the render, naming the output file and its
source template. So does a relative link to a file that is not in the output. External
URLs and `#fragment` links are not checked.

**Atomic.** The site is rendered into a new temporary directory next to the output folder
(`.tmp-<random>`). On success it replaces the output folder: the old one is renamed
aside, the new one renamed in, then the old one removed. On failure the temporary
directory is removed and the output folder is unchanged. A failure reports the design file and, for template
errors, the line.

**Sandbox.** Templates run in `jinja2.sandbox.ImmutableSandboxedEnvironment`. They
cannot reach Python internals, the file system (other than design files through the
loader), the database or the network. Templates only format what the queries returned.
A template or static file that is a symlink resolving outside the design folder is
refused, so a design cannot publish other files.

### Design check

`check(design)` runs before every render of that design, and can be run on its own for
one design or every design in a workspace. It reports every problem it finds, not only
the first, each with the file:

- structure:
  - `page.gql` without a sibling `page.html.j2`;
  - `params.gql` outside a `[name]` folder, or a `[name]` folder without one;
  - `site.gql` below the top level;
  - bad segment names, repeated segment names in one path;
  - a top-level `resources`, `render.json` or `render.json.j2`;
- `_config.json` parses and validates;
- templates parse (syntax only);
- every `.gql` file:
  - parses;
  - is exactly one `query` operation;
  - validates against the schema, including variables: only enclosing segment names may
    be declared;
  - uses none of `includeHidden`, `version`, `snapshot`;
- every selection of an `Asset` includes `__typename`, `id` and `path`, so images can be
  published and linked;
- `params.gql` has one root field, of list type, whose selection includes the segment
  name as a field or alias.

Problems found only by running are render errors: missing keys or duplicates in
`params` results, output collisions, template runtime errors, missing asset files.

### Open questions

1. **Which design is published.** Every render names its design, and an output folder
   holds its design's latest render (`render.json` says which data). If 05-site needs a
   standing choice (the owner's current design), it can be a workspace setting that
   `build` falls back to when no name is given.
2. **Previews share the folder.** A render of a snapshot or with `include_hidden` writes
   to the design's one output folder, replacing the public site until the next automatic
   re-render. Should previews get their own folder?
3. **The not-found page.** A server shows `404.html` for a missing URL at any depth, so
   its relative links resolve against the missing URL and may break. Options: keep
   `404.html` self-contained (inline CSS, a link to `./` only), or leave it to the
   hosting setup in 05-site. Folder links ending in `/` likewise need a server;
   opening an output folder from the file system (`file://`) would need `url()` to link
   `index.html` explicitly.
4. **Static files in `[name]` folders.** The rule above copies them once per value. The
   alternative is to forbid non-template files there and keep shared files higher up.
5. **Empty page data.** `data` is passed as returned. A `page.gql` whose root entity is
   `null` renders anyway, with the template deciding what to show. Should that instead
   be a render error?

## Acceptance criteria

- [x] With two designs in `design/`, `build(workspace, "a")` and `build(workspace, "b")`
      each render from their own design folder only; an unknown name is an error, and
      `_*` / `.*` entries in `design/` are not designs.
- [x] A template cannot extend or include a file of another design.
- [x] The design check can check one named design or every design in a workspace.
- [x] A design renders to `{run_dir}/sites/{workspace}/{design}/`; a render leaves other
      designs' folders untouched; `render.json` names the rendered version (`active`,
      `3`, `4_Spring-2026`), whether the snapshot was named by number or by tag.
- [x] Every committed GraphQL or MCP mutation queues a re-render job carrying a
      `MutationEvent` (mutation name, commit time); queries and failed mutations queue
      nothing; a Redis outage doesn't fail the mutation.
- [x] The worker re-renders every design from the active version; a failing design
      doesn't stop the others.
- [x] `url()` and `asset_url()` give links relative to the output file at every depth;
      the rendered site works unchanged when served from a sub-folder of a web server.
- [x] A root-relative link or a relative link to a missing file in any `.html` / `.css`
      output fails the render; external URLs pass.
- [x] `build(..., snapshot=tag)` renders that snapshot's data; `include_hidden=True`
      renders hidden entities; the defaults render the active version without hidden
      entities.
- [x] Every output folder has `render.json` matching `RenderInfo`: render time, design,
      version name, snapshot number and tag (or the active version's `based_on` and
      `modified`), latest `updatedAt`, `include_hidden`. `render` in templates is the
      same object, and a page can fetch it through `url('render.json')` at any depth.
- [x] A top-level design entry named `resources`, `render.json` or `render.json.j2` is
      reported by the design check.
- [x] A design with `page.html.j2` + `page.gql` at the top level and in a static
      subfolder renders `index.html` and `<sub>/index.html` in its output folder
      with the query data.
- [x] `[slug]/` with `params.gql` renders one `index.html` per value, with `params.slug`
      in the template and `$slug` in `page.gql`; nested segments receive the enclosing
      values.
- [x] `site.gql` runs once, and its result is `site` in every template.
- [x] `other.ext.j2` renders to `other.ext`; other files are copied; `*.gql`, `_*` and
      dotfiles are not in the output.
- [x] Layouts and partials under `_layouts/` and `_partials/` can be extended and included
      from any folder.
- [x] By default a hidden entity gets no page from a list-based `params.gql` and is
      absent from lists and optional references in templates.
- [x] Assets selected with `__typename id path` are copied to `resources/<path>` in the
      output folder; unused assets are not.
- [x] A failing render (GraphQL error, template error, missing asset file, output
      collision, bad params) leaves the previous output folder unchanged and names the
      file.
- [x] A template cannot access attributes outside the sandbox (e.g. `__class__`).
- [x] The design check reports each structural rule, a query that is not a single
      query, `includeHidden` / `version` / `snapshot`, an Asset selection without
      `__typename id path`, a bad `params.gql` shape, and an invalid `_config.json`.
- [x] A misspelled context variable or field is a render error (`StrictUndefined`).
- [x] The Whitby Eye Care example ships at least one design that renders without errors.

## Implementation notes

`build(workspace, name, options)` in this document is
`intelliw.render.render(ws, design, RenderOptions(...), dry_run=...)`.

- `src/intelliw/render/`:
  - `layout.py`: design names, file kinds, `version_name`, `list_designs`;
  - `config.py`: `DesignConfig`;
  - `check.py`: `check_design`, `check_workspace`;
  - `env.py`: the sandbox, `url()` / `asset_url()`;
  - `query.py`: design queries, run in-process through `intelliw.graphql.schema`;
  - `links.py`: `relative_url`, the link check;
  - `info.py`: `RenderInfo`;
  - `renderer.py`: `render()`. It renders everything in memory, then writes (skipped by
    a dry run) and swaps the output folder;
  - `errors.py`: `Problem`, `DesignNotFound`, `CheckFailed` (design or link check),
    `RenderError`.
- `intelliw.graphql`: `Context.default_view` is used by query fields whose
  `version` / `snapshot` / `includeHidden` arguments are omitted. The API server keeps
  the defaults; the renderer sets its options. The schema is unchanged.
- `intelliw.businessdata.queries.latest_update` provides `businessdata.updated_at`.
- CLI `uv run render [<workspace>] --design <name>`, with `--version N` or
  `--snapshot TAG`, `--include-hidden` and `--dryrun`. The workspace defaults to
  `WORKSPACE` in `.env` (relative to the `.env` file), like the `business` CLI
  (`src/intelliw/cli/render.py`). Exit codes:
  - `0`: success;
  - `1`: a check failed or a render error;
  - `2`: a usage error (unknown design or snapshot, both version and snapshot, no
    database).
- Sites go to `Settings.sites_dir` (`{run_dir}/sites`), via `site_dir(sites_dir, ws, design)`;
  the re-render queue is `intelliw.jobs` (models, queue, tasks) with the `jobs` CLI.
- `Workspace`: `design(name)` is added;
  `templates_dir`, `assets_dir` and `site_dir` are removed. The prototype `intelliw.site`
  builder and the old `templates/` + `assets/` example designs are removed.
- The Whitby example ships a minimal design,
  `workspaces/whitby_eye_care/design/clinic/`: a home page, one page per service, a 404
  page, a stylesheet template, and a script that reads `render.json`.
  `workspaces/import_whitby_digest.py` now rebuilds only `businessdata/`, keeping the
  designs.
- A third example design, `workspaces/whitby_eye_care/design/clinic-pro/`, built from
  `docs/notes/2026-09-27.md` with the `/intelliw:design` skill; its choices are its own
  (see the design folder and `tests/test_clinic_pro_design.py`).
- A second, generic example design, `workspaces/whitby_eye_care/design/inspector/`, is a
  developer view of the entire #businessdata on one page.
  - Its `page.gql` selects every field of every collection, with references as
    `{ __typename id }`, plus versions, trash and resource files.
  - Generic Jinja macros render any value shape as a hierarchy:
    - each collection is a responsive multi-column grid of record cards;
    - each card lists its fields, with nested values and long text as indented,
      collapsible blocks;
    - references are chips linking to the card they point to.
  - On narrow screens the sidebar becomes a sticky top bar with a menu.
  - Every entity also has its own page, through a dynamic route per collection
    (`services/[id]/`, …, `reviews/[id]/`) and a static `business/` page.
    - Each route's `page.gql` selects every field, with related entities one level
      deep; its `page.html.j2` only extends `_layouts/entity.html.j2`.
    - `site.gql` is an index of all entities. Links go only to pages that were
      rendered, reference chips show titles, and entity pages have previous / next
      links.
    - Card titles and reference chips on the index lead to the entity pages.
  - `tests/test_inspector_design.py` checks that the query selects every schema field,
    and that every record, column and image is rendered.
- Tests:
  - `tests/test_render.py`: the renderer and the design check, one or more tests per
    criterion;
  - `tests/test_render_cli.py`: the CLI;
  - `tests/test_graphql_api.py`: the default view.
- Verified by hand: the example renders against the Whitby database, and served from a
  sub-folder (`python -m http.server -d outputs`, `/active/...`) its links, CSS and
  images load in a browser, and the page script fetches `render.json`.
