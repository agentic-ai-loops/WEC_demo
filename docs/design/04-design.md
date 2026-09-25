# #design templates and assets

**Status:** draft
**Depends on:** 00-architecture, 01-businessdata

## Goal

Define the template contract (context variables available to templates), and how #owners modify templates/assets.

## Scope

- In scope:
  - #design images and image slots (specified below).
  - Template contract (TBD).
- Out of scope:
  - Images of the business itself (logo, staff photos, equipment): those are
    #businessdata (01-businessdata, *Image ownership*).

## Specification

### #design images

Images that are not pictures of the business — stock photos, generic illustrations,
banners, backgrounds — belong to the #design and live in `design/assets/`. The rule for
which images go where is defined in 01-businessdata, *Image ownership*.

### Image slots

A template never hard-codes an image file. It asks for a named **slot**, and the #design
declares which image fills each slot in `design/design.json`:

```json
{
  "slots": {
    "hero":  {"image": "assets/iStock-947926156-1600x700.jpg", "alt": "Family wearing sunglasses outdoors"},
    "about": {"image": "businessdata:about-clinic"},
    "service-illustration": {
      "per": "services",
      "bindings": {
        "adult-senior-eye-exams":      {"image": "assets/adult.jpg", "alt": "Adult eye exam"},
        "children-eye-exams":          {"image": "assets/children_exam.jpg", "alt": "Child's eye exam"},
        "mto-and-police-eye-exams":    {"image": "assets/MTO_and_police_eye_exams.jpg", "alt": "Occupational vision test"},
        "contact-lens-fitting":        {"image": "assets/lens_fitting.jpg", "alt": "Contact lens fitting"},
        "computer-related-eye-strain": {"image": "assets/computer_banner.jpg", "alt": "Person working at a computer"}
      },
      "default": null
    },
    "product-category-illustration": {
      "per": "product_categories",
      "bindings": {
        "contact-lenses": {"image": "assets/Contact-Lences.jpg", "alt": "Contact lenses"},
        "sunglasses":     {"image": "assets/SunGlasses.jpg", "alt": "Sunglasses"}
      },
      "default": null
    }
  }
}
```

- **Single slots** (`hero`, `about`) hold one image.
- **Per-entity slots** (`"per": "<collection>"`) hold one image per #businessdata entity,
  keyed by its id, with an optional `default` for entities without a binding.
- **An image reference** is either a #design file (`assets/...`, relative to `design/`)
  or a #businessdata asset (`businessdata:<asset id>`), so a slot can show a real photo
  of the business — e.g. the `about` slot showing the clinic.
- **Alt text** for #design files is given in the binding. For `businessdata:` references
  it comes from the `Asset`, so there is no `alt` in the binding.
- Which slots exist is up to each #design. A different design may declare different
  slots (e.g. no `about`, or a `banner` per page).

### Template access

Templates get two helpers:

- `slot(name)` returns `{url, alt}` for a single slot, or `None` if it is unbound.
- `image_for(entity, slot_name)` returns, in order of preference:
  1. the entity's own #businessdata image (`Service.image`, `ProductCategory.image`),
  2. the per-entity binding in `slot_name`,
  3. the slot's `default`,
  4. `None`.

  So a real photo of the business always wins over a stock illustration. For example,
  Dry Eye Testing shows the clinic's own device (#businessdata); Adult & Senior Eye Exams
  shows the stock illustration (#design).

### Editing images

| #owner asks to... | Change |
| --- | --- |
| change the hero image | put the new file in `design/assets/`, then rebind `slots.hero` |
| use a real clinic photo on the About section | upload it as a #businessdata `Asset`, then bind `slots.about` to `businessdata:<id>` |
| replace a service's stock illustration | rebind `slots.service-illustration.bindings.<service id>` |
| show a real photo for a service | set `Service.image` (#businessdata); it takes precedence automatically |

The MCP tools for rebinding slots and adding #design files are specified in
06-mcp-server. #design images do not go through the #businessdata GraphQL API.

### Integrity

- Bindings to #businessdata entities that no longer exist (e.g. a deleted service) are
  ignored when rendering and reported by the design check.
- A `businessdata:` reference to a missing asset, or an `assets/` path to a missing file,
  is reported by the design check. The slot renders as unbound.

### Template contract

TBD.

## Acceptance criteria

- [ ] `design/design.json` declares slots; templates obtain images only through
      `slot()` and `image_for()`.
- [ ] `image_for()` prefers the entity's #businessdata image over the slot binding, and
      the binding over the slot default.
- [ ] A slot bound to `businessdata:<id>` renders that asset with its #businessdata alt text.
- [ ] Bindings for deleted entities and missing files are reported by the design check,
      and the affected slot renders as unbound.
- [ ] The Whitby Eye Care design places the 8 stock/illustrative digest images in
      `design/assets/`, bound as shown above.

## Implementation notes

