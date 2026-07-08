# Deploy

PCを止めても見られるようにするには、このアプリをクラウドのWebサービスへ配置します。

## Renderで公開する

1. このフォルダをGitHubリポジトリへアップロードする。
2. Renderで「New Web Service」を作る。
3. GitHubリポジトリを選ぶ。
4. Build Command: `pip install -r requirements.txt`
5. Start Command: `python server.py`
6. Environment Variable: `KEIRIN_HOST=0.0.0.0`
7. Deployする。

Renderは自動で `PORT` を渡します。`server.py` は `PORT` に対応済みです。

## 注意

- 無料枠は一定時間アクセスがないとスリープする場合があります。
- ライブオッズ取得は外部サイトへアクセスするため、クラウド側の通信制限や対象サイトの制限で失敗することがあります。
- DBや学習結果をクラウドで更新し続けたい場合は、永続ディスク付きプランにして `data/` を永続化してください。
