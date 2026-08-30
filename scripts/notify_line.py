from __future__ import annotations

"""自動更新が失敗したことをLINEへ知らせる。

止まったことに気づけるのが最重要。GitHubの失敗通知メールは当てにならず、
2026-08-22〜30はヘルスチェックが8日間赤かったのに誰も気づけないまま
サイトが古い予想を配信し続けた。

環境変数:
  LINE_CHANNEL_ACCESS_TOKEN  LINE Messaging API のチャネルアクセストークン
  LINE_USER_ID               送信先のユーザーID
  RUN_URL                    失敗した実行のURL(任意)

secretsが未設定なら何もせず正常終了する。通知の設定漏れがジョブを
落とす原因になっては本末転倒なので、ここでは失敗しない。

python scripts/notify_line.py "本文"
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
ENDPOINT = "https://api.line.me/v2/bot/message/push"


def main() -> int:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    user_id = os.environ.get("LINE_USER_ID", "").strip()
    if not token or not user_id:
        print("::warning::LINEのsecretsが未設定のため通知しません")
        return 0

    headline = sys.argv[1] if len(sys.argv) > 1 else "競輪AIラボの自動更新が失敗しました"
    stamp = datetime.now(JST).strftime("%m/%d %H:%M")
    lines = [f"{headline} ({stamp})", "サイトの予想が古いままになります。"]
    run_url = os.environ.get("RUN_URL", "").strip()
    if run_url:
        lines.append(run_url)

    body = json.dumps(
        {"to": user_id, "messages": [{"type": "text", "text": "\n".join(lines)}]}
    ).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            print(f"LINE通知を送信しました (HTTP {res.status})")
        return 0
    except urllib.error.HTTPError as exc:
        # トークン失効などで通知できなくても、ジョブの失敗理由を上書きしない
        print(f"::warning::LINE通知に失敗 HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}")
        return 0
    except Exception as exc:
        print(f"::warning::LINE通知に失敗: {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
