"""#businessdata: fixed schema, owner-editable content.

Modules:

- `schema`    — Pydantic models: the entities, the `business.json` document, query results
- `tables`    — SQLAlchemy tables (SQLite), versioned rows with composite foreign keys
- `database`  — engine / session setup
- `queries`   — read access returning `schema` models, batched for GraphQL resolvers
- `documents` — whole-document import/export between `BusinessData` and the database
- `store`     — `business.json` file load/save
"""

from intelliw.businessdata.schema import BusinessData
from intelliw.businessdata.store import load, save

__all__ = ["BusinessData", "load", "save"]
