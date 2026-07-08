from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.learner import train_win_model
from keirin_ai.storage import connect, learning_status


def main() -> None:
    model = train_win_model()
    with connect() as conn:
        status = learning_status(conn)
    print(json.dumps({"status": status, "model": model}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
