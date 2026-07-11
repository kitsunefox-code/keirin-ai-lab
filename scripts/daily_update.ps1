# Keirin AI Lab 日次自動更新
#   -Mode morning : 今日の予想を生成 → 静的ビルド → push (公開版が今日の予想になる)
#   -Mode night   : 結果・レース後談話を回収+再学習 → 静的ビルド → push (答え合わせが最新になる)
#   -Mode push    : 静的ビルド → push のみ (動作確認用)
#   -Mode live    : 日中の速報反映 (直近結果+払戻+決済 → 静的ビルド → push)
# タスクスケジューラから毎日実行される。ログは data\auto_update.log。
param(
    [ValidateSet("morning", "night", "push", "live")]
    [string]$Mode = "push"
)

$ErrorActionPreference = "Continue"
$repo = "C:\Users\shadai15\Desktop\keirin-ai-lab"
Set-Location $repo
$log = Join-Path $repo "data\auto_update.log"

function Write-Log([string]$message) {
    $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Mode, $message
    Add-Content -Path $log -Value $line -Encoding utf8
}

Write-Log "start"

if ($Mode -eq "morning") {
    # 夜間タスクが走れなかった日でも、まず前夜分の結果と談話を回収してから予想する
    python scripts\collect_raceresults.py --limit 150 --delay 0.5 | Out-Null
    Write-Log "results catch-up (exit $LASTEXITCODE)"
    python scripts\backfill_payouts.py --limit 200 --delay 0.4 | Out-Null
    Write-Log "payouts catch-up (exit $LASTEXITCODE)"
    # 前日のオリジナル運用を(停止する前に)実結果で決済して締める
    python scripts\settle_original.py | Out-Null
    Write-Log "yesterday settled (exit $LASTEXITCODE)"
    $today = Get-Date -Format "yyyy-MM-dd"
    $stamp = Get-Date -Format "yyyyMMdd"
    python scripts\forecast_winticket_after.py --date $today --after 00:00 --max-races 150 --delay 0.4 --out "data\forecast_${stamp}_after_0000.json" | Out-Null
    Write-Log "forecast generated (exit $LASTEXITCODE)"
    # オリジナル運用(株式運用型)を毎日自動開始
    python scripts\start_original.py | Out-Null
    Write-Log "original session auto-start (exit $LASTEXITCODE)"
    python scripts\backfill_player_profiles.py --limit 250 --delay 0.5 | Out-Null
    Write-Log "player profiles (exit $LASTEXITCODE)"
}
elseif ($Mode -eq "night") {
    python scripts\collect_raceresults.py --limit 120 --delay 0.6 | Out-Null
    Write-Log "results collected (exit $LASTEXITCODE)"
    python scripts\backfill_keirinjp_results.py --limit 60 --delay 0.5 | Out-Null
    Write-Log "keirinjp fallback done (exit $LASTEXITCODE)"
    python scripts\backfill_payouts.py --limit 200 --delay 0.4 | Out-Null
    Write-Log "payouts backfilled (exit $LASTEXITCODE)"
    python scripts\settle_original.py | Out-Null
    Write-Log "original settled (exit $LASTEXITCODE)"
}
elseif ($Mode -eq "live") {
    # 日中の速報反映: 直近の結果を取り込み、払戻・決済を更新してから公開(軽量・再学習なし)
    python scripts\collect_raceresults.py --limit 50 --delay 0.4 | Out-Null
    Write-Log "live results (exit $LASTEXITCODE)"
    python scripts\backfill_payouts.py --limit 80 --delay 0.3 | Out-Null
    Write-Log "live payouts (exit $LASTEXITCODE)"
    python scripts\settle_original.py | Out-Null
    Write-Log "live settled (exit $LASTEXITCODE)"
}

python scripts\build_static_api.py | Out-Null
Write-Log "static build (exit $LASTEXITCODE)"

git add -A 2>&1 | Out-Null
$pending = git status --porcelain
if ($pending) {
    git commit -m "Auto update ($Mode) $(Get-Date -Format 'yyyy-MM-dd HH:mm')" 2>&1 | Out-Null
    # クラウド(GitHub Actions)側の更新を取り込んでからpush(衝突回避)
    git pull --rebase origin main 2>&1 | Out-Null
    git push origin main 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Log "pushed"
    } else {
        Write-Log "push FAILED (exit $LASTEXITCODE)"
    }
} else {
    Write-Log "no changes"
}

Write-Log "done"
