# GraphQL API over #businessdata

**Status:** implemented
**Depends on:** 00-architecture, 01-businessdata

## Goal

Define the GraphQL API (`intelliw.graphql`, Strawberry) through which #owner agents
query and mutate #businessdata. Every field an #owner may want to change is reachable by
a small, obvious mutation, and every query a #design template or agent needs resolves
efficiently on a relational storage backend.

The MCP server (06-mcp-server) forwards to this API.

## Scope

- In scope:
  - Requirements on storage.
  - Types, queries and mutation conventions.
  - Update semantics.
  - Assets: registering uploaded files and referencing them.
  - Delete, restore, validation and errors.
  - Review-item side effects.
  - Versions: snapshots, rollback, reading snapshots.
  - Query efficiency.
- Out of scope:
  - Authentication/authorisation (one #owner per workspace; handled by the MCP layer).
  - Uploading files. A dedicated web interface manages files in
    `businessdata/resources/` and `design/assets/`; this API records and references files
    that are already there.
  - #design images and slots (04-design).
  - Purging deleted entities (a separate admin tool).
  - Subscriptions / live updates.
  - Generating the code.

## Specification

### Storage requirements

The storage is specified in 03-storage. This API requires:

1. **One database per workspace**, so keys are plain slugs within a version and
   `business` is a single row per version.
2. **Every row is scoped to a version** (01-businessdata, *Snapshot versioning*): either
   a `version` column in every primary key with (version, id) foreign keys, or separate
   storage per version. Snapshot rows are never updated.
3. **One table per collection**, foreign keys for references, join tables for
   many-to-many links, child tables for nested lists (opening hours, brands, service
   areas, payment methods), and a JSON array column for staff languages.
4. **Rows are never hard-deleted by the API.** Deletion is a flag; reference clearing is
   done by the mutation layer.
5. **Snapshot and rollback copy whole versions** (on the order of 200 rows) in one
   transaction.
6. **`BusinessData` is the import/export format**, not the storage unit. Day-to-day
   changes are row-level mutations, each validated in its own transaction.

### Naming conventions

- Types are named after entities: `Business`, `ContactPoint`, `Location`,
  `OpeningHours`, `ServiceCategory`, `Service`, `ProductCategory`, `StaffMember`, `Faq`,
  `SocialLink`, `Affiliation`, `CustomerAction`, `Asset`, `ReviewItem`.
- Fields are camelCase (Strawberry's conversion from the snake_case model fields).
  Review targets name fields by their camelCase name.
- Entity ids use the `ID` scalar and hold the slug.
- References appear as resolved objects in output types (`category: ServiceCategory!`)
  and as ids in inputs (`category: ID!`).
- Enum values are the model's enum values (e.g. `e_transfer`, `direct_billing`).

### Types (abridged SDL)

```graphql
scalar Time        # "HH:MM"
scalar DateTime    # ISO 8601, UTC
scalar Url

type Business {
  name: String!  legalName: String  tagline: String!  category: String!
  description: String!  servingSince: Int
  logo: Asset  favicon: Asset
  brands: [String!]!  serviceAreas: [String!]!  paymentMethods: [PaymentMethod!]!
  reviews: [ReviewItem!]!
}

type ContactPoint { id: ID!  kind: ContactKind!  label: String!  value: String!
                    primary: Boolean!  href: String!        # derived: tel:, mailto:, URL
                    usedBy: [CustomerAction!]!  reviews: [ReviewItem!]! }

type Location { id: ID!  name: String!  address: Address!  geo: GeoPoint  mapUrl: Url
                phone: ContactPoint  hours: [OpeningHours!]!  reviews: [ReviewItem!]! }
type Address  { street: String!  city: String!  region: String!  postalCode: String!  country: String! }
type GeoPoint { lat: Float!  lng: Float! }
type OpeningHours { day: Weekday!  seq: Int!  open: Time  close: Time  closed: Boolean! }

type ServiceCategory { id: ID!  name: String!  services: [Service!]! }
type Service { id: ID!  category: ServiceCategory!  name: String!  summary: String!
               description: String!  audience: String  image: Asset
               faqs: [Faq!]!  actions: [CustomerAction!]!  reviews: [ReviewItem!]! }
type ProductCategory { id: ID!  name: String!  description: String!  image: Asset  reviews: [ReviewItem!]! }
type StaffMember { id: ID!  name: String!  role: String!  credentials: String!  bio: String!
                   languages: [String!]!  photo: Asset  reviews: [ReviewItem!]! }
type Faq { id: ID!  question: String!  answer: String!  services: [Service!]! }
type SocialLink { id: ID!  platform: SocialPlatform!  url: Url!  label: String! }
type Affiliation { id: ID!  name: String!  description: String!  url: Url  logo: Asset }
type CustomerAction { id: ID!  type: ActionType!  label: String!  channel: ContactPoint!
                      href: String!  services: [Service!]! }
type Asset { id: ID!  type: AssetType!  path: String!  alt: String!
             usedBy: [EntityRef!]! }                # entities referencing this asset

type EntityRef { collection: Collection!  id: ID  field: String }

union ReviewTargetEntity = Business | ContactPoint | Location | ServiceCategory | Service
  | ProductCategory | StaffMember | Faq | SocialLink | Affiliation | CustomerAction | Asset
type ReviewItem { id: ID!  target: ReviewTarget!  note: String!  status: ReviewStatus!  resolution: String }
type ReviewTarget { collection: Collection!  id: ID  field: String
                    entity: ReviewTargetEntity }    # null if the target does not exist
```

Also on every type, omitted above for brevity:

- `hidden: Boolean!` on content entities (01-businessdata, rule 10);
- `createdAt: DateTime!` and `updatedAt: DateTime!` on every entity (rule 11);
- `version: Int` on every entity — the snapshot it was read from, `null` for the active
  version.

`position` and `deleted` are not exposed: lists are returned in position order, and
deleted entities are not returned.

### Queries

```graphql
type Query {
  business: Business!

  contactPoints(kind: ContactKind): [ContactPoint!]!
  locations: [Location!]!
  serviceCategories: [ServiceCategory!]!
  services(category: ID): [Service!]!
  productCategories: [ProductCategory!]!
  staff: [StaffMember!]!
  faqs(service: ID): [Faq!]!
  socialLinks: [SocialLink!]!
  affiliations: [Affiliation!]!
  actions: [CustomerAction!]!
  assets(type: AssetType, unused: Boolean): [Asset!]!
  resourceFiles(unregistered: Boolean): [String!]!   # paths under businessdata/resources/
  reviews(status: ReviewStatus = open): [ReviewItem!]!
  trash(collection: Collection): [TrashItem!]!       # see *Restore*

  snapshots: [Snapshot!]!                            # see *Versions*
  activeVersion: ActiveVersion!

  # one by-id field per collection
  contactPoint(id: ID!): ContactPoint
  location(id: ID!): Location
  service(id: ID!): Service
  staffMember(id: ID!): StaffMember
  # ... likewise for every collection
}
```

- The workspace comes from the server context, never from an argument.
- Every field except `snapshots` and `activeVersion` takes the optional `version` /
  `snapshot` arguments (see *Versions*); omitted above for brevity.
- **Deleted entities are never returned**, except by `trash`: list and by-id queries (a
  by-id query for a deleted entity returns `null`), relation fields, `usedBy`,
  `ReviewTarget.entity` and the `unused` / `unregistered` queries all exclude them (a
  deleted asset's file counts as unregistered).
- **Hidden entities are returned**, with `hidden: true`. Every list query over content
  entities takes an optional `hidden: Boolean` filter (`staff(hidden: false)` = visible
  staff only); omitted = all.

### Mutations

Every collection has six mutations:

| Mutation | Purpose |
| --- | --- |
| `create<Entity>(input, before: ID)` | Add an entity at the end, or ahead of `before`. Returns the entity. |
| `update<Entity>(id, patch)` | Partial update (see *Update semantics*). Returns the entity. |
| `delete<Entity>(id)` | Mark deleted (see *Delete behaviour*). Returns a `DeleteResult`. |
| `restore<Entity>(id)` | Undelete (see *Restore*). Returns a `RestoreResult`. |
| `move<Entity>(id, before: ID)` | Move ahead of `before`; `before: null` moves to the end. |
| `reorder<Collection>(ids: [ID!]!)` | Set the complete order. `ids` must be exactly the current non-deleted ids. |

Plus:

| Mutation | Purpose |
| --- | --- |
| `updateBusiness(patch)` | The singleton; no create/delete/restore/move. |
| `setOpeningHours(location: ID!, day: Weekday!, intervals: [TimeRangeInput!]!, closed: Boolean)` | Replace one day's hours; other days untouched. |
| `resolveReview(id, resolution)` / `dismissReview(id, reason)` / `reopenReview(id)` | Review workflow. |
| `createReview(target, note)` | Raise a question for the #owner. |
| `takeSnapshot(tag)` / `activateVersion(version \| snapshot)` | See *Versions*. |

- Every mutation acts on the active version. Mutations accept the `version` / `snapshot`
  arguments, but naming a snapshot fails with `READ_ONLY`.
- Positions are renumbered 0…n-1 within the mutation's transaction.
- **Ids on create.** If `input.id` is omitted, the server generates a slug from the
  name/label/question (`Dr. Jane Doe` → `dr-jane-doe`), appending `-2`, `-3`, ... on a
  clash, and returns it. Ids of deleted entities count as clashes; an explicit `id`
  that is already taken, by a live or deleted entity, fails with `CONFLICT`.

### Update semantics

- **Omitted field = unchanged; explicit `null` = clear.** Strawberry exposes the
  difference as `UNSET`. Clearing a required field fails with `VALIDATION`.
- **Nested value objects are replaced whole.** `updateLocation(id, {address: {...}})`
  requires a complete address.
- **Value lists are replaced whole**: `brands`, `serviceAreas`, `paymentMethods`,
  `languages`, `Faq.services`, `CustomerAction.services`.
- **Hours are replaced one day at a time** with `setOpeningHours`.
- **References are set by id**: `updateStaffMember(id, {photo: "new-photo"})`;
  `{photo: null}` removes the photo.
- **Hiding is an ordinary update**: `{hidden: true}` hides, `{hidden: false}` shows.
- **Timestamps are server-set.** `createdAt` / `updatedAt` in an input or patch fails
  with `VALIDATION`. A mutation advances `updatedAt` of every entity whose content it
  changes (01-businessdata, rule 11), including the deleted or restored entity; moves
  and reorders advance none.
- Bios are free text; consistency with structured fields is not enforced.

### Assets and uploaded files

The web interface places files in `businessdata/resources/`. The API registers them and
wires them to entities:

| Step | Mutation |
| --- | --- |
| Register an uploaded file | `createAsset(input: {path, type, alt})` |
| Reference it | `updateBusiness({logo})`, `updateStaffMember(id, {photo})`, `updateService(id, {image})`, ... |
| Point an asset at a different file, keeping every reference | `updateAsset(id, {path})` |
| Change alt text or type | `updateAsset(id, {alt, type})` |
| Remove the asset | `deleteAsset(id)` |

- `path` is relative to `businessdata/resources/` and must name an existing file,
  otherwise `VALIDATION`.
- Files are immutable (01-businessdata, *Snapshot versioning*); the API never writes or
  removes files.
- `resourceFiles(unregistered: true)` lists files no asset refers to.

Example — Dr. Chan's portrait after the #owner has uploaded `dr-andrea-chan.jpg`:

```graphql
mutation {
  createAsset(input: {id: "dr-andrea-chan", path: "dr-andrea-chan.jpg", type: photo,
                      alt: "Portrait of Dr. Andrea Chan"}) { id }
  updateStaffMember(id: "dr-andrea-chan", patch: {photo: "dr-andrea-chan"}) { id photo { path } }
}
```

### Delete behaviour

`delete<Entity>` sets the entity's `deleted` flag (01-businessdata, rule 12); it never
removes the row. From then on the entity behaves as non-existent, and every mutation on
it except `restore<Entity>` fails with `NOT_FOUND`. At deletion:

| Collection | On delete |
| --- | --- |
| staff, faqs, social links, affiliations, product categories, customer actions | Deleted; join rows removed |
| services | Deleted; join rows to FAQs and actions removed. #design slot bindings are left to the design check (04-design) |
| service categories | Refused with `IN_USE` while a non-deleted service is in the category |
| contact points | Refused with `IN_USE` while a non-deleted customer action uses it as channel; a location's `phone` referring to it is cleared |
| assets | Deleted; every image/photo/logo/favicon reference to it is cleared |
| locations | Deleted with their hours |
| all | Open review items targeting the entity (or its fields) are dismissed with reason "target deleted" |

```graphql
type DeleteResult {
  id: ID!
  clearedReferences: [EntityRef!]!   # e.g. {collection: staff, id: "dr-andrea-chan", field: "photo"}
  dismissedReviews: [ID!]!
  orphanedAssets: [ID!]!             # assets no longer referenced by anything
}
```

Orphaned assets are reported, not deleted.

### Restore

A deleted entity can be restored in the active version until the admin tool purges it.

`trash(collection)` lists deleted entities, newest first:

```graphql
type TrashItem {
  ref: EntityRef!          # collection + id
  label: String!           # name / label / question
  deletedAt: DateTime!     # the entity's updatedAt at deletion
}
```

`restore<Entity>(id)` clears `deleted`, advances `updatedAt` and appends the entity to
the end of its collection. It restores the entity only: references cleared, links
removed and review items dismissed at deletion are not restored (the `DeleteResult`
lists them; `reopenReview` reopens a review). The restored entity's own references are
checked against the current data:

| The restored entity refers to... | Result |
| --- | --- |
| a deleted entity through a required reference (`Service.category`, `CustomerAction.channel`, `ReviewItem.target`) | Refused with `VALIDATION`, naming the reference |
| a deleted entity through an optional reference (`image`, `photo`, `logo`, `Location.phone`) or a many-to-many list | Reference cleared / link dropped, and reported |
| a primary contact point when another non-deleted primary of the same kind exists | Restored with `primary: false`, and reported |

```graphql
type RestoreResult {
  id: ID!
  clearedReferences: [EntityRef!]!
  demotedPrimary: Boolean!           # contact points only
}
```

`restore<Entity>` on an id that is not deleted, or unknown, fails with `NOT_FOUND`.

### Versions

#businessdata has one editable active version and numbered, optionally tagged,
read-only snapshots (01-businessdata, *Snapshot versioning*).

```graphql
type Snapshot {
  number: Int!
  tag: String
  parent: Int              # snapshot the active version was based on; null for 1
  createdAt: DateTime!
}

type ActiveVersion {
  basedOn: Snapshot!
  modified: Boolean!       # changed since basedOn
}

type ActivateResult { activeVersion: ActiveVersion!  autoSnapshot: Snapshot }

type Query {
  snapshots: [Snapshot!]!  # newest first
  activeVersion: ActiveVersion!
}

type Mutation {
  takeSnapshot(tag: String): Snapshot!
  activateVersion(version: Int, snapshot: String): ActivateResult!
}
```

- **`takeSnapshot(tag)`** copies the active version into the next snapshot. The active
  version is then based on it and not modified. A tag already in use fails with
  `CONFLICT`; an invalid tag (01-businessdata) with `VALIDATION`.
- **`activateVersion`** (rollback) takes exactly one of `version` / `snapshot` and makes
  the active version an exact copy of that snapshot, timestamps included. If the active
  version is modified, an untagged snapshot of it is taken first and returned as
  `autoSnapshot`.
- `activateVersion` leaves the active version not modified (based on the activated
  snapshot); any other successful mutation except `takeSnapshot` sets `modified` to
  `true`.

**Reading a version.** Every query field except `snapshots` and `activeVersion` takes:

| Argument | Reads |
| --- | --- |
| `version: Int` | the snapshot with that number |
| `snapshot: String` | the snapshot with that tag |
| neither | the active version |

- Both arguments at once fail with `VALIDATION`; an unknown number or tag with
  `NOT_FOUND`.
- Nested fields resolve in their parent's version. One request can read several
  versions through several top-level fields.
- `trash(version: n)` lists deleted entities of snapshot `n`; they cannot be restored
  there.

```graphql
# Save point, then roll back to it (edits made since are kept in autoSnapshot)
mutation { takeSnapshot(tag: "reviewed-2026-09") { number tag } }
mutation { activateVersion(snapshot: "reviewed-2026-09") {
             activeVersion { basedOn { number } modified } autoSnapshot { number } } }

# Compare the bio now with snapshot 1
query {
  now:    staffMember(id: "dr-andrea-chan")             { bio version }
  before: staffMember(id: "dr-andrea-chan", version: 1) { bio version }
}

# Editing a snapshot fails with READ_ONLY
mutation { updateStaffMember(id: "dr-andrea-chan", version: 1, patch: {bio: "..."}) { id } }
```

### Validation

Each mutation runs in one transaction and is rejected as a whole if any rule fails.

| Rule | Enforced by |
| --- | --- |
| Ids unique per collection per version, counting deleted entities | primary key |
| References resolve | foreign keys |
| References point at non-deleted entities | mutation layer |
| At most one primary non-deleted contact point per kind | partial unique index (`WHERE primary AND NOT deleted`) |
| Hours: `closed` xor times; `open < close`; (day, seq) unique; no overlapping intervals | check constraints + mutation layer |
| `Asset.path` names an existing file under `businessdata/resources/` | mutation layer |
| `Business.logo` → asset of type `logo`; `Business.favicon` → type `favicon` | mutation layer |
| Customer-action type fits its channel's kind (call→phone, book→booking/phone, buy→store, email→email, visit→website), checked when an action is created/updated and when a contact point's kind changes | mutation layer |
| Review target exists (`id` in `collection`; `field` is a field of that type) | mutation layer |
| `reorder<Collection>` lists exactly the current non-deleted ids | mutation layer |
| Mutations do not name a snapshot | mutation layer |

A hidden entity is a valid reference target; hiding never fails validation.

### Errors

Failures are GraphQL errors with a machine-readable code and a plain-language message the
agent can relay to the #owner:

```json
{"message": "Contact point 'booking' is used by customer action 'book-appointment'.",
 "extensions": {"code": "IN_USE", "usedBy": [{"collection": "actions", "id": "book-appointment"}]}}
```

| Code | Meaning |
| --- | --- |
| `NOT_FOUND` | entity, snapshot or tag does not exist (deleted entities count as not existing) |
| `VALIDATION` | a validation rule failed |
| `IN_USE` | delete refused; `usedBy` lists the users |
| `CONFLICT` | id or tag already taken |
| `STALE_ORDER` | `reorder<Collection>` list does not match the current ids |
| `READ_ONLY` | a mutation named a snapshot |

### Review side effects

- Every reviewable type exposes `reviews`, so an agent updating a field can select the
  open questions about it in the same request.
- Deleting an entity dismisses its open review items.
- Mutations never resolve review items automatically.

### Efficiency

- Each relation has a DataLoader keyed by (version, id), so a nested query costs one
  batched query per nesting level and several versions can be read in one request.
- Reverse relations (`Service.faqs`, `ContactPoint.usedBy`, `*.reviews`) use indexed
  join or target columns.

### Worked examples

```graphql
# Change the phone number: the location and "Call Now" follow automatically
mutation { updateContactPoint(id: "main-phone", patch: {value: "905-555-0100"}) { id href usedBy { id } } }

# Switch the booking system
mutation { updateContactPoint(id: "booking", patch: {value: "https://form.jotform.com/..."}) { id reviews { id note } } }

# Change Tuesday's closing time
mutation { setOpeningHours(location: "whitby", day: tuesday, intervals: [{open: "09:00", close: "19:00"}]) { hours { day open close } } }

# Update a bio
mutation { updateStaffMember(id: "dr-andrea-chan", patch: {bio: "..."}) { id bio } }

# Add, remove, reorder staff
mutation { createStaffMember(input: {name: "Dr. Jane Doe", role: "Optometrist"}) { id } }
mutation { deleteStaffMember(id: "dr-hajra-naeem") { dismissedReviews orphanedAssets } }
mutation { moveStaffMember(id: "dr-sumeya-mao", before: "dr-andrea-chan") { id } }
mutation { reorderStaff(ids: ["dr-raniero-fernando", "dr-sumeya-mao", "dr-andrea-chan", "dr-hajra-naeem"]) { id } }

# Undo a deletion
query    { trash(collection: staff) { ref { id } label deletedAt } }
mutation { restoreStaffMember(id: "dr-hajra-naeem") { clearedReferences { field } } }

# Resolve a review
mutation { resolveReview(id: "booking-system", resolution: "Use Atlas; JotForm page removed") { status } }
```

### Open questions

1. **Retiring snapshots.** Whether old snapshots (including automatic ones) can be
   deleted, and by this API or the admin tool. Deleting a snapshot would also free files
   only it refers to.
2. **Changing tags.** Whether a snapshot's tag may be added, changed or removed after it
   is taken.
3. **Concurrent edits** (not a concern for now). Whole-field replacement can lose an edit
   if two agent sessions write in turn; `updatedAt` could serve as an optimistic check.

## Acceptance criteria

- [x] Every collection has `create`, `update`, `delete`, `restore`, `move` and `reorder` mutations following the naming convention.
- [x] Omitted patch fields are unchanged; explicit `null` clears optional fields and fails with `VALIDATION` for required ones.
- [x] `reorder<Collection>` with a missing or extra id fails with `STALE_ORDER`.
- [x] Deleting a service category in use, or a contact point used as a channel, fails with `IN_USE` naming the users.
- [x] Deleting an asset clears all references to it and reports them in `DeleteResult.clearedReferences`.
- [x] Deleting an entity dismisses open review items that target it.
- [x] Changing a contact point's kind so that an action's type no longer fits fails with `VALIDATION`.
- [x] `setOpeningHours` changes one day and leaves the others untouched.
- [x] A query for all services with category, image, FAQs and actions issues one SQL query per nesting level.
- [x] Creating an entity without an id generates a unique slug from its name, skipping ids of deleted entities.
- [x] Creating an entity with a taken id (live or deleted) fails with `CONFLICT`.
- [x] `createAsset` / `updateAsset` with a `path` that does not exist fails with `VALIDATION`.
- [x] `updateAsset(id, {path})` changes the file while every reference to the asset is kept.
- [x] `resourceFiles(unregistered: true)` lists exactly the files no non-deleted asset refers to.
- [x] Hidden entities are returned with `hidden: true`; `hidden: false` filters them out.
- [x] Hiding a referenced entity succeeds and leaves the references intact.
- [x] `createdAt` / `updatedAt` cannot be set: they are not input fields, so GraphQL
      rejects them during document validation (see *Implementation notes*).
- [x] An update, delete or restore advances the entity's `updatedAt`; a move or reorder advances none.
- [x] `delete<Entity>` marks the entity deleted without removing the row.
- [x] Deleted entities are absent from list queries, by-id queries (`null`), relation fields and `ReviewTarget.entity`.
- [x] Updating, moving or deleting a deleted entity fails with `NOT_FOUND`.
- [x] `trash` lists exactly the deleted entities, newest first; no other query returns them.
- [x] `restore<Entity>` clears `deleted` and appends the entity to the end of its collection.
- [x] Restoring an entity whose required reference points at a deleted entity fails with `VALIDATION`.
- [x] Restoring clears optional references and links to deleted entities and reports them in `RestoreResult`.
- [x] Restoring a primary contact point when another primary of its kind exists restores it as non-primary.
- [x] `restore<Entity>` on a non-deleted or unknown id fails with `NOT_FOUND`.
- [x] `takeSnapshot` creates the next snapshot as an exact copy of the active version; afterwards `activeVersion.modified` is `false`.
- [x] `takeSnapshot` with a tag already in use fails with `CONFLICT`.
- [x] `activateVersion` makes the active version an exact copy of the snapshot; if the active version was modified, an untagged snapshot is taken first and returned as `autoSnapshot`.
- [x] No operation changes a snapshot's content.
- [x] Every query accepts `version` or `snapshot`; neither reads the active version; both fail with `VALIDATION`; unknown fails with `NOT_FOUND`.
- [x] Any mutation naming a snapshot fails with `READ_ONLY`.
- [x] Nested fields resolve in their parent's version; every entity reports its `version` (`null` for the active version).
- [x] After `activateVersion`, the active version is based on the activated snapshot and not modified; any other successful mutation except `takeSnapshot` sets `modified` to `true`.

## Implementation notes

| Module | Contents |
| --- | --- |
| `intelliw/businessdata/queries.py` | reads, returning schema models; batched reverse relations |
| `intelliw/businessdata/mutations.py` | every mutation of the active version: validation, delete / restore / reorder rules, timestamps, `modified`; domain errors carrying the API error code |
| `intelliw/businessdata/documents.py` | `take_snapshot`, `activate_version` (03-storage) |
| `intelliw/graphql/types.py` | output types derived from the Pydantic models (`strawberry.experimental.pydantic`, `strawberry.auto`); reference fields are resolvers over DataLoaders |
| `intelliw/graphql/inputs.py` | create inputs; each patch input is derived from its create input (all fields optional, `UNSET` default) |
| `intelliw/graphql/context.py` | per-request `Context`: session and DataLoaders keyed by `(version, id)` |
| `intelliw/graphql/schema.py` | `Query`, `Mutation` (the six collection mutations are generated from one table), error mapping, one transaction per mutation |
| `intelliw/graphql/server.py` | ASGI app; the workspace database comes from `WORKSPACE` |

Every resolver goes through `queries` (reads) or `mutations` / `documents` (writes); no
resolver touches the tables directly.

Names and details settled in the implementation:

- Collection names in the API: `contactPoints`, `locations`, `serviceCategories`,
  `services`, `productCategories`, `staff`, `faqs`, `socialLinks`, `affiliations`,
  `actions`, `assets`, `reviews`; entity names: `ContactPoint`, `Location`,
  `ServiceCategory`, `Service`, `ProductCategory`, `StaffMember`, `Faq`, `SocialLink`,
  `Affiliation`, `CustomerAction`, `Asset`, and `Review` for review items (so
  `updateReview`, `deleteReview`, `reorderReviews`; the by-id query is `reviewItem`).
  `ReviewPatch` has only `note`; status changes go through the review workflow mutations.
- URLs are `String`, not a custom `Url` scalar.
- `createdAt` / `updatedAt` are not input fields, so an attempt to set them is rejected by
  GraphQL document validation (a standard GraphQL error without `extensions.code`).
- After each mutation the request's DataLoaders are reset, so later fields in the same
  request see the change.
- The `hello` query from the initial stub remains, for health checks.

Tests: `tests/test_mutations.py` (mutation layer), `tests/test_graphql_api.py` (the API
end to end, including the SQL-query count of nested queries).
