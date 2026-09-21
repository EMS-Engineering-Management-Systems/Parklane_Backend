#!/usr/bin/env python3
"""Print every collection in the Atal database with its document count.

python -m scripts.list_collections
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.core.database import close, connect, get_db  # noqa: E402


def main() -> int:
    connect()
    db = get_db()

    names = sorted(db.list_collection_names())
    print(f"Database: {settings.mongodb_db}  ({len(names)} collections)\n")

    total = 0
    for name in names:
        count = db[name].count_documents({})
        total += count
        print(f"  {name:<48} {count:>8}")

    print(f"\n  {'TOTAL':<48} {total:>8}")
    close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
