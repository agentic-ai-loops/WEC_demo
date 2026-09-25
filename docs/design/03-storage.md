# #businessdata storage

**Status:** implemented
**Depends on:** 01-businessdata, 02-graphql-api

## Goal

Persist a workspace's #businessdata so that the GraphQL API (02-graphql-api) can query
and mutate it efficiently, with every version — the active version and read-only
snapshots — stored side by side, and rollback to any snapshot always possible and exact.

## Scope

- In scope:
  - The database file and engine setup.
  - Table layout, keys, constraints and indexes.
  - How versions are stored; taking snapshots and activating them (rollback).
  - Tracking whether the active version is modified.
  - Whole-document transfer (`BusinessData` ↔ database) and workspace initialization.
  - Resource files.
  - Health checks (`business doctor`).
- Out of scope:
  - The GraphQL API and its mutations (02-graphql-api).
  - The purge admin tool (removing deleted entities).
  - Retiring old snapshots (02-graphql-api, open questions).
  - Importing `digest.xml` (a one-off script, `examples/import_whitby_digest.py`).
  - Concurrent writers.

## Specification

### Database

- One SQLite database per workspace: `<workspace>/businessdata/business.db`
  (`Workspace.database_file`). Database files are not committed to git.
- Foreign-key enforcement is switched on for every connection (`PRAGMA foreign_keys = ON`;
  SQLite leaves it off by default).
- SQLAlchemy 2 ORM. Row classes (`*Row` in `intelliw.businessdata.tables`) name their
  attributes exactly like the Pydantic fields (01-businessdata), so
  `Model.model_validate(row)` converts a row and `Row(version=v, **model.model_dump())`
  creates one. Child-table data is exposed through association proxies and properties
  for that purpose; there is no per-entity mapping code.

### Versions

- Every entity row has a `version` column in its primary key: `0` is the active version,
  `1, 2, ...` are snapshots.
- `snapshots(number, tag, parent, created_at)`: one row per snapshot; `tag` unique.
- `version_state(id = 1, based_on, modified)`: a single row — the snapshot the active
  version is based on, and whether it has changed since.
- References are composite foreign keys `(version, ref) → table(version, id)`, so a
  reference can never point into another version.

### Table layout

| Table | Holds | Key |
| --- | --- | --- |
| `business` | the singleton business | `version` |
| `business_brands`, `business_service_areas`, `business_payment_methods` | the business's value lists | `(version, position)` |
| `contacts`, `locations`, `service_categories`, `services`, `product_categories`, `staff`, `faqs`, `social_links`, `affiliations`, `actions`, `assets`, `reviews` | one table per collection | `(version, id)` |
| `opening_hours` | a location's hours | `(version, location_id, day, seq)` |
| `faq_services`, `action_services` | many-to-many links, with `position` | `(version, owner_id, service_id)` |
| `snapshots`, `version_state` | version metadata | see *Versions* |

- Nested value objects are flattened into their owner's columns (address, geo on
  `locations`); review targets into `target_collection`, `target_id`, `target_field`.
- `staff.languages` is a JSON array column (a list of BCP 47 tags) rather than a child
  table: languages are only ever read and written with their staff member. SQLAlchemy's
  `JSON` type stores it as JSON text; `MutableList` tracks in-place changes.
- Child and link rows are owned by their parent: `ON DELETE CASCADE`, and replaced when
  the parent's list is assigned.
- Entity rows are never hard-deleted by the API (`deleted` flag, 01-businessdata rule 12).

Constraints and indexes:

| Rule | Mechanism |
| --- | --- |
| References resolve within the version | composite foreign keys |
| Enum columns hold valid values | CHECK constraints (from the enum types) |
| At most one primary non-deleted contact point per kind | partial unique index `uq_contacts_one_primary_per_kind` |
| Hours: `closed` xor times; `open < close` | CHECK `ck_opening_hours_times` (times stored as fixed-format ISO strings) |
| Review target id present iff not `business` | CHECK `ck_reviews_target_id` |
| `staff.languages` is a JSON array | CHECK `ck_staff_languages_array` (`json_type(languages) = 'array'`) |
| One version-state row | CHECK `ck_version_state_single_row` |
| Reverse lookups | indexes on `services(version, category)`, `actions(version, channel)`, `faq_services(version, service_id)`, `action_services(version, service_id)`, `reviews(version, target_collection, target_id)` |

Timestamps are stored as naive UTC and returned as timezone-aware UTC.

### Foreign keys

Every reference between entities is a composite foreign key that includes `version`, so it
always points into the same version. Two kinds:

- **reference** — an entity field referring to another entity (e.g. `services.category`).
  `NO ACTION` on delete: entity rows are soft-deleted, and the mutation layer clears or
  refuses references before marking a target deleted (02-graphql-api, *Delete
  behaviour*). A nullable reference column that is `NULL` is not checked (SQLite treats a
  composite key with a `NULL` part as satisfied), which is how optional references work.
- **owner** — a child or link row pointing at the row that owns it. `ON DELETE CASCADE`:
  deleting the owner (only ever done when the active version is replaced during
  rollback, or by the purge admin tool) removes its children.

Two relationships have no foreign key:

- entity rows' `version` → `snapshots`, because the active version (`0`) has no
  `snapshots` row;
- `reviews` → its target: `(target_collection, target_id)` can name any collection, so
  it cannot be a foreign key. It is checked by the mutation layer (02-graphql-api,
  *Validation*) and by `business doctor`.

| Table | Columns | References | Kind | On delete |
| --- | --- | --- | --- | --- |
| `snapshots` | `(parent)` | `snapshots(number)` | reference | NO ACTION |
| `version_state` | `(based_on)` | `snapshots(number)` | reference | NO ACTION |
| `business` | `(version, favicon)` | `assets(version, id)` | reference | NO ACTION |
| `business` | `(version, logo)` | `assets(version, id)` | reference | NO ACTION |
| `business_brands` | `(version)` | `business(version)` | owner (child row) | CASCADE |
| `business_service_areas` | `(version)` | `business(version)` | owner (child row) | CASCADE |
| `business_payment_methods` | `(version)` | `business(version)` | owner (child row) | CASCADE |
| `locations` | `(version, phone)` | `contacts(version, id)` | reference | NO ACTION |
| `opening_hours` | `(version, location_id)` | `locations(version, id)` | owner (child row) | CASCADE |
| `services` | `(version, category)` | `service_categories(version, id)` | reference | NO ACTION |
| `services` | `(version, image)` | `assets(version, id)` | reference | NO ACTION |
| `product_categories` | `(version, image)` | `assets(version, id)` | reference | NO ACTION |
| `staff` | `(version, photo)` | `assets(version, id)` | reference | NO ACTION |
| `faq_services` | `(version, faq_id)` | `faqs(version, id)` | owner (child row) | CASCADE |
| `faq_services` | `(version, service_id)` | `services(version, id)` | reference | NO ACTION |
| `affiliations` | `(version, logo)` | `assets(version, id)` | reference | NO ACTION |
| `actions` | `(version, channel)` | `contacts(version, id)` | reference | NO ACTION |
| `action_services` | `(version, action_id)` | `actions(version, id)` | owner (child row) | CASCADE |
| `action_services` | `(version, service_id)` | `services(version, id)` | reference | NO ACTION |

### Schema

The complete SQLite schema, generated from `intelliw/businessdata/tables.py`. Enum
columns get a CHECK constraint named after their enum type. Mixin columns (`version`,
`id`, `position`, `deleted`, `hidden`, `created_at`, `updated_at`) follow each table's own
columns.

#### Versions

```sql
CREATE TABLE snapshots (
    number INTEGER NOT NULL,
    tag VARCHAR(100),
    parent INTEGER,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (number),
    UNIQUE (tag),
    FOREIGN KEY(parent) REFERENCES snapshots (number)
);

CREATE TABLE version_state (
    id INTEGER NOT NULL,
    based_on INTEGER NOT NULL,
    modified BOOLEAN NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_version_state_single_row CHECK (id = 1),
    FOREIGN KEY(based_on) REFERENCES snapshots (number)
);
```

#### Business

```sql
CREATE TABLE business (
    version INTEGER NOT NULL,
    name VARCHAR NOT NULL,
    legal_name VARCHAR,
    tagline VARCHAR NOT NULL,
    category VARCHAR NOT NULL,
    description VARCHAR NOT NULL,
    logo VARCHAR,
    favicon VARCHAR,
    serving_since INTEGER,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (version),
    FOREIGN KEY(version, logo) REFERENCES assets (version, id),
    FOREIGN KEY(version, favicon) REFERENCES assets (version, id)
);

CREATE TABLE business_brands (
    version INTEGER NOT NULL,
    position INTEGER NOT NULL,
    value VARCHAR NOT NULL,
    PRIMARY KEY (version, position),
    FOREIGN KEY(version) REFERENCES business (version) ON DELETE CASCADE
);

CREATE TABLE business_service_areas (
    version INTEGER NOT NULL,
    position INTEGER NOT NULL,
    value VARCHAR NOT NULL,
    PRIMARY KEY (version, position),
    FOREIGN KEY(version) REFERENCES business (version) ON DELETE CASCADE
);

CREATE TABLE business_payment_methods (
    version INTEGER NOT NULL,
    position INTEGER NOT NULL,
    value VARCHAR(32) NOT NULL,
    PRIMARY KEY (version, position),
    FOREIGN KEY(version) REFERENCES business (version) ON DELETE CASCADE,
    CONSTRAINT paymentmethod CHECK (value IN ('cash', 'credit', 'debit', 'cheque', 'e_transfer', 'direct_billing'))
);
```

#### Assets

```sql
CREATE TABLE assets (
    type VARCHAR(32) NOT NULL,
    path VARCHAR NOT NULL,
    alt VARCHAR NOT NULL,
    version INTEGER NOT NULL,
    id VARCHAR(100) NOT NULL,
    position INTEGER NOT NULL,
    deleted BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (version, id),
    CONSTRAINT assettype CHECK (type IN ('logo', 'favicon', 'photo'))
);
```

#### Contact points and locations

```sql
CREATE TABLE contacts (
    kind VARCHAR(32) NOT NULL,
    label VARCHAR NOT NULL,
    value VARCHAR NOT NULL,
    "primary" BOOLEAN NOT NULL,
    hidden BOOLEAN NOT NULL,
    version INTEGER NOT NULL,
    id VARCHAR(100) NOT NULL,
    position INTEGER NOT NULL,
    deleted BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (version, id),
    CONSTRAINT contactkind CHECK (kind IN ('phone', 'email', 'booking', 'store', 'website'))
);
CREATE UNIQUE INDEX uq_contacts_one_primary_per_kind ON contacts (version, kind) WHERE "primary" IS 1 AND deleted IS 0;

CREATE TABLE locations (
    name VARCHAR NOT NULL,
    street VARCHAR NOT NULL,
    city VARCHAR NOT NULL,
    region VARCHAR NOT NULL,
    postal_code VARCHAR NOT NULL,
    country VARCHAR NOT NULL,
    lat DOUBLE,
    lng DOUBLE,
    map_url VARCHAR,
    phone VARCHAR,
    hidden BOOLEAN NOT NULL,
    version INTEGER NOT NULL,
    id VARCHAR(100) NOT NULL,
    position INTEGER NOT NULL,
    deleted BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (version, id),
    FOREIGN KEY(version, phone) REFERENCES contacts (version, id)
);

CREATE TABLE opening_hours (
    version INTEGER NOT NULL,
    location_id VARCHAR(100) NOT NULL,
    day VARCHAR(32) NOT NULL,
    seq INTEGER NOT NULL,
    position INTEGER NOT NULL,
    open TIME,
    close TIME,
    closed BOOLEAN NOT NULL,
    PRIMARY KEY (version, location_id, day, seq),
    FOREIGN KEY(version, location_id) REFERENCES locations (version, id) ON DELETE CASCADE,
    CONSTRAINT ck_opening_hours_times CHECK ((closed = 1 AND open IS NULL AND close IS NULL) OR (closed = 0 AND open IS NOT NULL AND close IS NOT NULL AND open < close)),
    CONSTRAINT weekday CHECK (day IN ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'))
);
```

#### Services and products

```sql
CREATE TABLE service_categories (
    name VARCHAR NOT NULL,
    hidden BOOLEAN NOT NULL,
    version INTEGER NOT NULL,
    id VARCHAR(100) NOT NULL,
    position INTEGER NOT NULL,
    deleted BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (version, id)
);

CREATE TABLE services (
    category VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    summary VARCHAR NOT NULL,
    description VARCHAR NOT NULL,
    audience VARCHAR,
    image VARCHAR,
    hidden BOOLEAN NOT NULL,
    version INTEGER NOT NULL,
    id VARCHAR(100) NOT NULL,
    position INTEGER NOT NULL,
    deleted BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (version, id),
    FOREIGN KEY(version, category) REFERENCES service_categories (version, id),
    FOREIGN KEY(version, image) REFERENCES assets (version, id)
);
CREATE INDEX ix_services_category ON services (version, category);

CREATE TABLE product_categories (
    name VARCHAR NOT NULL,
    description VARCHAR NOT NULL,
    image VARCHAR,
    hidden BOOLEAN NOT NULL,
    version INTEGER NOT NULL,
    id VARCHAR(100) NOT NULL,
    position INTEGER NOT NULL,
    deleted BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (version, id),
    FOREIGN KEY(version, image) REFERENCES assets (version, id)
);
```

#### Staff

```sql
CREATE TABLE staff (
    name VARCHAR NOT NULL,
    role VARCHAR NOT NULL,
    credentials VARCHAR NOT NULL,
    bio VARCHAR NOT NULL,
    photo VARCHAR,
    languages JSON NOT NULL,
    hidden BOOLEAN NOT NULL,
    version INTEGER NOT NULL,
    id VARCHAR(100) NOT NULL,
    position INTEGER NOT NULL,
    deleted BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (version, id),
    FOREIGN KEY(version, photo) REFERENCES assets (version, id),
    CONSTRAINT ck_staff_languages_array CHECK (json_type(languages) = 'array')
);
```

#### FAQs

```sql
CREATE TABLE faqs (
    question VARCHAR NOT NULL,
    answer VARCHAR NOT NULL,
    hidden BOOLEAN NOT NULL,
    version INTEGER NOT NULL,
    id VARCHAR(100) NOT NULL,
    position INTEGER NOT NULL,
    deleted BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (version, id)
);

CREATE TABLE faq_services (
    version INTEGER NOT NULL,
    faq_id VARCHAR(100) NOT NULL,
    service_id VARCHAR(100) NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (version, faq_id, service_id),
    FOREIGN KEY(version, faq_id) REFERENCES faqs (version, id) ON DELETE CASCADE,
    FOREIGN KEY(version, service_id) REFERENCES services (version, id)
);
CREATE INDEX ix_faq_services_service ON faq_services (version, service_id);
```

#### Social links and affiliations

```sql
CREATE TABLE social_links (
    platform VARCHAR(32) NOT NULL,
    url VARCHAR NOT NULL,
    label VARCHAR NOT NULL,
    hidden BOOLEAN NOT NULL,
    version INTEGER NOT NULL,
    id VARCHAR(100) NOT NULL,
    position INTEGER NOT NULL,
    deleted BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (version, id),
    CONSTRAINT socialplatform CHECK (platform IN ('facebook', 'instagram', 'x', 'pinterest', 'linkedin', 'youtube', 'tiktok'))
);

CREATE TABLE affiliations (
    name VARCHAR NOT NULL,
    description VARCHAR NOT NULL,
    url VARCHAR,
    logo VARCHAR,
    hidden BOOLEAN NOT NULL,
    version INTEGER NOT NULL,
    id VARCHAR(100) NOT NULL,
    position INTEGER NOT NULL,
    deleted BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (version, id),
    FOREIGN KEY(version, logo) REFERENCES assets (version, id)
);
```

#### Customer actions

```sql
CREATE TABLE actions (
    type VARCHAR(32) NOT NULL,
    label VARCHAR NOT NULL,
    channel VARCHAR NOT NULL,
    hidden BOOLEAN NOT NULL,
    version INTEGER NOT NULL,
    id VARCHAR(100) NOT NULL,
    position INTEGER NOT NULL,
    deleted BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (version, id),
    FOREIGN KEY(version, channel) REFERENCES contacts (version, id),
    CONSTRAINT actiontype CHECK (type IN ('book', 'call', 'email', 'buy', 'visit'))
);
CREATE INDEX ix_actions_channel ON actions (version, channel);

CREATE TABLE action_services (
    version INTEGER NOT NULL,
    action_id VARCHAR(100) NOT NULL,
    service_id VARCHAR(100) NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (version, action_id, service_id),
    FOREIGN KEY(version, action_id) REFERENCES actions (version, id) ON DELETE CASCADE,
    FOREIGN KEY(version, service_id) REFERENCES services (version, id)
);
CREATE INDEX ix_action_services_service ON action_services (version, service_id);
```

#### Review items

```sql
CREATE TABLE reviews (
    target_collection VARCHAR(32) NOT NULL,
    target_id VARCHAR(100),
    target_field VARCHAR(64),
    note VARCHAR NOT NULL,
    status VARCHAR(32) NOT NULL,
    resolution VARCHAR,
    version INTEGER NOT NULL,
    id VARCHAR(100) NOT NULL,
    position INTEGER NOT NULL,
    deleted BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (version, id),
    CONSTRAINT ck_reviews_target_id CHECK ((target_collection = 'business') = (target_id IS NULL)),
    CONSTRAINT reviewstatus CHECK (status IN ('open', 'resolved', 'dismissed'))
);
CREATE INDEX ix_reviews_target ON reviews (version, target_collection, target_id);
```

### Document transfer

`intelliw.businessdata.documents`:

- `write_version(session, data, version)` inserts a whole `BusinessData` document into an
  empty version, in foreign-key order.
- `read_version(session, version, validate=True)` reads a version back as a document,
  including hidden and deleted entities. `validate=False` skips the document-level
  integrity check, so a damaged version can still be read and inspected.
- `initialize(session, data)` loads the first document into an empty database: snapshot 1,
  an active version based on it, `modified = false`.

Load → save through the database is lossless.

### Snapshot operations

In `intelliw.businessdata.documents`, each in the caller's transaction:

**`take_snapshot(session, tag=None, *, now=None) -> Version`**

1. Reject a tag already in use (`TagConflict`) or an invalid tag (`ValueError`).
2. Allocate the next number: `max(snapshots.number) + 1`.
3. Insert the `snapshots` row (`parent` = current `based_on`, `created_at` = now).
4. Copy the active version into the new number: `write_version(session,
   read_version(session, ACTIVE), n)`.
5. Set `version_state` to `based_on = n`, `modified = false`.

**`activate_version(session, number, *, now=None) -> tuple[VersionIndex, Version | None]`**

1. Reject an unknown snapshot (`NotFound`).
2. If the active version is modified, `take_snapshot(session)` (untagged) first; this is
   the returned `Version`, otherwise `None`.
3. Delete every entity row of version `0`, in reverse insert order (child and link rows
   cascade), and clear the session's identity map of the deleted rows.
4. Copy the snapshot into version `0`: `write_version(session, read_version(session,
   number), ACTIVE)`. Timestamps are copied unchanged.
5. Set `version_state` to `based_on = number`, `modified = false`.

**`mark_modified(session)`** sets `version_state.modified = true`. Every mutation of the
active version calls it in the same transaction (02-graphql-api).

Snapshot rows are never updated or deleted: the only code paths that write rows with
`version > 0` are `initialize` and `take_snapshot`.

### Resource files

- Asset files live under `businessdata/resources/`; `Asset.path` is relative to it.
- Files are immutable and shared by all versions (01-businessdata, *Snapshot
  versioning*): a file must not be overwritten or removed while the active version or any
  snapshot refers to it. This is a requirement on the web interface that manages
  resources and on the purge admin tool.

### Health checks

`intelliw.businessdata.doctor.check` (the `business doctor` command) checks one version —
the active version unless a snapshot is named (`--version` / `--tag`) — and returns a
report:

- statistics per collection (live, hidden, deleted) and the number of open review items;
- SQLite `foreign_key_check` violations of the version's rows, and the database-wide
  `integrity_check`;
- the version state exists and refers to an existing snapshot;
- the document rules of 01-businessdata (`integrity_errors`);
- missing resource files: the version's asset files that do not exist;
- files under `businessdata/resources/` that no version at all refers to (reported, not a
  problem; files are shared, so a file used only by another snapshot is not stray).

`business doctor` exits with status 1 if there is an integrity problem or a missing file.

### Open questions

1. **Enforcing read-only snapshots in the database.** Triggers could reject `UPDATE` /
   `DELETE` of rows with `version > 0`, at the cost of complicating snapshot retirement.
2. **Resource files are not versioned.** Snapshots hold asset *records*, but the files
   under `businessdata/resources/` exist once, outside the database. A snapshot cannot
   restore a file that was overwritten or deleted, so rollback consistency rests entirely
   on the immutability rule (*Resource files*), which nothing enforces today — deleting a
   file is only detected afterwards, by `business doctor`. Accepted for now. Possible
   remedies: record a content hash on each asset so changed files are detected; store
   files content-addressed (the path is the hash) so a new upload can never replace an
   old one; or have the resources web interface refuse to overwrite or remove files that
   any version refers to.

## Acceptance criteria

- [x] Foreign keys are enforced on every connection.
- [x] References cannot cross versions.
- [x] The constraints listed above reject invalid rows.
- [x] Batched lookups by id issue one query.
- [x] Load → save of a document through the database is lossless.
- [x] `initialize` creates snapshot 1 and an unmodified active version based on it.
- [x] `business doctor` reports integrity problems, missing files and unregistered files
      for the selected version (active by default), and exits 1 on problems.
- [x] `take_snapshot` creates the next snapshot as an exact copy of the active version,
      rejects a duplicate tag, and leaves the active version unmodified and based on it.
- [x] `activate_version` makes the active version an exact copy of the snapshot; if the
      active version was modified, it is snapshotted first and that snapshot is returned.
- [x] No operation changes rows with `version > 0` after they are written.
- [x] `mark_modified` sets `modified`; `take_snapshot` and `activate_version` clear it.

## Implementation notes

| Module | Contents |
| --- | --- |
| `intelliw/businessdata/tables.py` | row classes, constraints, indexes, `ACTIVE` |
| `intelliw/businessdata/database.py` | `create_db_engine`, `init_db`, `session_factory` |
| `intelliw/businessdata/documents.py` | `write_version`, `read_version`, `initialize`, `take_snapshot`, `activate_version`, `mark_modified`, `TagConflict` |
| `intelliw/businessdata/doctor.py` | `check` → `Report` |
| `intelliw/cli/business.py` | `business doctor`, `dump`, `versions`, `snapshot`, `activate` |

Tests: `tests/test_businessdata_db.py`, `tests/test_business_cli.py`.
