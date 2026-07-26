from __future__ import annotations

"""オリジナル運用の選別ルールを実データで検証する(捏造なし・実払戻のみ)。

目的: 「毎日10レース必ず張る」現行運用がなぜ負け続けるのかを数値で示し、
選別を厳しくした場合に回収率がどう動くかを、確定オッズの実払戻だけで比較する。

前提:
- 的中判定は races.result_json の実着順のみを使う
- 払戻は races.payouts_json の確定オッズのみを使う(未取得レースは集計から除外)
- 賭け方は「1レース各100円の平坦買い」で統一し、選別ルールだけを変えて比較する
  (資金配分の巧拙を混ぜず、選別そのものの良し悪しを見るため)
"""

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

UNIT = 100  # 1点あたりの賭け金(円)

try:
    from keirin_ai.predictor import SOFTMAX_TEMPERATURE
except Exception:  # 予想モジュールが読めない環境でも集計だけは回せるように
    SOFTMAX_TEMPERATURE = 4.0


def _calibrated_probs(scores: list[float]) -> list[float]:
    """model_scoreから較正済み勝率を再計算する(表示と同じ温度付きsoftmax)。"""
    import math

    if not scores or all(s == 0 for s in scores):
        return []
    top = max(scores)
    exps = [math.exp((s - top) / SOFTMAX_TEMPERATURE) for s in scores]
    total = sum(exps)
    if total <= 0:
        return []
    return [e / total for e in exps]


def _json(raw, default=None):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def load_races(conn: sqlite3.Connection) -> list[dict]:
    """予想・実着順・確定オッズが揃ったレースだけを、検証可能な形にして返す。"""
    rows = conn.execute(
        """
        select r.race_key, r.venue, r.race_date, r.race_class_official, r.hour_type,
               r.result_json, r.payouts_json, r.lineup_json,
               p.ranking_json
        from races r
        join (
            select race_key, min(id) as id from predictions group by race_key
        ) f on f.race_key = r.race_key
        join predictions p on p.id = f.id
        where r.result_json is not null and r.payouts_json is not null
        """
    ).fetchall()

    races = []
    for row in rows:
        result = _json(row["result_json"], {}) or {}
        order = [int(c) for c in (result.get("finish_order") or []) if str(c).isdigit()]
        if len(order) < 3:
            continue
        payouts = _json(row["payouts_json"], {}) or {}
        ranking = _json(row["ranking_json"], []) or []
        cars = [int(x.get("car_no") or 0) for x in ranking]
        # 保存済み予想は較正前の確率が入っていることがあるため、model_scoreから
        # 現行の温度で再計算する(順位は変わらないので過去予想の書き換えにはならない)
        probs = _calibrated_probs([float(x.get("model_score") or 0) for x in ranking])
        if not probs:
            probs = [float(x.get("win_probability") or 0) for x in ranking]
        if len(cars) < 3 or not all(cars[:3]):
            continue
        lineup = _json(row["lineup_json"], []) or []
        races.append(
            {
                "race_key": row["race_key"],
                "venue": row["venue"] or "",
                "date": row["race_date"] or "",
                "race_class": row["race_class_official"] or "",
                "hour_type": row["hour_type"] or "",
                "cars": cars,
                "probs": probs,
                "top_prob": probs[0] if probs else 0.0,
                "order": order,
                "field_size": len(lineup) if lineup else 0,
                "exacta_odds": payouts.get("exacta"),
                "trifecta_odds": payouts.get("trifecta"),
            }
        )
    return races


def exacta_bet(race: dict) -> tuple[int, int] | None:
    """現行と同じ2車単2点(軸1着固定 × 相手上位2)。戻り値=(投資, 払戻)。"""
    cars, order = race["cars"], race["order"]
    odds = race["exacta_odds"]
    if odds is None or len(cars) < 3:
        return None
    picks = [(cars[0], cars[1]), (cars[0], cars[2])]
    actual = (order[0], order[1])
    stake = UNIT * len(picks)
    payout = int(round(odds * UNIT)) if actual in picks else 0
    return stake, payout


def summarize(name: str, races: list[dict]) -> dict:
    stake_total = payout_total = 0
    hits = 0
    for race in races:
        bet = exacta_bet(race)
        if bet is None:
            continue
        stake, payout = bet
        stake_total += stake
        payout_total += payout
        if payout > 0:
            hits += 1
    n = len([r for r in races if exacta_bet(r) is not None])
    return {
        "rule": name,
        "races": n,
        "hits": hits,
        "hit_rate": (hits / n) if n else 0.0,
        "stake": stake_total,
        "payout": payout_total,
        "profit": payout_total - stake_total,
        "roi": (payout_total / stake_total) if stake_total else 0.0,
    }


def main() -> None:
    from keirin_ai.storage import connect

    with connect() as conn:
        conn.row_factory = sqlite3.Row
        races = load_races(conn)

    if not races:
        print("検証可能なレースがありません(実着順+確定オッズが揃っていない)")
        return

    print(f"検証対象: {len(races)}レース(実着順+確定オッズが揃ったもののみ)\n")

    def show(title: str, rules: list[tuple[str, list[dict]]]) -> None:
        print(f"\n【{title}】")
        print(f"{'ルール':<30}{'R数':>6}{'的中率':>9}{'回収率':>9}{'収支':>12}")
        print("-" * 78)
        for name, subset in rules:
            res = summarize(name, subset)
            print(
                f"{res['rule']:<30}{res['races']:>6}{res['hit_rate']*100:>8.1f}%"
                f"{res['roi']*100:>8.1f}%{res['profit']:>11,}円"
            )

    show("基準", [("全レース(現行運用に近い)", races)])

    # 1. AI勝率(自信)で絞る = 現行運用が使っている考え方
    show(
        "1. AIの自信で絞る",
        [(f"AI勝率 >= {th:.0%}", [r for r in races if r["top_prob"] >= th]) for th in (0.30, 0.35, 0.40, 0.45)],
    )

    # 2. オッズ(市場評価)で絞る。人気すぎる=旨みなし、人気薄すぎ=当たらない
    show(
        "2. 確定オッズ帯で絞る",
        [
            ("オッズ 3倍未満(本命すぎ)", [r for r in races if (r["exacta_odds"] or 0) < 3]),
            ("オッズ 3〜7倍", [r for r in races if 3 <= (r["exacta_odds"] or 0) < 7]),
            ("オッズ 7〜15倍", [r for r in races if 7 <= (r["exacta_odds"] or 0) < 15]),
            ("オッズ 15倍以上(人気薄)", [r for r in races if (r["exacta_odds"] or 0) >= 15]),
        ],
    )

    # 3. 期待値(AI確率 × 実オッズ)で絞る = 理屈の上で唯一正しい選別軸
    def ev_of(r: dict) -> float:
        """買い目2点のうち本線(1着固定・相手1番手)のEV近似。"""
        if not r["exacta_odds"] or len(r["probs"]) < 2:
            return 0.0
        # Harville近似: P(a→b) = p_a × p_b / (1 - p_a)
        pa, pb = r["probs"][0], r["probs"][1]
        if pa >= 0.999:
            return 0.0
        return (pa * pb / (1 - pa)) * float(r["exacta_odds"])

    show(
        "3. 期待値(AI確率×実オッズ)で絞る",
        [(f"EV >= {th:.2f}", [r for r in races if ev_of(r) >= th]) for th in (0.8, 1.0, 1.2, 1.5)],
    )

    print("\n※ 全て1点100円の平坦買い(資金配分の巧拙を混ぜず、選別だけを比較)。")
    print("※ 競輪は控除率25%。回収率75%前後が「平均的な負け方」で、100%超が損益分岐。")
    print("※ R数が少ないルールは偶然の振れが大きい。100R未満は参考値にとどめること。")
    print("※ 2と3は結果確定後のオッズで絞っており、事前に同じ選別ができるとは限らない。")

    # --- 資金配分の検証 --------------------------------------------------
    # 選別を固定した上で「1レースに残高の何%を張るか」だけを変え、
    # 破産リスクと最終残高がどう変わるかを実際の勝敗列で追う。
    print("\n\n【4. 同じ選別・同じ勝敗で「賭け金の大きさ」だけ変えた場合】")
    picked = sorted(
        [r for r in races if r["top_prob"] >= 0.35],
        key=lambda r: (r["date"], r["race_key"]),
    )
    print(f"対象: AI勝率35%以上の {len(picked)}レースを日付順に消化(回収率72.9%の勝敗列)")
    print(f"{'1R あたり':<14}{'最終残高':>12}{'最大下落':>10}{'破産':>8}")
    print("-" * 46)
    for pct in (12, 8, 5, 3, 2, 1):
        balance = 10000.0
        peak = balance
        max_dd = 0.0
        busted = False
        for race in picked:
            # 実際の買い方に合わせる: 2点×100円が最低。100円単位に丸める
            stake = max(200, int(balance * pct / 100) // 200 * 200)
            if balance < 200:  # 最低単位すら張れない = 破産
                busted = True
                break
            stake = min(stake, int(balance) // 200 * 200)
            if stake < 200:
                busted = True
                break
            bet = exacta_bet(race)
            if bet is None:
                continue
            _, payout_at_100 = bet
            # 100円単位の結果を、実際の賭け金にスケールして再現する
            balance += (payout_at_100 / 200.0) * stake - stake
            peak = max(peak, balance)
            max_dd = max(max_dd, (peak - balance) / peak if peak > 0 else 0)
        label = "あり" if busted else "なし"
        print(f"残高の{pct:>2}%{'':<7}{int(balance):>11,}円{max_dd*100:>9.0f}%{label:>8}")
    print("\n※ 回収率が100%未満である限り、賭け金を小さくしても最終的には減る。")
    print("※ 小さく張ることは「勝てるようになる」ためではなく「長く続ける」ための手段。")


if __name__ == "__main__":
    main()
