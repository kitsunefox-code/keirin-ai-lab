from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from keirin_ai.bankroll import build_bankroll_payload
from keirin_ai.capital_plan import build_capital_plan_payload
from keirin_ai.results_view import build_results_payload
from keirin_ai.forecast_view import build_today_forecast_payload
from keirin_ai.learner import load_model
from keirin_ai.predictor import predict_race
from keirin_ai.storage import connect, learning_status


DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "app" / "static-api"
APP_DIR = ROOT / "app"


def write_json(name: str, payload: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def stamp_asset_versions() -> str:
    """styles.css / app.js の内容ハッシュを各HTMLのリンクに付与し、
    ブラウザの古いCSSキャッシュで表示が崩れるのを防ぐ(?v=ハッシュ)。"""
    css = (APP_DIR / "styles.css").read_bytes()
    js = (APP_DIR / "app.js").read_bytes()
    ver = hashlib.sha1(css + js).hexdigest()[:8]
    for name in ("index.html", "results.html", "motion.html", "record.html", "consult.html"):
        path = APP_DIR / name
        if not path.exists():
            continue
        html = path.read_text(encoding="utf-8")
        html = re.sub(r'href="styles\.css(?:\?v=[0-9a-f]+)?"', f'href="styles.css?v={ver}"', html)
        html = re.sub(r'src="app\.js(?:\?v=[0-9a-f]+)?"', f'src="app.js?v={ver}"', html)
        path.write_text(html, encoding="utf-8")
    return ver


def write_preview(payloads: dict) -> None:
    html = (APP_DIR / "index.html").read_text(encoding="utf-8")
    css = (APP_DIR / "styles.css").read_text(encoding="utf-8")
    js = (APP_DIR / "app.js").read_text(encoding="utf-8")
    data = json.dumps(payloads, ensure_ascii=False).replace("</", "<\\/")
    bootstrap = f"""
    <script id="keirin-static-data" type="application/json">{data}</script>
    <script>
      (() => {{
        const payloads = JSON.parse(document.getElementById("keirin-static-data").textContent);
        const nativeFetch = window.fetch.bind(window);
        window.fetch = async (url, options) => {{
          const href = String(url);
          const path = href.split("?")[0];
          if (payloads[path]) {{
            return new Response(JSON.stringify(payloads[path]), {{
              status: 200,
              headers: {{ "Content-Type": "application/json; charset=utf-8" }},
            }});
          }}
          if (href.startsWith("/api/")) {{
            return new Response(JSON.stringify({{
              ok: false,
              error: "プレビュー版ではこの操作は使えません。公開用の保存済み予想を表示しています。",
            }}), {{
              status: 200,
              headers: {{ "Content-Type": "application/json; charset=utf-8" }},
            }});
          }}
          return nativeFetch(url, options);
        }};
      }})();
    </script>
    <script>{js}</script>
"""
    # ?v= 付きでも外せるよう正規表現で(2回目以降のビルド対策)。
    # 置換文字列のバックスラッシュ特殊解釈を避けるため関数で差し込む。
    style_block = f"<style>\n{css}\n</style>"
    html = re.sub(r'<link rel="stylesheet" href="styles\.css(?:\?v=[0-9a-f]+)?" />',
                  lambda _m: style_block, html)
    html = re.sub(r'<script src="app\.js(?:\?v=[0-9a-f]+)?"></script>',
                  lambda _m: bootstrap, html)
    (APP_DIR / "preview.html").write_text(html, encoding="utf-8")


def main() -> None:
    with connect() as conn:
        today = build_today_forecast_payload(conn, DATA_DIR)
        status = learning_status(conn)
        today["learning_status"] = status
        capital = build_capital_plan_payload(
            conn,
            DATA_DIR,
            start_amount=1000,
            target_amount=3000,
            max_races=2,
            live_odds=False,
            max_live_fetch=0,
        )

    sample_race = json.loads((DATA_DIR / "sample_race.json").read_text(encoding="utf-8"))
    with connect() as conn:
        bankroll = build_bankroll_payload(conn, DATA_DIR)
        results = build_results_payload(conn, None, DATA_DIR)
    model = load_model() or {}
    model_summary = {
        "name": model.get("name"),
        "backend": model.get("backend"),
        "version": model.get("version"),
        "training": model.get("training", {}),
        "metrics": model.get("metrics", {}),
        "feature_importance": model.get("feature_importance", {}),
    }
    payloads = {
        "/api/today": today,
        "/api/sample": {"ok": True, "race": sample_race, "prediction": predict_race(sample_race)},
        "/api/learn/status": {"ok": True, "status": status, "model": model_summary},
        "/api/capital_plan": capital,
        "/api/bankroll": bankroll,
        "/api/results": results,
    }
    write_json("today.json", payloads["/api/today"])
    write_json("sample.json", payloads["/api/sample"])
    write_json("learn-status.json", payloads["/api/learn/status"])
    write_json("capital-plan.json", payloads["/api/capital_plan"])
    write_json("bankroll.json", payloads["/api/bankroll"])
    write_json("results.json", payloads["/api/results"])
    write_preview(payloads)  # CSSインライン版(先に素のindex.htmlから生成)
    ver = stamp_asset_versions()  # 直リンクHTMLにキャッシュバスター付与
    print(f"asset version: {ver}")


if __name__ == "__main__":
    main()
