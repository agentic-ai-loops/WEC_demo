# Design documents

Each file in `design/` describes one component or feature and is written so it
can be handed to a coding agent verbatim as a prompt for incremental work.

Conventions:

- One document per component/feature, numbered in dependency order (`NN-name.md`).
- Start from `TEMPLATE.md`.
- Use the `#tags` from `AGENT.md` (`#owner`, `#businessdata`, `#design`, `#site`)
  consistently so documents compose cleanly as context.
- Keep the **Status** field current (`draft` → `ready` → `implemented`).
- When a document is implemented, record the key modules/tests under **Implementation notes**.
