# Whitby Eye Care

This branch holds the site produced by the webdev.ai pipeline from the reviewed
`digest.xml`, built with `injector build` from the Meridian template.

**This is a demo build, not live website.**

## What is here

- `docs/` — the built site, byte-for-byte what `uv run python -m injector
  build` produces, served by GitHub Pages. Nothing was hand-edited.
- `site/` — the same files plus a `404.html`, and the only directory the AWS
  deploy uploads. It is a copy of `docs/`: after a rebuild, re-copy it, or
  the two will quietly disagree about what the site says.
- `scripts/fingerprint.py` — run by the workflows, never by hand. It gives
  every file under `site/assets/` and `site/media/` a name containing a hash
  of its own contents so those files can be cached for a year. It edits the
  CI checkout only; nothing here is committed with a hashed name.
- `.github/workflows/` — deploy on push to `main`, plus per-PR previews.
- All 15 media files came off the clinic's own existing website. No template
  demo photography is included (D-28: Meridian ships and carries no licence
  obligation; D-29: assets the customer themselves licensed may be used).
