from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.storage import connect, learning_status


def main() -> None:
    with connect() as conn:
        rows = conn.execute(
            """
            select id, url, title, word_count
            from source_documents
            where title = ''
               or title like '404 Not Found%'
               or url like '%_rss.html%'
               or word_count < 120
            order by id
            """
        ).fetchall()
        removed = [dict(row) for row in rows]
        conn.execute(
            """
            delete from source_documents
            where title = ''
               or title like '404 Not Found%'
               or url like '%_rss.html%'
               or word_count < 120
            """
        )
        conn.commit()
        status = learning_status(conn)
    print(json.dumps({"removed": removed, "status": status}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
