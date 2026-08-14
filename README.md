# Whitby Eye Care — generated demo site

This branch holds the site produced by the webdev.ai pipeline from the reviewed
`digest.xml`, built with `injector build` from the Meridian template.

**This is a demonstration build, not the clinic's live website.**

## What is here

- `index.html`, `assets/`, `media/` — the built site, byte-for-byte what
  `uv run python -m injector build` produces. Nothing was hand-edited.
- All 15 media files came off the clinic's own existing website. No template
  demo photography is included (D-28: Meridian ships and carries no licence
  obligation; D-29: assets the customer themselves licensed may be used).

## What is NOT confirmed, and matters

The digest still carries **unresolved `needs-review` flags**, and the most
important one is the opening hours. The clinic's current site states its hours in
three places and they do not agree — Monday–Thursday opening (9am or 10am),
Tuesday closing (5pm or 7pm) and Saturday closing (3pm or 4pm) all differ between
sources. The build uses the set that two of the three sources agree on.

**Those hours are unconfirmed and must be checked with the clinic before this is
published anywhere a patient might read it.** The same applies to the booking
link, the appointment email address and the map pin, each of which is flagged in
the digest with the reason.

No flag has been resolved and no value has been marked owner-confirmed. Nothing
here was invented: every fact traces to the clinic's own site.

## Why this is a branch and not `main`

`main` is deliberately left with no commits, and GitHub Pages is **not** enabled.
Publishing a site root and a live Pages deployment for a real commissioned client
is a decision for the project owner, not something to do unattended. Deleting
this branch removes everything here.
