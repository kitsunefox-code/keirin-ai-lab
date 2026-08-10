from __future__ import annotations

"""市場(オッズ)の値付けの歪みを探す研究スクリプト。

これまでの「1着になる車を当てにいく」発想をやめ、
「市場が過小評価している買い目を探す」発想で検証する。

考え方:
- 全通りの2車単オッズから、市場が織り込んでいる確率を復元する
    market_p(i,j) ∝ 1 / odds(i,j)  を合計1に正規化(=控除率を除いた市場の本音)
- AIの較正済み勝率からHarville近似で自分の確率を出す
    our_p(i,j) = p_i × p_j / (1 - p_i)
- 期待値 EV = our_p × オッズ が1を超える買い目だけを買う

重要な前提:
- 判断は「発走前スナップショットのオッズ」で行う(latest_odds_json)
- 精算は「確定オッズ」で行う(payouts_json)。日本の公営競技は
  パリミュチュエル方式なので、買った時点のオッズではなく確定オッズで払い戻される
- 過学習を避けるため、日付で前半(学習)と後半(検証)に分けて両方を報告する
"""

import json
import math
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

UNIT = 100

try:
    from keirin_ai.predictor import SOFTMAX_TEMPERATURE
except Exception:
    SOFTMAX_TEMPERATURE = 4.0


def _json(raw, default=None):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _calibrated(scores: list[float]) -> list[float]:
    if not scores or all(s == 0 for s in scores):
        return []
    top = max(scores)
    exps = [math.exp((s - top) / SOFTMAX_TEMPERATURE) for s in scores]
    total = sum(exps)
    return [e / total for e in exps] if total > 0 else []


def load(conn: sqlite3.Connection, *, max_minutes_to_post: int | None = None, source: str = "any") -> list[dict]:
    """研究用データを読み出す。

    source:
      "pre"   発走前に取得したスナップショットだけを使う(実運用に近い)
      "final" 確定後に取り込んだ全通りオッズだけを使う(過去分の一括取込)
      "any"   両方。発走前があればそちらを優先する

    max_minutes_to_post を指定すると、発走までの残り時間がその範囲内で
    最も締切に近い1枚(=市場の最終結論に近いオッズ)だけを使う。

    ⚠️ "final" は買う時点では分からない値なので、これで買い目を選ぶ検証は
    現実より有利に出る。実運用の判断材料にはしないこと。
    確定後の取込は minutes_to_post = -1 で目印を付けてある。
    """
    where = []
    params: list = []
    if source == "pre":
        where.append("minutes_to_post is not null and minutes_to_post >= 0")
    elif source == "final":
        where.append("minutes_to_post = -1")
    if max_minutes_to_post is not None:
        where.append("minutes_to_post is not null and minutes_to_post >= 0 and minutes_to_post <= ?")
        params.append(max_minutes_to_post)
    cond = ("where " + " and ".join(where)) if where else ""

    # 1レース1枚を選ぶ。発走前のものを優先し、その中では締切に近い順。
    # 確定後の取込(-1)は最後に回す。
    pick = f"""
        select race_key, exacta_json,
               row_number() over (
                   partition by race_key
                   order by case when minutes_to_post is null then 2
                                 when minutes_to_post < 0 then 1
                                 else 0 end,
                            minutes_to_post asc, taken_at desc
               ) as rn
        from odds_snapshots
        {cond}
    """

    rows = conn.execute(
        f"""
        with board as ({pick})
        select r.race_key, r.venue, r.race_date, r.race_class_official,
               r.result_json, r.payouts_json, board.exacta_json,
               p.ranking_json
        from races r
        join (select race_key, min(id) as id from predictions group by race_key) f
          on f.race_key = r.race_key
        join predictions p on p.id = f.id
        join board on board.race_key = r.race_key and board.rn = 1
        where r.payouts_json is not null
          and r.result_json is not null
        """,
        tuple(params),
    ).fetchall()

    races = []
    for row in rows:
        order = [int(c) for c in ((_json(row["result_json"], {}) or {}).get("finish_order") or []) if str(c).isdigit()]
        if len(order) < 2:
            continue
        board = _json(row["exacta_json"], []) or []
        odds_map: dict[tuple[int, int], float] = {}
        for item in board:
            key = str(item.get("key") or "")
            odd = float(item.get("odds") or 0)
            if "-" not in key or odd <= 0 or odd >= 9999:
                continue
            a, b = key.split("-", 1)
            if a.isdigit() and b.isdigit():
                odds_map[(int(a), int(b))] = odd
        if len(odds_map) < 10:
            continue

        ranking = _json(row["ranking_json"], []) or []
        cars = [int(x.get("car_no") or 0) for x in ranking]
        probs = _calibrated([float(x.get("model_score") or 0) for x in ranking])
        if not probs:
            probs = [float(x.get("win_probability") or 0) for x in ranking]
        if len(cars) < 3 or len(probs) != len(cars):
            continue

        payouts = _json(row["payouts_json"], {}) or {}
        final_odds = payouts.get("exacta")
        if not final_odds:
            continue

        # 市場の織り込み確率(控除率を除いて合計1に正規化)
        inv_total = sum(1.0 / o for o in odds_map.values())
        market = {k: (1.0 / o) / inv_total for k, o in odds_map.items()}

        # 自分の確率(Harville近似)
        pmap = dict(zip(cars, probs))
        ours: dict[tuple[int, int], float] = {}
        for i in cars:
            pi = pmap.get(i, 0.0)
            if pi <= 0 or pi >= 0.999:
                continue
            for j in cars:
                if i == j:
                    continue
                ours[(i, j)] = pi * pmap.get(j, 0.0) / (1.0 - pi)

        races.append(
            {
                "race_key": row["race_key"],
                "date": row["race_date"] or "",
                "venue": row["venue"] or "",
                "winner": (order[0], order[1]),
                "final_odds": float(final_odds),
                "odds": odds_map,
                "market": market,
                "ours": ours,
                "overround": inv_total,  # 1.0を超えるほど控除率が乗っている
            }
        )
    races.sort(key=lambda r: (r["date"], r["race_key"]))
    return races


def backtest(races: list[dict], *, min_ev: float, max_picks: int, min_odds: float, max_odds: float) -> dict:
    stake = payout = 0
    bets = hits = 0
    returns: list[int] = []  # 1点ごとの払戻(ブートストラップ用)
    for race in races:
        cands = []
        for combo, odd in race["odds"].items():
            if not (min_odds <= odd <= max_odds):
                continue
            our_p = race["ours"].get(combo, 0.0)
            ev = our_p * odd
            if ev >= min_ev:
                cands.append((ev, combo))
        cands.sort(reverse=True)
        for _ev, combo in cands[:max_picks]:
            stake += UNIT
            bets += 1
            if combo == race["winner"]:
                # 精算は確定オッズ(パリミュチュエルのため買った時のオッズでは払われない)
                got = int(round(race["final_odds"] * UNIT))
                payout += got
                hits += 1
                returns.append(got)
            else:
                returns.append(0)
    return {
        "bets": bets,
        "hits": hits,
        "hit_rate": hits / bets if bets else 0.0,
        "stake": stake,
        "payout": payout,
        "profit": payout - stake,
        "roi": payout / stake if stake else 0.0,
        "races": len(races),
        "returns": returns,
    }


def roi_interval(returns: list[int], trials: int = 2000) -> tuple[float, float]:
    """ブートストラップで回収率の95%信頼区間を出す。

    的中率が数%で配当が大きい買い方は、たまたま数本当たっただけで
    回収率が100%を超えて見える。区間の下限が100%を割るなら
    「勝てる証拠」とは言えない。
    """
    import random

    if not returns:
        return (0.0, 0.0)
    rnd = random.Random(20260725)  # 再現性のため固定
    n = len(returns)
    rois = []
    for _ in range(trials):
        total = sum(returns[rnd.randrange(n)] for _ in range(n))
        rois.append(total / (n * UNIT))
    rois.sort()
    return (rois[int(trials * 0.025)], rois[int(trials * 0.975)])


def show(title: str, rows: list[tuple[str, dict]]) -> None:
    print(f"\n【{title}】")
    print(f"{'条件':<26}{'点数':>7}{'的中率':>9}{'回収率':>9}{'収支':>11}")
    print("-" * 64)
    for name, res in rows:
        print(
            f"{name:<26}{res['bets']:>7}{res['hit_rate']*100:>8.1f}%"
            f"{res['roi']*100:>8.1f}%{res['profit']:>10,}円"
        )


def main() -> None:
    import argparse

    from keirin_ai.storage import connect

    ap = argparse.ArgumentParser(description="市場の値付けに歪みがあるかを検証する。")
    ap.add_argument(
        "--source",
        default="any",
        choices=["any", "pre", "final"],
        help="pre=発走前のみ / final=確定後の一括取込のみ / any=両方(発走前優先)",
    )
    args = ap.parse_args()

    with connect() as conn:
        conn.row_factory = sqlite3.Row
        races = load(conn, source=args.source)
    print(f"オッズの出所: {args.source}")

    if len(races) < 50:
        print(f"検証可能なレースが少なすぎます({len(races)}R)")
        return

    avg_or = sum(r["overround"] for r in races) / len(races)
    print(f"検証対象: {len(races)}レース(全通りオッズ+確定オッズ+結果が揃ったもの)")
    print(f"期間: {races[0]['date']} 〜 {races[-1]['date']}")
    print(f"市場の平均オーバーラウンド: {avg_or:.3f} (=控除率およそ {(1-1/avg_or)*100:.1f}%)")

    # 日付で前半/後半に分割。後半は「ルールを決めた後に初めて見るデータ」として扱う
    cut = int(len(races) * 0.7)
    train, test = races[:cut], races[cut:]
    print(f"\n学習側(前半): {len(train)}R / 検証側(後半): {len(test)}R")

    base = dict(max_picks=3, min_odds=2.0, max_odds=50.0)
    show(
        "1. 期待値のしきい値(全期間)",
        [(f"EV >= {ev:.2f}", backtest(races, min_ev=ev, **base)) for ev in (1.0, 1.1, 1.2, 1.3, 1.5)],
    )

    show(
        "2. 同じ条件を前半/後半で比較(過学習チェック)",
        [
            (f"前半 EV>={ev:.1f}", backtest(train, min_ev=ev, **base)) for ev in (1.0, 1.2, 1.5)
        ]
        + [
            (f"後半 EV>={ev:.1f}", backtest(test, min_ev=ev, **base)) for ev in (1.0, 1.2, 1.5)
        ],
    )

    show(
        "3. オッズ帯を絞る(EV>=1.2固定・全期間)",
        [
            ("2〜10倍", backtest(races, min_ev=1.2, max_picks=3, min_odds=2, max_odds=10)),
            ("10〜30倍", backtest(races, min_ev=1.2, max_picks=3, min_odds=10, max_odds=30)),
            ("30〜100倍", backtest(races, min_ev=1.2, max_picks=3, min_odds=30, max_odds=100)),
            ("2〜100倍(全部)", backtest(races, min_ev=1.2, max_picks=3, min_odds=2, max_odds=100)),
        ],
    )

    print("\n【4. 後半(検証側)の結果は偶然で説明がつくか】")
    print("回収率の95%信頼区間をブートストラップで算出。下限が100%を割れば「勝てる証拠」とは言えない。")
    print(f"{'条件':<26}{'回収率':>9}{'95%信頼区間':>22}{'判定':>10}")
    print("-" * 70)
    for ev in (1.0, 1.2, 1.5):
        res = backtest(test, min_ev=ev, **base)
        lo, hi = roi_interval(res["returns"])
        verdict = "有意" if lo > 1.0 else "偶然の範囲"
        print(
            f"{f'後半 EV>={ev:.1f}':<26}{res['roi']*100:>8.1f}%"
            f"{f'{lo*100:.0f}% 〜 {hi*100:.0f}%':>22}{verdict:>10}"
        )

    print("\n※ 精算は確定オッズ。判断は発走前スナップショットのオッズ。")
    print("※ 点数が少ない条件は偶然の振れが大きい。最低でも300点は欲しい。")
    print("※ 前半で良くても後半で崩れるなら、それは過学習であって発見ではない。")


if __name__ == "__main__":
    main()
