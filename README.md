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

画面上部の「バンクロール運用」は、元手を目標額に近づけるための資金管理補助機能です。利益を保証するものではありません。

- 運用スタイルを「堅実 / バランス / 冒険」から選びます。スタイルが1レース上限%・損失上限%・連敗停止・EV下限・買い目配分をまとめて決めます
  - 堅実: 混戦レースは見送り、本線厚めの2点。上限10%/損失20%/2連敗停止/EV1.3
  - バランス: 本線・抑え・妙味の3点分散。上限20%/損失30%/3連敗停止/EV1.2
  - 冒険: 妙味厚めの3点で高配当狙い。上限30%/損失40%/4連敗停止/EV1.1
- 元手と目標額を入れると「何レースで達成できそうか」の目安(本線好調ペース〜EV下限ペース)が出ます
- これから発走するレースを時刻順に判定し、期待値が下限未満のレースは自動で見送ります
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

今日の予想を作ってDBに保存します(ファイル名に日付を入れると画面が最新日付を選びます)。

```powershell
python scripts\forecast_winticket_after.py --date 2026-07-09 --after 09:00 --max-races 50 --out data\forecast_20260709_after_0900.json
```

レース後はWINTICKETのraceresultページから、確定結果・決まり手・レース後インタビューをまとめて回収します。談話は選手ごとに蓄積され(player_formテーブル)、翌日以降の予想で「前走後の談話」として評価に使われます。

```powershell
python scripts\collect_raceresults.py --limit 100 --delay 0.6
```

WINTICKETに結果が出ないレースはKEIRIN.JP公式で補完できます。

```powershell
python scripts\backfill_keirinjp_results.py --limit 120 --delay 0.4
```

画面の「結果・答え合わせ」で日付ごとに、AIの本命・買い目と実際の着順、本命的中/車券圏/3連単的中のバッジ、当日の的中率が確認できます。

## 予想が読んでいる材料

- 出走表の基本指標(得点・勝率・バック数・脚質・ライン)
- 前検日コメント(短評)と前検日インタビュー全文の心理・状態語
- 前走レース後インタビュー(蓄積した談話)
- EXデータ(スパート/突き抜け/奪取/置かれ/ライン分断/競り)
- 直近開催の着順(直近3着内率・平均着順)
- 保存済みレース結果からのオンライン学習重み

レースカードの「▶ 展開を再生」で、打鐘から最終バック、直線、ゴールまでの展開予想をモーションで再生できます。

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
