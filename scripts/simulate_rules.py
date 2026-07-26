from __future__ import annotations

"""修正前後の運用ルールを、同じ過去データ・同じ実結果で比較する。

「同じAI・同じ着順・同じ確定オッズ」のまま、運用ルールだけを差し替えたら
損失がどう変わるかを見る。AIを賢くする話ではなく、負け方を穏やかにする話。

旧ルール: 信頼度のみで10レース確定(足りなければ次点で水増し)/1R 残高の12%
          /損失上限は決済後にラベルを付けるだけで実際には止まらない
新ルール: 信頼度 + AI勝率35%以上のみ(足りなければ少ないまま)/1R 残高の5%
          /損失上限に達したらその日はそこで打ち切り
"""

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backtest_strategies import exacta_bet, load_races  # noqa: E402

START = 10000
UNIT = 100


def run(days: dict[str, list[dict]], *, min_prob: float, cap_pct: int, enforce_limit: bool, pad_to: int | None) -> dict:
    """実装に合わせ、毎朝1万円でセッションを開始し直す(日ごとに独立)。

    日中は複利で回し、その日の損失が上限に達したらそこで打ち切る。
    通算収支は各日の損益の合計。
    """
    loss_limit = START * 0.25
    daily = []
    total_stake = 0
    bets = wins = 0
    for date in sorted(days):
        races = days[date]
        balance = float(START)  # 毎朝リセット
        # --- レース選定 ---
        picked = [r for r in races if r["top_prob"] >= min_prob]
        picked.sort(key=lambda r: -r["top_prob"])
        picked = picked[:10]
        if pad_to and len(picked) < pad_to:
            # 旧ルール: 枠を埋めるため基準未満のレースを足していた
            rest = sorted([r for r in races if r not in picked], key=lambda r: -r["top_prob"])
            picked += rest[: pad_to - len(picked)]
        picked.sort(key=lambda r: r["race_key"])

        day_start = balance
        for race in picked:
            if enforce_limit and (day_start - balance) >= loss_limit:
                break  # 損失上限に到達 → その日はここで打ち切り
            if balance < 200:
                break
            stake = max(200, int(balance * cap_pct / 100) // 200 * 200)
            stake = min(stake, int(balance) // 200 * 200)
            if stake < 200:
                break
            bet = exacta_bet(race)
            if bet is None:
                continue
            _, payout_at_100 = bet
            balance += (payout_at_100 / 200.0) * stake - stake
            total_stake += stake
            bets += 1
            if payout_at_100 > 0:
                wins += 1
        daily.append(balance - day_start)
    return {
        "profit": sum(daily),
        "daily": daily,
        "bets": bets,
        "wins": wins,
        "total_stake": total_stake,
        "worst_day": min(daily) if daily else 0,
        "days_over_limit": sum(1 for d in daily if d < -loss_limit),
        "days": len(daily),
    }


def main() -> None:
    from keirin_ai.storage import connect

    with connect() as conn:
        conn.row_factory = sqlite3.Row
        races = load_races(conn)

    days: dict[str, list[dict]] = defaultdict(list)
    for race in races:
        if race["date"]:
            days[race["date"]].append(race)

    old = run(days, min_prob=0.0, cap_pct=12, enforce_limit=False, pad_to=10)
    new = run(days, min_prob=0.35, cap_pct=5, enforce_limit=True, pad_to=None)

    print(f"対象: {len(days)}日 / {len(races)}レース(実着順+確定オッズが揃ったもの)")
    print("毎朝1万円でセッション開始(実装どおり)。通算収支は各日の損益の合計\n")
    print(f"{'':<22}{'旧ルール':>14}{'新ルール':>14}")
    print("-" * 50)
    rows = [
        ("張ったレース数", f"{old['bets']:,}R", f"{new['bets']:,}R"),
        ("投資総額", f"{old['total_stake']:,}円", f"{new['total_stake']:,}円"),
        ("的中率", f"{old['wins']/max(1,old['bets'])*100:.1f}%", f"{new['wins']/max(1,new['bets'])*100:.1f}%"),
        ("通算収支", f"{int(old['profit']):,}円", f"{int(new['profit']):,}円"),
        ("1日平均の収支", f"{int(old['profit']/max(1,old['days'])):,}円", f"{int(new['profit']/max(1,new['days'])):,}円"),
        ("1日の最大損失", f"{int(old['worst_day']):,}円", f"{int(new['worst_day']):,}円"),
        ("上限超えの日数", f"{old['days_over_limit']}日", f"{new['days_over_limit']}日"),
        ("勝った日", f"{sum(1 for d in old['daily'] if d>0)}日", f"{sum(1 for d in new['daily'] if d>0)}日"),
    ]
    for label, a, b in rows:
        print(f"{label:<22}{a:>14}{b:>14}")

    print("\n※ 新ルールでも回収率は100%未満のため、長期では減り続ける。")
    print("※ 改善するのは『減る速さ』と『1日で溶かす額』であって、勝てるようになるわけではない。")


if __name__ == "__main__":
    main()
