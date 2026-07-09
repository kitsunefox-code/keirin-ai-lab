from __future__ import annotations

import json
import mimetypes
import os
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from keirin_ai.bankroll import (
    BankrollConfig,
    active_session,
    build_bankroll_payload,
    commit_bet,
    record_result,
    record_skip,
    start_session,
    stop_session,
)
from keirin_ai.capital_plan import build_capital_plan_payload
from keirin_ai.results_view import build_results_payload
from keirin_ai.forecast_view import build_today_forecast_payload
from keirin_ai.learner import load_model, train_win_model
from keirin_ai.odds import fetch_live_odds
from keirin_ai.predictor import predict_race
from keirin_ai.sources import fetch_url, parse_winticket_racecard
from keirin_ai.storage import attach_line_partner_stats, connect, learning_status, result_from_order, save_race
from keirin_ai.winticket_state import enrich_race_from_state


ROOT = Path(__file__).resolve().parent
APP_DIR = ROOT / "app"
DATA_DIR = ROOT / "data"
STATUS_LOG = ROOT / "server.status.log"


class KeirinHandler(BaseHTTPRequestHandler):
    server_version = "KeirinAILab/0.2"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            return self._serve_file(APP_DIR / "index.html")
        if path in {"/app.js", "/styles.css"}:
            return self._serve_file(APP_DIR / path.lstrip("/"))
        if path == "/api/sample":
            race = self._load_sample()
            return self._json({"ok": True, "race": race, "prediction": predict_race(race)})
        if path == "/api/today":
            return self._handle_today_forecast()
        if path == "/api/fetch":
            params = parse_qs(parsed.query)
            url = params.get("url", [""])[0].strip()
            return self._handle_fetch(url)
        if path == "/api/odds":
            params = parse_qs(parsed.query)
            url = params.get("url", [""])[0].strip()
            return self._handle_odds(url)
        if path == "/api/capital_plan":
            params = parse_qs(parsed.query)
            return self._handle_capital_plan(params)
        if path == "/api/learn/status":
            return self._handle_learning_status()
        if path == "/api/learn/fetch_store":
            params = parse_qs(parsed.query)
            url = params.get("url", [""])[0].strip()
            return self._handle_fetch_store(url)
        if path == "/api/learn/train":
            return self._handle_train()
        if path == "/api/bankroll":
            return self._handle_bankroll()
        if path == "/api/results":
            params = parse_qs(parsed.query)
            return self._handle_results(params.get("date", [""])[0].strip())

        self._send(404, "text/plain; charset=utf-8", "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/learn/manual_result":
            return self._handle_manual_result()
        if parsed.path == "/api/bankroll/start":
            return self._handle_bankroll_start()
        if parsed.path == "/api/bankroll/commit":
            return self._handle_bankroll_commit()
        if parsed.path == "/api/bankroll/result":
            return self._handle_bankroll_result()
        if parsed.path == "/api/bankroll/skip":
            return self._handle_bankroll_skip()
        if parsed.path == "/api/bankroll/stop":
            return self._handle_bankroll_stop()
        self._send(404, "text/plain; charset=utf-8", "Not found")

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _load_sample(self) -> dict:
        sample_path = DATA_DIR / "sample_race.json"
        return json.loads(sample_path.read_text(encoding="utf-8"))

    def _handle_today_forecast(self) -> None:
        try:
            with connect() as conn:
                payload = build_today_forecast_payload(conn, DATA_DIR)
                payload["learning_status"] = learning_status(conn)
            return self._json(payload)
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)}, status=502)

    def _handle_fetch(self, url: str) -> None:
        if not url:
            return self._json({"ok": False, "error": "URLが空です。"}, status=400)
        try:
            html = fetch_url(url)
            race = parse_winticket_racecard(html, url)
            race = enrich_race_from_state(race, html)
            with connect() as conn:
                attach_line_partner_stats(conn, race)
            return self._json({"ok": True, "race": race, "prediction": predict_race(race)})
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)}, status=502)

    def _handle_odds(self, url: str) -> None:
        if not url:
            return self._json({"ok": False, "error": "URLが空です"}, status=400)
        try:
            return self._json(fetch_live_odds(url))
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)}, status=502)

    def _handle_capital_plan(self, params: dict) -> None:
        try:
            start_amount = int(params.get("start", ["1000"])[0] or 1000)
            target_amount = int(params.get("target", ["3000"])[0] or 3000)
            max_races = int(params.get("max_races", ["2"])[0] or 2)
            live_odds = params.get("live_odds", ["1"])[0] not in {"0", "false", "False"}
            max_live_fetch = int(params.get("max_live_fetch", ["10"])[0] or 10)
            with connect() as conn:
                payload = build_capital_plan_payload(
                    conn,
                    DATA_DIR,
                    start_amount=start_amount,
                    target_amount=target_amount,
                    max_races=max_races,
                    live_odds=live_odds,
                    max_live_fetch=max_live_fetch,
                )
            return self._json(payload)
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)}, status=502)

    def _handle_fetch_store(self, url: str) -> None:
        if not url:
            return self._json({"ok": False, "error": "URLが空です。"}, status=400)
        try:
            html = fetch_url(url)
            race = parse_winticket_racecard(html, url)
            race = enrich_race_from_state(race, html)
            with connect() as conn:
                attach_line_partner_stats(conn, race)
                prediction = predict_race(race)
                key = save_race(conn, race, prediction)
                status = learning_status(conn)
            model = train_win_model()
            return self._json(
                {
                    "ok": True,
                    "race_key": key,
                    "has_result": bool(race.get("result")),
                    "status": status,
                    "model": model,
                    "race": race,
                    "prediction": predict_race(race),
                }
            )
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)}, status=502)

    def _handle_manual_result(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            url = str(payload.get("url") or "").strip()
            order = payload.get("order") or []
            if isinstance(order, str):
                order = [int(part.strip()) for part in order.split(",") if part.strip()]
            order = [int(car) for car in order]
            if not url or not order:
                return self._json({"ok": False, "error": "URLと着順が必要です。"}, status=400)

            html = fetch_url(url)
            race = parse_winticket_racecard(html, url)
            race["result"] = result_from_order(order, source="manual")
            prediction = predict_race(race)
            with connect() as conn:
                key = save_race(conn, race, prediction)
                status = learning_status(conn)
            model = train_win_model()
            return self._json(
                {
                    "ok": True,
                    "race_key": key,
                    "order": order,
                    "status": status,
                    "model": model,
                    "race": race,
                    "prediction": predict_race(race),
                }
            )
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)}, status=502)

    def _handle_train(self) -> None:
        try:
            model = train_win_model()
            with connect() as conn:
                status = learning_status(conn)
            return self._json({"ok": True, "status": status, "model": model})
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)}, status=502)

    def _handle_results(self, date: str) -> None:
        try:
            with connect() as conn:
                payload = build_results_payload(conn, date or None, DATA_DIR)
            return self._json(payload)
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)}, status=502)

    def _handle_bankroll(self) -> None:
        try:
            with connect() as conn:
                payload = build_bankroll_payload(conn, DATA_DIR)
            return self._json(payload)
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)}, status=502)

    def _handle_bankroll_start(self) -> None:
        try:
            body = self._read_json_body()
            style = str(body.get("style") or "").strip()
            if style:
                config = BankrollConfig.from_style(
                    style,
                    start_amount=int(body.get("start_amount") or 0),
                    target_amount=int(body.get("target_amount") or 0),
                )
            else:
                config = BankrollConfig(
                    start_amount=int(body.get("start_amount") or 0),
                    target_amount=int(body.get("target_amount") or 0),
                    per_race_cap_pct=int(body.get("per_race_cap_pct") or 20),
                    daily_loss_limit_pct=int(body.get("daily_loss_limit_pct") or 30),
                    max_consecutive_losses=int(body.get("max_consecutive_losses") or 3),
                    min_ev=float(body.get("min_ev") or 1.2),
                )
            with connect() as conn:
                start_session(conn, config)
                payload = build_bankroll_payload(conn, DATA_DIR)
            return self._json(payload)
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)}, status=502)

    def _handle_bankroll_commit(self) -> None:
        try:
            body = self._read_json_body()
            proposal = body.get("proposal") or {}
            with connect() as conn:
                session = active_session(conn)
                if not session:
                    return self._json({"ok": False, "error": "運用中のセッションがありません。"}, status=400)
                commit_bet(conn, session["id"], proposal)
                payload = build_bankroll_payload(conn, DATA_DIR)
            return self._json(payload)
        except ValueError as exc:
            return self._json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)}, status=502)

    def _handle_bankroll_result(self) -> None:
        try:
            body = self._read_json_body()
            with connect() as conn:
                record_result(
                    conn,
                    bet_id=int(body.get("bet_id") or 0),
                    outcome=str(body.get("outcome") or ""),
                    payout=int(body.get("payout") or 0),
                )
                payload = build_bankroll_payload(conn, DATA_DIR)
            return self._json(payload)
        except ValueError as exc:
            return self._json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)}, status=502)

    def _handle_bankroll_skip(self) -> None:
        try:
            body = self._read_json_body()
            with connect() as conn:
                session = active_session(conn)
                if not session:
                    return self._json({"ok": False, "error": "運用中のセッションがありません。"}, status=400)
                record_skip(conn, session["id"], body.get("race") or {}, str(body.get("reason") or "手動見送り"))
                payload = build_bankroll_payload(conn, DATA_DIR)
            return self._json(payload)
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)}, status=502)

    def _handle_bankroll_stop(self) -> None:
        try:
            body = self._read_json_body()
            with connect() as conn:
                session = active_session(conn)
                if session:
                    stop_session(conn, session["id"], str(body.get("reason") or "手動停止"))
                payload = build_bankroll_payload(conn, DATA_DIR)
            return self._json(payload)
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)}, status=502)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def _handle_learning_status(self) -> None:
        try:
            with connect() as conn:
                status = learning_status(conn)
            model = load_model() or {}
            model_summary = {
                "name": model.get("name"),
                "backend": model.get("backend"),
                "version": model.get("version"),
                "training": model.get("training", {}),
                "metrics": model.get("metrics", {}),
                "feature_importance": model.get("feature_importance", {}),
            }
            return self._json({"ok": True, "status": status, "model": model_summary})
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)}, status=502)

    def _serve_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            return self._send(404, "text/plain; charset=utf-8", "Not found")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or path.suffix in {".js", ".css"}:
            content_type += "; charset=utf-8"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send(self, status: int, content_type: str, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    host = os.environ.get("KEIRIN_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT") or os.environ.get("KEIRIN_PORT", "8765"))
    try:
        server = ThreadingHTTPServer((host, port), KeirinHandler)
        local_url = f"http://127.0.0.1:{port}"
        lan_ip = _lan_ip()
        lan_url = f"http://{lan_ip}:{port}" if lan_ip else ""
        status = f"started {local_url}"
        if host in {"0.0.0.0", "::"} and lan_url:
            status += f" lan {lan_url}"
        _status(status)
        if sys.stdout:
            print(f"Keirin AI Lab: {local_url}")
            if host in {"0.0.0.0", "::"} and lan_url:
                print(f"LAN URL: {lan_url}")
            print("Press Ctrl+C to stop.")
        server.serve_forever()
    except Exception as exc:
        _status(f"error {type(exc).__name__}: {exc}")
        raise


def _status(message: str) -> None:
    STATUS_LOG.write_text(message + "\n", encoding="utf-8")


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return ""


if __name__ == "__main__":
    main()
