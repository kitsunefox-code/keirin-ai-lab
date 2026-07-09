# Keirin AI Lab

競輪のAI予想アプリの研究用プロトタイプです。

この初版は、WINTICKETの公開出走表HTMLを取得して構造化し、選手コメントから心理・状態シグナルを抽出して、説明可能な予想スコアを出します。外部サイトの大量取得やログイン突破は行いません。

## 起動

```powershell
cd C:\Users\shadai15\Desktop\keirin-ai-lab
python server.py
```

ブラウザで `http://127.0.0.1:8765` を開きます。

## できること

- WINTICKETの公開出走表URLを入力して取得
- 出走表の基本指標、並び予想、短いコメントを構造化
- コメントから「上向き」「不安」「修正意識」「積極策」などを抽出
- 勝率風の確率、上位候補、3連単の候補を表示
- サンプルレースでオフライン表示
- 過去レースと着順をSQLiteに保存
- 保存した結果から勝利確率モデルを再学習

## バンクロール運用セッション

画面上部の「バンクロール運用セッション」は、元手を目標額に近づけるための資金管理補助機能です。利益を保証するものではありません。

- 元手・目標額・1レース投資上限%・1日損失上限%・連敗停止回数・EV下限を設定して開始します
- これから発走するレースを時刻順に判定し、期待値が下限未満のレースは自動で見送ります
- 買うレースは本線・抑え・妙味の複数買い目に分散します(1点勝負なし)
- 的中すると残高が増え、次のレース予算は新しい残高の上限%で再計算されます(全額転がしなし・負け後の倍賭けなし)
- 目標達成・損失上限到達・連敗数到達のいずれかで自動停止します
- 自動購入は行いません。買い目コピーと購入前確認まで対応し、購入・結果は手動で記録します

## 学習

画面の `保存` は出走表をDBに保存します。ページ内から確定結果を安全に読める場合だけ教師データにします。

画面の `結果で学習` は、着順を `3,7,1,2,5,4,6` のように入力して、そのレースを教師データとして保存し直します。

CLIでも操作できます。

```powershell
python scripts\record_result.py "https://www.winticket.jp/keirin/..." --order 3,7,1,2,5,4,6
python scripts\train_model.py
python scripts\learn_from_urls.py urls.txt --delay 2
```

学習データ:

- DB: `data\keirin_learning.sqlite3`
- 重み: `data\model_weights.json`
- ネット記事タグ: `source_documents` テーブル

## 5-10年バックフィル

まず計画ファイルを作ります。これは取得を実行せず、月ごとの公式開催日程URLと安全方針だけを作ります。

```powershell
python scripts\backfill_plan.py --years 5
python scripts\backfill_plan.py --years 10
```

作成先:

- `data\backfill\plan_2022_2026.json`

コラムやニュースは本文を丸ごと保存しません。URL、タイトル、短い抜粋、タグ、フィンガープリントだけ保存します。

```powershell
python scripts\learn_text_sources.py --url https://keirin.netkeiba.com/ --kind portal
```

## 日次運用

今日の指定時刻以降を予想してDBに保存します。

```powershell
python scripts\forecast_winticket_after.py --date 2026-07-07 --after 15:00 --max-races 80
python scripts\report_forecast.py data\today_after_1500_forecast.json
```

レース後は結果回収を回します。結果が取れたレースだけ教師データ化し、再学習します。

```powershell
python scripts\update_saved_results.py --limit 80 --delay 1
```

予想の短縮表示:

```powershell
python scripts\summarize_forecast.py data\today_after_1500_forecast.json
```

## 方針

- 予想は投資助言ではなく、研究・記録用です。
- 20歳未満の車券購入はできません。
- 車券は生活に支障のない範囲で楽しむ前提です。
- 各サイトの利用規約、著作権、アクセス負荷に配慮してください。

## 次に育てる場所

- 結果と払戻を保存して、回収率・的中率を検証する
- 公式データ、KEIRIN.JP、netkeirin、楽天Kドリームス、オッズパークなどをアダプタ化する
- コメント全文は保存せず、要約特徴量だけ残す
- 履歴が貯まったら LightGBM / XGBoost / ロジスティック回帰へ移行する
