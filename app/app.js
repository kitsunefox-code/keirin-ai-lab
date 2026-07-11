const state = {
  today: null,
  bundle: null,
  activeTab: "today",
  learning: null,
  learnedModel: null,
  bankroll: null,
  bankrollStyle: null,
  bankrollAutoTimer: null,
  results: null,
  motionTimers: {},
};

const STATIC_API = {
  "/api/today": "static-api/today.json",
  "/api/sample": "static-api/sample.json",
  "/api/learn/status": "static-api/learn-status.json",
  "/api/bankroll": "static-api/bankroll.json",
  "/api/results": "static-api/results.json",
  "/api/players": "static-api/players.json",
};

// 公開版の静的JSONはブラウザにキャッシュされやすい。ページ読込ごとに必ず取り直す。
const CACHE_BUST = String(Date.now());

const el = (id) => document.getElementById(id);

function on(id, event, handler) {
  const node = el(id);
  if (node) node.addEventListener(event, handler);
}

async function apiGet(url) {
  try {
    const res = await fetch(url);
    if (res.ok) {
      return res;
    }
    throw new Error(`HTTP ${res.status}`);
  } catch (error) {
    const path = url.split("?")[0];
    const fallback = STATIC_API[path];
    if (!fallback) {
      throw error;
    }
    const busted = fallback + (fallback.includes("?") ? "&" : "?") + "v=" + CACHE_BUST;
    return fetch(busted, { cache: "no-store" });
  }
}

async function apiPost(url, options) {
  try {
    const res = await fetch(url, options);
    if (res.ok) {
      return res;
    }
    throw new Error(`HTTP ${res.status}`);
  } catch (error) {
    return {
      ok: true,
      json: async () => ({
        ok: false,
        error: "公開版では保存や手動学習は使えません。ローカル版で実行してください。",
      }),
    };
  }
}

document.addEventListener("DOMContentLoaded", () => {
  on("refreshTodayBtn", "click", loadToday);
  on("confidenceFilter", "change", renderToday);
  document.addEventListener("click", (event) => {
    const btn = event.target.closest(".motion-btn");
    if (btn) toggleRaceMotion(btn.dataset.race);
  });
  // 選手ページへのリンクはカード/ボタンのクリック処理より優先(中のリンクだけ反応)
  document.addEventListener(
    "click",
    (event) => {
      const link = event.target.closest("a.player-link, a.line-flat-member");
      if (link) event.stopPropagation();
    },
    true
  );
  const page = document.body.dataset.page || "home";
  if (page === "home") {
    // 読み込み中のスケルトン(体感速度の底上げ)
    const list = el("forecastList");
    if (list) list.innerHTML = Array.from({ length: 4 }, () => '<div class="skeleton-row"></div>').join("");
    loadLearnStatus();
    loadToday();
    loadBankroll();
    loadResults(); // AI成績バー用
  } else if (page === "results") {
    loadResults();
    loadBankroll(); // オリジナル運用の日次収支用
    // ライブ更新: 結果・回収率・収支を60秒ごとに取り直す
    setInterval(() => {
      const active = state.results?.date;
      loadResults(active);
      loadBankroll();
    }, 60000);
  } else if (page === "motion") {
    loadToday(); // モーションメーカーの素材
  } else if (page === "record") {
    loadResults(); // 実績ページ(record/conditions/calibrationを使う)
  } else if (page === "consult") {
    loadToday(); // スタイル別コンサルの素材(本日の予想)
    loadBankroll(); // オリジナル運用(自動)の状況
  } else if (page === "player") {
    loadPlayer();
  }
  renderOnboarding(page);
});

async function loadToday() {
  setStatus("今日の予想を読み込み中");
  try {
    const res = await apiGet("/api/today");
    const payload = await res.json();
    if (!payload.ok) {
      setStatus(payload.error || "今日の予想を読み込めませんでした");
      return;
    }
    state.today = payload;
    state.learning = payload.learning_status || state.learning;
    renderToday();
    renderLearn();
    setStatus(`${payload.summary?.count || 0}レースを表示中`);
  } catch (error) {
    setStatus(`今日の予想エラー: ${error.message}`);
  }
}

async function loadLearnStatus() {
  try {
    const res = await apiGet("/api/learn/status");
    const payload = await res.json();
    if (payload.ok) {
      state.learning = payload.status;
      if (payload.model) state.learnedModel = payload.model;
      renderLearn();
    }
  } catch {
    state.learning = null;
  }
}

function currentPlayerId() {
  return new URLSearchParams(window.location.search).get("id") || "";
}

async function loadPlayer() {
  const body = el("playerBody");
  const pid = currentPlayerId();
  if (!pid) {
    if (body) body.innerHTML = `<div class="empty">選手が指定されていません。レース一覧の選手名から開いてください。</div>`;
    return;
  }
  try {
    const res = await apiGet("/api/players");
    const payload = await res.json();
    if (!payload.ok) {
      if (body) body.innerHTML = `<div class="empty">${escapeHtml(payload.error || "選手情報を読み込めませんでした。")}</div>`;
      return;
    }
    state.players = payload.players || {};
    const player = state.players[pid];
    if (!player) {
      if (body) body.innerHTML = `<div class="empty">この選手のデータはまだありません(未出走、または直近データ収集前の可能性)。</div>`;
      el("playerName").textContent = "選手が見つかりません";
      return;
    }
    renderPlayerPage(player);
  } catch (error) {
    if (body) body.innerHTML = `<div class="empty">選手情報エラー: ${escapeHtml(error.message)}</div>`;
  }
}

function renderPlayerPage(p) {
  const nameEl = el("playerName");
  const metaEl = el("playerMeta");
  const body = el("playerBody");
  if (!body) return;
  document.title = `${p.name || "選手"} | Keirin AI Lab`;
  if (nameEl) nameEl.textContent = `${p.name || "選手"}${rookieBadge(p.term)}`;
  if (nameEl) nameEl.innerHTML = `${escapeHtml(p.name || "選手")}${rookieBadge(p.term)}${classMoveBadge(p.class_move)}`;
  if (metaEl) metaEl.textContent = `${p.prefecture || ""} / ${p.term ? `${p.term}期` : ""} / ${p.class || ""}${p.style ? " / " + p.style : ""}`;

  const kpis = [
    { label: "現在の得点", value: p.score_now != null ? num(p.score_now) : "—", sub: "今期適用", cls: "" },
    { label: "直近4ヶ月得点", value: p.recent_official?.score != null ? num(p.recent_official.score) : "—", sub: "JKA公式", cls: "" },
    { label: "調子", value: p.form ? p.form.label : "—", sub: "実戦データ判定", cls: p.form?.label === "好調" ? "kpi-up" : p.form?.label === "不調" ? "kpi-down" : "" },
    { label: "直近勝率(公式)", value: p.recent_official?.win_rate != null ? `${p.recent_official.win_rate}%` : "—", sub: "直近4ヶ月", cls: "" },
  ];
  const kpiHtml = `<div class="kpi-row">${kpis.map((k) => `<div class="kpi-card"><span>${k.label}</span><strong class="${k.cls}">${escapeHtml(String(k.value))}</strong><em>${k.sub}</em></div>`).join("")}</div>`;

  const todayHtml = p.today
    ? `<section class="surface-panel">
        <div class="capital-head"><div><h3>本日の出走</h3></div><span class="page-meta">${escapeHtml(p.today.venue || "")}${escapeHtml(p.today.race_no || "")}R ${escapeHtml(p.today.start_time || "")}</span></div>
        <p class="consult-lead">AI勝率 <b>${percent(p.today.win_probability)}</b>${p.today.comment ? ` / コメント「${escapeHtml(p.today.comment)}」` : ""}</p>
        <a class="button" href="index.html#${safeRaceId(p.today.race_key)}" data-jump-race="${escapeAttr(p.today.race_key)}">このレースの予想を見る →</a>
      </section>`
    : "";

  const trendRows = (p.score_trend || []).slice(-12);
  const trendHtml = trendRows.length
    ? `<section class="surface-panel">
        <div class="capital-head"><div><h3>競走得点の推移</h3></div><span class="page-meta">当ラボ収集分・直近${trendRows.length}件</span></div>
        ${sparkline(trendRows.map((r) => Number(r.score) || 0))}
      </section>`
    : "";

  const finishRows = (p.finishes || []).slice(-15).reverse();
  const finishHtml = finishRows.length
    ? `<section class="surface-panel">
        <div class="capital-head"><div><h3>直近の着順</h3></div><span class="page-meta">新しい順・全${finishRows.length}走</span></div>
        <div class="table-wrap"><table class="data-table compact-table">
          <thead><tr><th>日付</th><th>開催</th><th>級班</th><th>着順</th></tr></thead>
          <tbody>${finishRows
            .map(
              (r) => `<tr>
                <td>${escapeHtml(shortDate(r.race_date) || r.race_date || "")}</td>
                <td>${escapeHtml(r.venue || "")}${escapeHtml(r.race_no || "")}R</td>
                <td>${escapeHtml(r.race_class || "")}</td>
                <td class="${r.finish === 1 ? "kpi-up" : ""}"><b>${r.finish}着</b></td>
              </tr>`
            )
            .join("")}</tbody>
        </table></div>
      </section>`
    : "";

  const histRows = p.class_history || [];
  const histHtml = histRows.length
    ? `<section class="surface-panel">
        <div class="capital-head"><div><h3>級班の履歴(公式)</h3></div><span class="page-meta">昇級・降級の記録</span></div>
        <div class="table-wrap"><table class="data-table compact-table">
          <thead><tr><th>級班</th><th>適用日</th></tr></thead>
          <tbody>${histRows.map((h) => `<tr><td>${escapeHtml(h.class || "")}</td><td>${escapeHtml(h.date || "")}</td></tr>`).join("")}</tbody>
        </table></div>
      </section>`
    : "";

  body.innerHTML = `${kpiHtml}${todayHtml}${trendHtml}${finishHtml}${histHtml}`;
  const jumpBtn = body.querySelector("[data-jump-race]");
  if (jumpBtn) {
    jumpBtn.addEventListener("click", (event) => {
      event.preventDefault();
      window.location.href = `index.html?openRace=${encodeURIComponent(jumpBtn.dataset.jumpRace)}`;
    });
  }
}

// 得点推移の簡易スパークライン(SVG)
function sparkline(values) {
  if (values.length < 2) return `<p class="consult-hint">推移データが不足しています。</p>`;
  const w = 560, h = 120, pad = 12;
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;
  const stepX = (w - pad * 2) / (values.length - 1);
  const points = values.map((v, i) => {
    const x = pad + i * stepX;
    const y = h - pad - ((v - min) / range) * (h - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const last = points[points.length - 1].split(",");
  return `<svg viewBox="0 0 ${w} ${h}" class="calibration-chart" role="img" aria-label="得点推移">
    <polyline points="${points.join(" ")}" class="cal-line" />
    <circle cx="${last[0]}" cy="${last[1]}" r="4" class="cal-dot" />
  </svg>`;
}

async function loadResults(date) {
  try {
    const url = date ? `/api/results?date=${encodeURIComponent(date)}` : "/api/results";
    const res = await apiGet(url);
    const payload = await res.json();
    if (!payload.ok) {
      el("resultsBody").innerHTML = `<div class="empty">${escapeHtml(payload.error || "結果を読み込めませんでした。")}</div>`;
      return;
    }
    state.results = payload;
    renderResults(payload);
  } catch (error) {
    el("resultsBody").innerHTML = `<div class="empty">結果エラー: ${escapeHtml(error.message)}</div>`;
  }
}

function formIcon(form) {
  if (form === "hot") return '<span class="form-icon hot" title="好調(回収率90%以上)">🔥好調</span>';
  if (form === "cold") return '<span class="form-icon cold" title="不調(回収率70%未満)">🥶不調</span>';
  if (form === "even") return '<span class="form-icon even" title="標準">➖標準</span>';
  return "";
}

function renderRecordBar(record) {
  const bar = el("aiRecordBar");
  if (!bar || !record) return;
  const pctText = (v) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
  const roiClass = (roi) => (roi == null ? "" : roi >= 1 ? "kpi-up" : "kpi-down");
  const cell = (item) => {
    if (!item || !item.settled) {
      return `<div class="record-cell"><span class="record-cell-label">${escapeHtml(item?.label || "")}</span><div class="record-nums"><b>—</b></div><em>確定待ち</em></div>`;
    }
    return `<div class="record-cell">
      <span class="record-cell-label">${escapeHtml(item.label)} ${formIcon(item.form)}</span>
      <div class="record-nums">
        <span class="record-num"><i>回収率</i><b class="${roiClass(item.exacta_roi)}">${pctText(item.exacta_roi)}</b></span>
        <span class="record-num"><i>的中率</i><b>${pctText(item.exacta_hit_rate)}</b></span>
      </div>
      <em>本命 ${pctText(item.honmei_rate)} / 2車単 ${item.exacta_priced ? `${item.exacta_priced}R` : "集計待ち"} / 全${item.settled}R</em>
    </div>`;
  };
  bar.innerHTML = `<div class="record-title">AI成績 <span class="help-tip" tabindex="0" data-tip="回収率=実払戻÷投資額。的中率=2車単の買い目(軸1着固定2点)が当たった割合。各100円の平坦買いでの実測値。競輪は控除率25%なので回収率100%超は容易ではありません。">?</span></div>${cell(record.today)}${cell(record.week)}${cell(record.last_week)}${cell(record.total)}`;
}

// 実績ページ: 通算KPI + 期間別テーブル
function renderRecordPage(payload) {
  const hero = el("recordHero");
  if (!hero) return; // 実績ページ以外
  const record = payload.record || {};
  const total = record.total || {};
  const roiText = (roi) => (roi == null ? "—" : `${(roi * 100).toFixed(1)}%`);
  const pct = (v) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
  el("recordMeta").textContent = `${record.as_of || ""} 時点 / ${total.settled ?? 0}レースを検証`;
  const kpis = [
    { label: "検証レース数", value: `${total.settled ?? 0}`, sub: "全て事前予想", cls: "" },
    { label: "本命的中率", value: pct(total.honmei_rate), sub: `較正済みAI勝率 <span class="help-tip" tabindex="0" data-tip="AIの表示勝率は過去の実測に合わせて較正済み。40%と出たら実際に約40%当たります。">?</span>`, cls: "" },
    { label: "2車単回収率", value: roiText(total.exacta_roi), sub: `${total.exacta_priced ?? 0}R集計(実払戻)`, cls: (total.exacta_roi || 0) >= 1 ? "kpi-up" : "kpi-down" },
    { label: "3連単的中率", value: pct(total.trifecta_rate), sub: "候補6点以内", cls: "" },
  ];
  hero.innerHTML = kpis
    .map((k) => `<div class="kpi-card"><span>${k.label}</span><strong class="${k.cls}">${k.value}</strong><em>${k.sub}</em></div>`)
    .join("");
  const table = el("recordTable");
  if (!table) return;
  const rows = ["today", "week", "last_week", "total"].map((key) => record[key]).filter(Boolean);
  table.innerHTML = `<div class="table-wrap"><table class="data-table compact-table">
    <thead><tr><th>期間</th><th>確定R</th><th>本命的中</th><th>2車単的中</th><th>回収率</th><th>調子</th></tr></thead>
    <tbody>${rows
      .map(
        (r) => `<tr>
        <td><b>${escapeHtml(r.label)}</b></td>
        <td>${r.settled}</td>
        <td>${pct(r.honmei_rate)}</td>
        <td>${pct(r.exacta_hit_rate)}</td>
        <td class="${(r.exacta_roi || 0) >= 1 ? "kpi-up" : "kpi-down"}"><b>${roiText(r.exacta_roi)}</b></td>
        <td>${formIcon(r.form) || "—"}</td>
      </tr>`
      )
      .join("")}</tbody>
  </table></div>`;
}

// 初回訪問だけの30秒オンボーディング(3ステップ)。閉じたら二度と出さない。
function renderOnboarding(page) {
  if (page !== "home") return;
  try {
    if (localStorage.getItem("kl_onboard_v1")) return;
  } catch (_e) {
    return;
  }
  const header = document.querySelector(".page-header");
  if (!header) return;
  const banner = document.createElement("section");
  banner.className = "onboarding";
  banner.innerHTML = `
    <div class="onboarding-head"><b>はじめての方へ — このラボの読み方(30秒)</b></div>
    <ol class="onboarding-steps">
      <li><b>AI勝率は本物</b> — 表示する勝率は過去の実測に較正済み。「40%」は本当に約40%当たります(<a href="record.html">実績で検証可</a>)。</li>
      <li><b>💎妙味 = 買う価値</b> — 実オッズ×AI確率の期待値(EV)。EVが1を超えた目は「市場がAIの見立てより安く売っている」状態です。</li>
      <li><b>成績は全公開</b> — 的中も外れも隠しません。予想は発走前にGitへ記録され、後から書き換えられません。</li>
    </ol>
    <button type="button" class="button" id="onboardClose">わかった(次から表示しない)</button>`;
  header.insertAdjacentElement("afterend", banner);
  banner.querySelector("#onboardClose").addEventListener("click", () => {
    try {
      localStorage.setItem("kl_onboard_v1", "1");
    } catch (_e) {}
    banner.remove();
  });
}

// AIの得意条件テーブル(回収率100%超=緑、85%以上=中間、n>=30を「信頼できる」扱い)
function renderConditions(conditions) {
  const panel = el("conditionsPanel");
  const body = el("conditionsBody");
  if (!panel || !body) return;
  const rows = (conditions || []).filter((c) => c.races >= 10);
  if (!rows.length) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const roiClass = (roi) => (roi >= 1 ? "kpi-up" : roi >= 0.85 ? "" : "kpi-down");
  body.innerHTML = `<div class="table-wrap"><table class="data-table compact-table">
    <thead><tr><th>条件</th><th>R数</th><th>回収率</th><th>的中率</th></tr></thead>
    <tbody>${rows
      .slice(0, 12)
      .map(
        (c) => `<tr>
        <td><small class="cond-cat">${escapeHtml(c.category)}</small> <b>${escapeHtml(c.label)}</b>${c.races >= 30 && c.roi >= 1 ? ' <span class="cond-fav">得意</span>' : ""}</td>
        <td>${c.races}</td>
        <td class="${roiClass(c.roi)}"><b>${(c.roi * 100).toFixed(1)}%</b></td>
        <td>${(c.hit_rate * 100).toFixed(1)}%</td>
      </tr>`
      )
      .join("")}</tbody>
  </table></div>
  <p class="cond-note">R数が少ない条件は偶然の可能性あり。30R以上かつ回収率100%超だけ「得意」と表示。</p>`;
}

// 較正カーブ: 対角線に乗っていれば「言った勝率どおりに当たっている」
function renderCalibration(calibration) {
  const panel = el("calibrationPanel");
  const svg = el("calibrationChart");
  if (!panel || !svg) return;
  const rows = calibration || [];
  if (rows.length < 3) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const W = 320, H = 240, pad = 34;
  const x = (v) => pad + v * (W - pad - 12);
  const y = (v) => H - pad + v * -(H - pad - 12);
  const pts = rows.map((r) => ({ px: x(r.predicted), py: y(r.actual), r }));
  const gridLines = [0.2, 0.4, 0.6, 0.8]
    .map((v) => `<line x1="${x(v)}" y1="${y(0)}" x2="${x(v)}" y2="${y(1)}" class="cal-grid"/><line x1="${x(0)}" y1="${y(v)}" x2="${x(1)}" y2="${y(v)}" class="cal-grid"/>
      <text x="${x(v)}" y="${H - pad + 14}" class="cal-tick">${v * 100}%</text><text x="${pad - 6}" y="${y(v) + 3}" class="cal-tick" text-anchor="end">${v * 100}%</text>`)
    .join("");
  svg.innerHTML = `
    ${gridLines}
    <line x1="${x(0)}" y1="${y(0)}" x2="${x(1)}" y2="${y(1)}" class="cal-diagonal"/>
    <polyline points="${pts.map((p) => `${p.px},${p.py}`).join(" ")}" class="cal-line"/>
    ${pts.map((p) => `<circle cx="${p.px}" cy="${p.py}" r="${Math.min(9, 3 + Math.sqrt(p.r.races) / 3)}" class="cal-dot"><title>${p.r.bin}: 表示${(p.r.predicted * 100).toFixed(1)}% → 実際${(p.r.actual * 100).toFixed(1)}% (${p.r.races}R)</title></circle>`).join("")}
    <text x="${x(0.5)}" y="${H - 6}" class="cal-label" text-anchor="middle">AIが表示した勝率</text>
    <text x="10" y="${y(0.5)}" class="cal-label" transform="rotate(-90 10 ${y(0.5)})" text-anchor="middle">実際の的中率</text>`;
}

function renderResults(payload) {
  const summary = payload.summary || {};
  renderRecordBar(payload.record);
  renderConditions(payload.conditions);
  renderCalibration(payload.calibration);
  renderRecordPage(payload);
  if (!el("resultsBody")) return; // 結果ページ以外ではAI成績バーだけ更新
  el("resultsDates").innerHTML = (payload.dates || [])
    .map(
      (date) => `<button class="date-chip${date === payload.date ? " active" : ""}" data-date="${escapeAttr(date)}">${shortDate(date)}</button>`
    )
    .join("");
  el("resultsDates")
    .querySelectorAll(".date-chip")
    .forEach((btn) => btn.addEventListener("click", () => loadResults(btn.dataset.date)));

  el("resultsMeta").textContent = summary.settled
    ? `${payload.date} / ${summary.settled}レース確定`
    : `${payload.date || ""} / 結果待ち${summary.pending || 0}レース`;

  const summaryHtml = [
    ["本命的中", summary.honmei_rate != null ? percent(summary.honmei_rate) : "-", `${summary.honmei_hits ?? 0}/${summary.settled ?? 0}`, "green"],
    ["車券圏内", summary.in_top3_rate != null ? percent(summary.in_top3_rate) : "-", "本命が3着内", "blue"],
    ["3連単的中", summary.trifecta_hits ?? 0, summary.trifecta_rate != null ? percent(summary.trifecta_rate) : "-", "amber"],
    ["結果待ち", summary.pending ?? 0, `全${summary.races ?? 0}レース`, "slate"],
  ]
    .map(metric)
    .join("");
  el("resultsSummary").innerHTML = summaryHtml;
  renderOriginalDaily(state.bankroll?.daily_history || []);

  const venues = payload.venues || [];
  if (!venues.length) {
    el("resultsBody").innerHTML = `<div class="empty">この日の予想データがありません。</div>`;
    return;
  }
  el("resultsBody").innerHTML = venues
    .map(
      (group) => `<section class="results-venue">
        <header class="results-venue-head">
          <h3>${escapeHtml(group.venue)}</h3>
          <span>${group.races.length}レース</span>
        </header>
        <div class="results-rows">
          ${group.races.map(renderResultRow).join("")}
        </div>
      </section>`
    )
    .join("");
}

function renderResultRow(race) {
  const pick = race.top_pick || {};
  const badge = resultBadge(race.status);
  const tickets = (race.tickets || [])
    .map((label) => {
      const hit = race.hits?.trifecta_label === label;
      return `<span class="result-ticket${hit ? " is-hit" : ""}">${escapeHtml(label)}</span>`;
    })
    .join("");
  const order = (race.result_order || []).map((carNo) => car(carNo)).join("");
  return `<div class="result-row is-${race.status}">
    <span class="time-chip">${escapeHtml(race.start_time || "--:--")}</span>
    <strong class="result-race">${escapeHtml(race.race_no || "")}R</strong>
    <span class="result-pick">${car(pick.car_no)} ${playerLink(pick.name, pick.player_id)} <em>${percent(pick.probability)}</em></span>
    <span class="result-tickets">${tickets}</span>
    <span class="result-order">${order || '<em class="pending-text">結果待ち</em>'}</span>
    ${badge}
  </div>`;
}

function resultBadge(status) {
  if (status === "hit_trifecta") return `<span class="hit-badge hit-trifecta">3連単的中</span>`;
  if (status === "hit_honmei") return `<span class="hit-badge hit-honmei">本命的中</span>`;
  if (status === "in_top3") return `<span class="hit-badge hit-top3">車券圏</span>`;
  if (status === "miss") return `<span class="hit-badge hit-miss">ハズレ</span>`;
  return `<span class="hit-badge hit-pending">結果待ち</span>`;
}

function shortDate(iso) {
  const parts = String(iso || "").split("-");
  if (parts.length !== 3) return iso;
  return `${Number(parts[1])}/${Number(parts[2])}`;
}

/* ---- 展開予想モーション再生 ---- */

const CAR_COLORS = {
  1: ["#ffffff", "#2b2b2e"],
  2: ["#2b2b2e", "#ffffff"],
  3: ["#d13438", "#ffffff"],
  4: ["#1f5cd4", "#ffffff"],
  5: ["#f2c800", "#2b2b2e"],
  6: ["#1d9e50", "#ffffff"],
  7: ["#f4792a", "#ffffff"],
  8: ["#f26ba4", "#2b2b2e"],
  9: ["#7b3fd4", "#ffffff"],
};

/*
 * バンク形状(KEIRIN.JPガイド準拠): 直線+半円の競走路。
 * ゴール=ホームストレッチライン(下側直線)、打鐘=残り1周半のバックストレッチライン通過。
 * センターライン: 直線 y=250/50 (x 170..470)、両端は半径100の半円。
 */
// スタートから4周のフルレース。打鐘=残り1周半(2.5周消化)、ゴール=4.0周。
const MOTION = { duration: 20000, laps: 4, bellLap: 2.5 };
const BANK = {
  x1: 170,
  x2: 470,
  yBottom: 250,
  yTop: 50,
  cy: 150,
  r: 100,
  goalX: 430,
};
BANK.arcLen = Math.PI * BANK.r;
BANK.straight = BANK.x2 - BANK.x1;
BANK.segA = BANK.x2 - BANK.goalX; // ゴール→1コーナー
BANK.perimeter = 2 * BANK.straight + 2 * BANK.arcLen;

function bankPointAt(fraction) {
  let s = ((fraction % 1) + 1) % 1 * BANK.perimeter;
  const { x1, x2, yBottom, yTop, cy, r, goalX, segA, arcLen, straight } = BANK;
  if (s < segA) {
    return { x: goalX + s, y: yBottom, nx: 0, ny: 1 };
  }
  s -= segA;
  if (s < arcLen) {
    const theta = Math.PI / 2 - s / r;
    return { x: x2 + r * Math.cos(theta), y: cy + r * Math.sin(theta), nx: Math.cos(theta), ny: Math.sin(theta) };
  }
  s -= arcLen;
  if (s < straight) {
    return { x: x2 - s, y: yTop, nx: 0, ny: -1 };
  }
  s -= straight;
  if (s < arcLen) {
    const theta = -Math.PI / 2 - s / r;
    return { x: x1 + r * Math.cos(theta), y: cy + r * Math.sin(theta), nx: Math.cos(theta), ny: Math.sin(theta) };
  }
  s -= arcLen;
  return { x: x1 + s, y: yBottom, nx: 0, ny: 1 };
}

function toggleRaceMotion(raceKey) {
  const race = (state.today?.forecasts || []).find((item) => item.race_key === raceKey);
  const container = document.querySelector(`[data-motion-race="${CSS.escape(raceKey)}"]`);
  if (!race || !container) return;
  const stage = container.querySelector(".motion-stage");
  const btn = container.querySelector(".motion-btn");
  if (state.motionTimers[raceKey]) {
    clearInterval(state.motionTimers[raceKey]);
    delete state.motionTimers[raceKey];
  }
  stage.hidden = false;
  btn.textContent = "↺ もう一度再生";
  startRaceMotion(raceKey, race, stage);
}

function normalizeOrder(order, cars) {
  // 並び順に全車を必ず含める(欠けた車番が先頭に重なって消えるバグの対策)
  const seen = new Set();
  const clean = [];
  for (const raw of order) {
    const car = Number(raw);
    if (cars.includes(car) && !seen.has(car)) {
      seen.add(car);
      clean.push(car);
    }
  }
  for (const car of cars) {
    if (!seen.has(car)) clean.push(car);
  }
  return clean;
}

function computeMotionBase(race) {
  const entries = race.entries || [];
  const lineup = (race.lineup || []).map((line) => line.map(Number));
  const probs = new Map(entries.map((entry) => [Number(entry.car_no), Number(entry.win_probability || 0)]));
  const styles = new Map(entries.map((entry) => [Number(entry.car_no), entry.style || ""]));
  const names = new Map(entries.map((entry) => [Number(entry.car_no), entry.name || ""]));
  (race.top3 || []).forEach((row) => {
    if (row.car_no != null && !names.get(Number(row.car_no))) names.set(Number(row.car_no), row.name || "");
  });
  // 全車 = 出走表 ∪ top3。出走表が空のときだけラインからも拾う
  // (ライン解析は稀に勝ち上がり条件などの数字を誤検出するため、実在車に限定する)
  const entryCars = entries.map((entry) => Number(entry.car_no)).filter((car) => Number.isFinite(car) && car > 0);
  const carPool = entryCars.length
    ? [...entryCars, ...(race.top3 || []).map((row) => Number(row.car_no))]
    : [...lineup.flat(), ...(race.top3 || []).map((row) => Number(row.car_no))];
  const cars = [...new Set(carPool.filter((car) => Number.isFinite(car) && car > 0))];

  const initial = normalizeOrder(lineup.flat(), cars);
  const top3 = (race.top3 || []).map((row) => Number(row.car_no)).filter(Boolean);
  const rest = cars.filter((car) => !top3.includes(car)).sort((a, b) => (probs.get(b) || 0) - (probs.get(a) || 0));
  const final = normalizeOrder([...top3, ...rest], cars);

  const escapeLine =
    lineup.find((line) => styles.get(line[0]) === "逃") ||
    lineup.find((line) => line.length >= 2) ||
    (lineup.length ? [lineup[0]].flat() : [initial[0]].filter(Boolean));

  // 過去データ(S/B実績・決まり手)から展開の役どころを決める
  const statOf = new Map(entries.map((entry) => [Number(entry.car_no), entry.stats || {}]));
  const st = (car, key) => Number((statOf.get(Number(car)) || {})[key] || 0);
  const lineHeads = lineup.filter((line) => line.length).map((line) => line[0]);
  // S取り: 先頭選手のS実績が最多のライン(同数は車番が若い方)
  const sHead = lineHeads.length
    ? lineHeads.reduce((best, car) => (st(car, "start_count") > st(best, "start_count") || (st(car, "start_count") === st(best, "start_count") && car < best) ? car : best))
    : initial[0];
  const sLine = lineup.find((line) => line.includes(sHead)) || [sHead];
  // 主導権(B): B実績+逃げ決まり手が最も強いライン
  const bHead = lineHeads.length
    ? lineHeads.reduce((best, car) => (st(car, "back_count") + st(car, "escape") * 2 > st(best, "back_count") + st(best, "escape") * 2 ? car : best))
    : escapeLine[0];
  const bLine = lineup.find((line) => line.includes(bHead)) || escapeLine;
  // 一度出るが下がる役: 主導権以外の自力型でまくり実績が逃げ実績以上
  let feint = null;
  for (const car of lineHeads) {
    if (car === bHead) continue;
    if (st(car, "makuri") >= Math.max(1, st(car, "escape"))) {
      if (feint == null || st(car, "makuri") > st(feint, "makuri")) feint = car;
    }
  }

  return { cars, lineup, initial, final, escapeLine, probs, styles, names, sLine, sHead, bLine, bHead, feint, st };
}

function buildMotionPlan(race) {
  const base = computeMotionBase(race);
  const { cars, lineup, initial, escapeLine, names, sLine, sHead, bLine, bHead, feint, st } = base;
  const N = (arr) => normalizeOrder(arr, cars);
  const nameOf = (car) => `${car}${(names.get(car) || "").slice(0, 4)}`;

  // 展開ベースのリアル着順(決定的=AI予想と一致)
  const finish = realisticFinishOrder(base, {}, escapeLine.filter((car) => cars.includes(car)));
  const final = N(finish.order);
  const topCar = final[0];

  // --- スタートからのフルレース(4周) ---
  const startOrder = N([...sLine, ...initial]);       // S取りラインが前受け
  const keyframes = [{ lap: 0.0, order: startOrder }];
  const captions = [
    { lap: 0.0, text: `スタート!S取りは${nameOf(sHead)}のライン(S実績${st(sHead, "start_count")}回)。前受けで隊列を組む` },
    { lap: 1.0, text: "青板(残り3周)、先頭員の後ろで落ち着いて周回" },
    { lap: 2.0, text: "赤板(残り2周)、位置取りの駆け引きが始まる" },
    { lap: 2.27, text: "先頭員が退避!いよいよ勝負どころ" },
  ];
  let cur = startOrder;
  const push = (lap, order, text) => {
    cur = N(order);
    keyframes.push({ lap, order: cur });
    if (text) captions.push({ lap, text });
  };

  // 一度出るが下がる(過去データ: まくり型の自力)
  if (feint != null && feint !== bHead) {
    const feintLine = lineup.find((line) => line.includes(feint)) || [feint];
    push(1.3, [...feintLine, ...cur], `${nameOf(feint)}が一度前へ出る!`);
    push(1.9, [...cur.filter((car) => !feintLine.includes(car)).slice(0, 2), ...feintLine, ...cur], `${nameOf(feint)}は深追いせず下げる。まくり(実績${st(feint, "makuri")}回)へ脚をためる`);
  }
  // 主導権ラインが打鐘前に上昇→打鐘で駆ける
  if (bHead !== cur[0]) {
    push(2.2, [...bLine, ...cur], `${nameOf(bHead)}ラインが上昇、主導権を取りに動く`);
  }
  push(MOTION.bellLap, [...bLine, ...cur], `🔔 打鐘!${nameOf(bHead)}が主導権(B実績${st(bHead, "back_count")}回)、残り1周半`);
  captions.push({ lap: 3.0, text: "残り1周、ホームストレッチライン通過" });

  // 最終バック: 勝ちライン(AI着順の1着のライン)が仕掛ける
  const attackLine = lineup.find((line) => line.includes(topCar) && line !== bLine);
  if (attackLine) {
    const others = cur.filter((car) => !attackLine.includes(car));
    push(3.5, [others[0], ...attackLine, ...others.slice(1)], "最終バック、まくり勢が外から仕掛ける!");
  } else {
    captions.push({ lap: 3.5, text: "最終バック、先行ラインがそのまま踏み合う" });
  }

  captions.push({ lap: 3.87, text: finish.close ? "ゴール前、横一線!写真判定級の大接戦!" : "直線勝負!内圏線と外帯線の間で追い比べ" });
  captions.push({ lap: 3.99, text: `ゴール!${finish.close ? "写真判定、ハナ差で" : "1着"} ${topCar || "-"} ${escapeHtml(names.get(topCar) || "")}` });
  push(3.9, final);
  keyframes.push({ lap: 4.0, order: final });
  keyframes.sort((a, b) => a.lap - b.lap);
  captions.sort((a, b) => a.lap - b.lap);
  return { cars, keyframes, captions };
}

/* ---- モーションメーカー: 自分で展開を組む ---- */

function buildCustomMotionPlan(race, opts) {
  const base = computeMotionBase(race);
  const { cars, lineup, initial, names } = base;
  const N = (arr) => normalizeOrder(arr, cars);
  const nameOf = (car) => `${car}${names.get(car) || ""}`;
  const lineOf = (car) => lineup.find((line) => line.includes(car)) || [car];

  // S(スタート先頭)で青板の隊列先頭を決める
  const startLead = opts.sCar && cars.includes(opts.sCar) ? opts.sCar : initial[0];
  const startOrder = N([...lineOf(startLead), ...initial]);

  const keyframes = [{ lap: 0.0, order: startOrder }];
  const sName = opts.sCar ? `S:${nameOf(startLead)} が誘導の後ろで先頭` : "スタート、先頭員の後ろで隊列を組んで周回";
  const captions = [
    { lap: 0.0, text: sName },
    { lap: 1.0, text: "青板(残り3周)、隊列は落ち着いて周回" },
    { lap: 2.0, text: "赤板(残り2周)、位置取りの駆け引き" },
    { lap: 2.27, text: "先頭員が退避!" },
  ];
  let cur = startOrder;
  const push = (lap, order, text) => {
    cur = N(order);
    keyframes.push({ lap, order: cur });
    if (text) captions.push({ lap, text });
  };

  // 主導権(先行)ラインは B(バック線を先頭で通過する車)から決める。無指定ならAI推定。
  const bLine = opts.bCar && cars.includes(opts.bCar) ? lineOf(opts.bCar) : null;
  const leadLine = (
    bLine && bLine.length ? bLine
      : opts.leadLine && opts.leadLine.length ? opts.leadLine
      : base.escapeLine
  ).filter((car) => cars.includes(car));
  // 4周ドメイン: 打鐘=2.5周。カマシ=打鐘前、遅め=残り1周手前。
  const spurtLap = opts.spurt === "early" ? 2.2 : opts.spurt === "late" ? 2.85 : MOTION.bellLap;

  // --- 主導権争い ---
  if (opts.tsuppari) {
    const frontLine = lineOf(initial[0]);
    push(
      spurtLap,
      [...frontLine, ...leadLine, ...cur],
      opts.spurt === "early"
        ? "打鐘前から動くが…つっぱり先行!前は譲らない!"
        : "🔔 打鐘!つっぱり先行、前は絶対に譲らない!"
    );
  } else {
    const text =
      opts.spurt === "early"
        ? `打鐘前から${nameOf(leadLine[0])}ラインがカマシ気味に動く!`
        : opts.spurt === "late"
        ? `残り1周、${nameOf(leadLine[0])}ラインが満を持してスパート!`
        : `🔔 打鐘!${nameOf(leadLine[0])}ラインが主導権を取る`;
    push(spurtLap, [...leadLine, ...cur], text);
  }
  if (opts.spurt === "early") captions.push({ lap: 2.5, text: "🔔 打鐘!早めの主導権争いでピッチが上がる" });
  if (opts.pace === "slow") captions.push({ lap: 2.7, text: "スローペース…上がり勝負、差し・追込有利の流れ" });
  else if (opts.pace === "high") captions.push({ lap: 2.7, text: "ハイペースの消耗戦!先行有利、後方は離れる" });
  // B(バック線先頭)= 先行の主役をナレーション
  if (opts.bCar && cars.includes(opts.bCar)) {
    captions.push({ lap: 3.45, text: `B:${nameOf(opts.bCar)} が先頭でバック線を通過(先行)` });
  }
  // H(ホーム線先頭): 残り1周で先頭に立つ車を反映
  if (opts.hCar && cars.includes(opts.hCar)) {
    push(3.0, [...lineOf(opts.hCar), ...cur], `H:${nameOf(opts.hCar)} が先頭でホーム線を通過`);
  } else if (opts.spurt !== "late") {
    captions.push({ lap: 3.0, text: "残り1周、ホームストレッチライン通過" });
  }

  // --- 番手競り ---
  if (opts.seriCar && cars.includes(opts.seriCar)) {
    const head = cur[0];
    push(
      Math.min(3.1, spurtLap + 0.25),
      [head, opts.seriCar, ...cur.filter((car) => car !== opts.seriCar && car !== head)],
      `${nameOf(opts.seriCar)}が番手を狙って外から競りかける!`
    );
    if (opts.seriResult === "lose") {
      const without = cur.filter((car) => car !== opts.seriCar);
      push(3.2, [...without.slice(0, 4), opts.seriCar, ...without.slice(4)], `競り負け…${nameOf(opts.seriCar)}は位置を下げる`);
    } else {
      captions.push({ lap: 3.2, text: `競り勝ち!${nameOf(opts.seriCar)}が番手を奪い切る` });
    }
  }

  // --- アクシデント ---
  if (opts.accidentCar && cars.includes(opts.accidentCar)) {
    push(3.05, [...cur.filter((car) => car !== opts.accidentCar), opts.accidentCar], `⚠️ ${nameOf(opts.accidentCar)}にアクシデント!大きく後退`);
  }

  // --- ちぎれ ---
  if (opts.chigirareCar && cars.includes(opts.chigirareCar)) {
    push(3.3, [...cur.filter((car) => car !== opts.chigirareCar), opts.chigirareCar], `${nameOf(opts.chigirareCar)}が車間を詰められず…ちぎれた!`);
  }

  // --- まくり ---
  if (opts.makuriCar && cars.includes(opts.makuriCar)) {
    const makuriLine = lineOf(opts.makuriCar);
    const move = [opts.makuriCar, ...makuriLine.filter((car) => car !== opts.makuriCar && cars.includes(car))];
    if (opts.makuriResult === "fail") {
      push(3.5, [...cur.slice(0, 2), ...move, ...cur.slice(2)], `${nameOf(opts.makuriCar)}が外からまくりに出るが…`);
      const withoutMove = cur.filter((car) => !move.includes(car));
      push(3.72, [...withoutMove.slice(0, 4), ...move, ...withoutMove.slice(4)], "まくり不発!前のペースに飲み込まれる");
    } else {
      push(3.5, [...move, ...cur], `${nameOf(opts.makuriCar)}が豪快にまくる!一気に前へ!`);
    }
  } else if (!opts.chigirareCar && !opts.accidentCar) {
    captions.push({ lap: 3.5, text: "最終バック、勝負どころ!" });
  }

  // --- 大外一気(最後方から追込) ---
  if (opts.closerCar && cars.includes(opts.closerCar) && opts.closerCar !== opts.makuriCar) {
    const others = cur.filter((car) => car !== opts.closerCar);
    // 一旦最後方 → 直線で外を強襲して上位へ
    push(3.55, [...others, opts.closerCar], `${nameOf(opts.closerCar)}は最後方…ここから大外へ持ち出す`);
    push(3.8, [others[0], opts.closerCar, ...others.slice(1)], `${nameOf(opts.closerCar)}が大外一気に伸びてくる!`);
  }

  // --- 結末 ---
  const dropTail = (order) => {
    let result = [...order];
    for (const car of [opts.chigirareCar, opts.accidentCar]) {
      if (car && result.includes(car)) {
        result = [...result.filter((c) => c !== car), car];
      }
    }
    return result;
  };
  let finalOrder;
  let isClose = false;
  let who = "AIの結末";
  if (opts.finishMode === "manual" && opts.finishTop3?.length) {
    const picked = opts.finishTop3.filter((car) => cars.includes(car));
    finalOrder = N([...picked, ...dropTail(cur).filter((car) => !picked.includes(car))]);
    who = "あなたの結末";
  } else {
    // 展開(先行ライン・番手・まくり・ペース等)から競輪らしい着順を組み立てる
    const finish = realisticFinishOrder(base, opts, leadLine);
    finalOrder = N(dropTail(finish.order));
    isClose = finish.close;
  }

  // 決まり手ナレーションを展開から推定
  const winner = finalOrder[0];
  const winnerLine = lineOf(winner);
  let kimarite;
  if (opts.makuriCar === winner && opts.makuriResult !== "fail") kimarite = "まくり炸裂!";
  else if (opts.tsuppari && winnerLine.includes(initial[0]) && winnerLine[0] === winner) kimarite = "つっぱり逃げ切り!";
  else if (winnerLine[0] === winner && leadLine.includes(winner)) kimarite = "逃げ切り!";
  else if (winnerLine.indexOf(winner) === 1) kimarite = "番手絶好、差し切り!";
  else if (opts.seriCar === winner && opts.seriResult !== "lose") kimarite = "競り勝ちからの差し切り!";
  else kimarite = "直線強襲!";

  captions.push({ lap: 3.87, text: isClose ? "ゴール前、横一線!写真判定級の大接戦!" : "直線勝負!" });
  if (isClose) captions.push({ lap: 3.95, text: "際どい…!ハナ差の追い比べ…!" });
  captions.push({
    lap: 3.99,
    text: `${isClose ? "写真判定、ハナ差!" : ""}${kimarite} ${who} ${finalOrder.slice(0, 3).map((car) => escapeHtml(nameOf(car))).join(" → ")}`,
  });

  push(3.9, finalOrder);
  keyframes.push({ lap: 4.0, order: finalOrder });
  keyframes.sort((a, b) => a.lap - b.lap);
  captions.sort((a, b) => a.lap - b.lap);
  return { cars, keyframes, captions };
}

// 展開から着順を組み立てる。決定的(毎回同じ)。
// 何も展開を指定しなければ AI予想の並び(base.final)をそのまま返す = 展開予想はAI予想と一致。
// まくり・大外・ちぎれ・ペース等を指定したときだけ、その展開に応じて決定的に着順が変わる。
function realisticFinishOrder(base, opts, leadLine) {
  const { cars, styles, lineup, final, escapeLine } = base;
  const lineOf = (car) => lineup.find((line) => line.includes(car)) || [car];

  // 展開の上書きが無く、先行ラインもAI想定どおりなら「AI予想の着順」をそのまま使う
  const defaultLead = escapeLine.filter((car) => cars.includes(car));
  const sameLead =
    leadLine.length === defaultLead.length && leadLine.every((car, i) => car === defaultLead[i]);
  const noDrama =
    !opts.makuriCar && !opts.closerCar && !opts.chigirareCar && !opts.accidentCar &&
    !opts.seriCar && !opts.tsuppari && (opts.pace || "normal") === "normal" && sameLead;
  const aiClose = _aiTopClose(base);
  if (noDrama) {
    return { order: normalizeOrder(final, cars), close: aiClose };
  }

  // --- ここからは「ユーザーが展開をいじった」場合。AI予想順を土台に決定的に補正する ---
  const makuriHit = opts.makuriCar && cars.includes(opts.makuriCar) && opts.makuriResult !== "fail";
  const makuriLine = makuriHit ? lineOf(opts.makuriCar) : [];
  const rankBonus = new Map(final.map((car, i) => [car, (final.length - i) * 0.06])); // AI予想順の土台

  const score = new Map();
  for (const car of cars) {
    const st = styles.get(car) || "";
    let s = rankBonus.get(car) || 0;
    if (!sameLead && leadLine.includes(car)) {
      // 先行ラインをAIと違う線にしたときだけ、そのラインのスジを強調
      const lp = leadLine.indexOf(car);
      if (lp === 0) s += 0.30;
      else if (lp === 1) s += 0.46;
      else if (lp === 2) s += 0.24;
      else s += 0.08;
    }
    if (makuriHit) {
      const mp = makuriLine.indexOf(car);
      if (car === opts.makuriCar) s += 0.5;
      else if (mp === 1) s += 0.32;
      else if (mp >= 2) s += 0.12;
    }
    if (opts.pace === "slow" && (st === "追" || st === "差")) s += 0.12;
    if (opts.pace === "high" && st === "逃") s += 0.10;
    if (opts.tsuppari && leadLine[0] === car) s += 0.14;
    if (opts.seriCar === car) s += opts.seriResult === "lose" ? -0.35 : 0.12;
    score.set(car, s);
  }
  if (opts.chigirareCar) score.set(opts.chigirareCar, (score.get(opts.chigirareCar) || 0) - 1.0);
  if (opts.accidentCar) score.set(opts.accidentCar, (score.get(opts.accidentCar) || 0) - 1.2);

  // 決定的なソート(同点はAI予想順で割る)
  const rankIdx = new Map(final.map((car, i) => [car, i]));
  let ordered = [...cars].sort((a, b) => {
    const d = (score.get(b) || 0) - (score.get(a) || 0);
    return d !== 0 ? d : (rankIdx.get(a) || 0) - (rankIdx.get(b) || 0);
  });

  if (opts.closerCar && cars.includes(opts.closerCar)) {
    ordered = ordered.filter((car) => car !== opts.closerCar);
    ordered.splice(Math.min(1, ordered.length), 0, opts.closerCar);
  }
  for (const car of [opts.chigirareCar, opts.accidentCar]) {
    if (car && ordered.includes(car)) {
      ordered = ordered.filter((c) => c !== car);
      ordered.push(car);
    }
  }
  const order = normalizeOrder(ordered, cars);
  const g1 = score.get(order[0]);
  const g2 = score.get(order[1]);
  const close = order.length >= 2 && g1 != null && g2 != null ? g1 - g2 < 0.12 : aiClose;
  return { order, close };
}

// AI予想の1着と2着の勝率差が小さければ「接戦」とみなす(展開予想の実況用・決定的)
function _aiTopClose(base) {
  const { final, probs } = base;
  if (!final || final.length < 2) return false;
  const p1 = probs.get(final[0]) || 0;
  const p2 = probs.get(final[1]) || 0;
  return p1 - p2 < 0.06;
}

function renderMotionMaker() {
  const box = el("mmControls");
  if (!box) return;
  const forecasts = state.today?.forecasts || [];
  if (!forecasts.length) {
    box.innerHTML = `<div class="empty">本日の予想がありません。</div>`;
    return;
  }
  const venues = [...new Set(forecasts.map((race) => race.venue || "未設定"))];
  const selVenue = state.mmVenue && venues.includes(state.mmVenue) ? state.mmVenue : venues[0];
  const races = forecasts.filter((race) => (race.venue || "未設定") === selVenue);
  const selKey = state.mmRaceKey && races.some((race) => race.race_key === state.mmRaceKey) ? state.mmRaceKey : races[0]?.race_key;
  const race = races.find((item) => item.race_key === selKey);

  box.innerHTML = `
    <div class="mm-row">
      <label class="field compact"><span>レース場</span>
        <select id="mmVenue">${venues.map((v) => `<option value="${escapeAttr(v)}"${v === selVenue ? " selected" : ""}>${escapeHtml(v)}</option>`).join("")}</select>
      </label>
      <label class="field compact"><span>レース</span>
        <select id="mmRace">${races.map((r) => `<option value="${escapeAttr(r.race_key)}"${r.race_key === selKey ? " selected" : ""}>${escapeHtml(r.race_no)}R ${escapeHtml(r.start_time || "")}${r.elapsed ? " (終了)" : ""}</option>`).join("")}</select>
      </label>
    </div>
    <div id="mmOptions">${race ? renderMmOptions(race) : ""}</div>
    <div class="mm-actions">
      <button id="mmPlayBtn" class="button primary">▶ この展開で再生</button>
      <button id="mmAiBtn" class="button">AIの展開で再生</button>
    </div>
  `;
  on("mmVenue", "change", () => {
    state.mmVenue = el("mmVenue").value;
    state.mmRaceKey = null;
    renderMotionMaker();
  });
  on("mmRace", "change", () => {
    state.mmVenue = selVenue;
    state.mmRaceKey = el("mmRace").value;
    renderMotionMaker();
  });
  on("mmPlayBtn", "click", () => playCustomMotion(false));
  on("mmAiBtn", "click", () => playCustomMotion(true));
  on("mmFinishManual", "change", updateMmFinishVisibility);
  on("mmFinishAi", "change", updateMmFinishVisibility);
  updateMmFinishVisibility();
}

function renderMmOptions(race) {
  const base = computeMotionBase(race);
  const lines = (race.lines || []).map((line) => ({
    label: line.label,
    cars: (line.members || []).map((m) => Number(m.car_no)).filter((car) => base.cars.includes(car)),
  })).filter((line) => line.cars.length);
  const carOptions = (selected) => base.cars
    .map((car) => `<option value="${car}"${car === selected ? " selected" : ""}>${car} ${escapeHtml(base.names.get(car) || "")}</option>`)
    .join("");
  const noneCarOptions = `<option value="">なし</option>` + carOptions(null);
  const aiTop3 = base.final.slice(0, 3);
  const sengo = base.escapeLine[0] || base.cars[0]; // S・B・Hの既定=先行選手
  return `
    <div class="mm-group">
      <div class="mm-group-title">隊列の先頭(S・H・B)</div>
      <p class="mm-note">S=スタート先頭 / H=ホーム線先頭 / B=バック線先頭(=先行の主役)。誰が取るか選べます。</p>
      <div class="mm-row">
        <label class="field compact"><span>S(スタート)</span>
          <select id="mmS">${carOptions(sengo)}</select>
        </label>
        <label class="field compact"><span>H(ホーム先頭)</span>
          <select id="mmH">${carOptions(sengo)}</select>
        </label>
        <label class="field compact"><span>B(バック先頭・先行)</span>
          <select id="mmB">${carOptions(sengo)}</select>
        </label>
      </div>
    </div>
    <div class="mm-group">
      <div class="mm-group-title">主導権争い</div>
      <div class="mm-row">
        <label class="field compact"><span>主導権を取るライン</span>
          <select id="mmLeadLine">
            <option value="">AIに任せる</option>
            ${lines.map((line, i) => `<option value="${i}">${escapeHtml(line.label)}ライン</option>`).join("")}
          </select>
        </label>
        <label class="field compact"><span>スパートのタイミング</span>
          <select id="mmSpurt">
            <option value="bell">打鐘どおり</option>
            <option value="early">打鐘前から動く(カマシ)</option>
            <option value="late">遅め(残り1周から)</option>
          </select>
        </label>
        <label class="check-field"><input type="checkbox" id="mmTsuppari" /><span>つっぱり先行(前が譲らない)</span></label>
      </div>
    </div>
    <div class="mm-group">
      <div class="mm-group-title">道中のドラマ</div>
      <div class="mm-row">
        <label class="field compact"><span>番手競りを挑む選手</span>
          <select id="mmSeri">${noneCarOptions}</select>
        </label>
        <label class="field compact"><span>競りの結果</span>
          <select id="mmSeriResult">
            <option value="win">奪い切る</option>
            <option value="lose">競り負けて下げる</option>
          </select>
        </label>
        <label class="field compact"><span>まくる選手</span>
          <select id="mmMakuri">${noneCarOptions}</select>
        </label>
        <label class="field compact"><span>まくりの結果</span>
          <select id="mmMakuriResult">
            <option value="win">炸裂(前を飲み込む)</option>
            <option value="fail">不発(八分で終わる)</option>
          </select>
        </label>
      </div>
      <div class="mm-row">
        <label class="field compact"><span>ちぎられる選手</span>
          <select id="mmChigirare">${noneCarOptions}</select>
        </label>
        <label class="field compact"><span>アクシデントで後退</span>
          <select id="mmAccident">${noneCarOptions}</select>
        </label>
      </div>
      <div class="mm-row">
        <label class="field compact"><span>大外一気(最後方から追込)</span>
          <select id="mmCloser">${noneCarOptions}</select>
        </label>
        <label class="field compact"><span>ペース(展開)</span>
          <select id="mmPace">
            <option value="normal">平均ペース</option>
            <option value="slow">スロー(上がり勝負・差し有利)</option>
            <option value="high">ハイペース(消耗戦・先行有利)</option>
          </select>
        </label>
      </div>
    </div>
    <div class="mm-group">
      <div class="mm-group-title">結末</div>
      <div class="mm-row mm-finish">
        <label class="check-field"><input type="radio" name="mmFinish" id="mmFinishAi" checked /><span>AIが考える</span></label>
        <label class="check-field"><input type="radio" name="mmFinish" id="mmFinishManual" /><span>自分で決める</span></label>
        <span id="mmFinishPicks" hidden>
          <label class="field compact"><span>1着</span><select id="mm1st">${carOptions(aiTop3[0])}</select></label>
          <label class="field compact"><span>2着</span><select id="mm2nd">${carOptions(aiTop3[1])}</select></label>
          <label class="field compact"><span>3着</span><select id="mm3rd">${carOptions(aiTop3[2])}</select></label>
        </span>
      </div>
    </div>
  `;
}

function updateMmFinishVisibility() {
  const picks = el("mmFinishPicks");
  if (picks) picks.hidden = !el("mmFinishManual")?.checked;
}

function playCustomMotion(useAiPlan) {
  const raceKey = el("mmRace")?.value;
  const race = (state.today?.forecasts || []).find((item) => item.race_key === raceKey);
  const stage = el("mmStage");
  if (!race || !stage) return;

  let plan = null;
  if (!useAiPlan) {
    const base = computeMotionBase(race);
    const lines = (race.lines || []).map((line) => (line.members || []).map((m) => Number(m.car_no)).filter(Boolean));
    const leadIdx = el("mmLeadLine")?.value;
    const finishManual = el("mmFinishManual")?.checked;
    const picked = [Number(el("mm1st")?.value), Number(el("mm2nd")?.value), Number(el("mm3rd")?.value)];
    const opts = {
      leadLine: leadIdx !== "" && lines[Number(leadIdx)] ? lines[Number(leadIdx)] : null,
      sCar: Number(el("mmS")?.value) || null,
      hCar: Number(el("mmH")?.value) || null,
      bCar: Number(el("mmB")?.value) || null,
      spurt: el("mmSpurt")?.value || "bell",
      tsuppari: !!el("mmTsuppari")?.checked,
      seriCar: Number(el("mmSeri")?.value) || null,
      seriResult: el("mmSeriResult")?.value || "win",
      makuriCar: Number(el("mmMakuri")?.value) || null,
      makuriResult: el("mmMakuriResult")?.value || "win",
      chigirareCar: Number(el("mmChigirare")?.value) || null,
      accidentCar: Number(el("mmAccident")?.value) || null,
      closerCar: Number(el("mmCloser")?.value) || null,
      pace: el("mmPace")?.value || "normal",
      finishMode: finishManual ? "manual" : "ai",
      finishTop3: finishManual ? [...new Set(picked.filter(Boolean))] : null,
    };
    plan = buildCustomMotionPlan(race, opts);
  }

  const key = "__custom__";
  if (state.motionTimers[key]) {
    clearInterval(state.motionTimers[key]);
    delete state.motionTimers[key];
  }
  stage.hidden = false;
  startRaceMotion(key, race, stage, plan);
  setStatus(useAiPlan ? "AIの展開で再生中" : "あなたの展開で再生中");
}

function startRaceMotion(raceKey, race, stage, planOverride) {
  const svg = stage.querySelector(".motion-svg");
  const caption = stage.querySelector(".motion-caption");
  const plan = planOverride || buildMotionPlan(race);
  if (!plan.cars.length) {
    caption.textContent = "出走データが足りないため再生できません";
    return;
  }
  svg.innerHTML = motionTrackSvg() + motionPacerSvg() + plan.cars.map((car) => motionRiderSvg(car)).join("");
  const riders = new Map(plan.cars.map((car) => [car, svg.querySelector(`[data-rider="${car}"]`)]));
  const pacer = svg.querySelector('[data-rider="__pacer__"]');
  // 残り周回カウンタ(実際のレースと同じく 青板=残り3周 / 赤板=残り2周 / 打鐘=残り1周半)
  let lapCounter = stage.querySelector(".lap-counter");
  if (!lapCounter) {
    lapCounter = document.createElement("div");
    lapCounter.className = "lap-counter";
    stage.appendChild(lapCounter);
  }

  const started = performance.now();
  const gapStep = 0.014; // 車間(周回の割合)
  const PACER_RETREAT = MOTION.bellLap - 0.25; // 先頭誘導員は打鐘の少し前に退避する

  const positionAt = (car, coveredLaps) => {
    const frames = plan.keyframes;
    let a = frames[0];
    let b = frames[frames.length - 1];
    for (let i = 0; i < frames.length - 1; i += 1) {
      if (coveredLaps >= frames[i].lap && coveredLaps <= frames[i + 1].lap) {
        a = frames[i];
        b = frames[i + 1];
        break;
      }
    }
    const span = Math.max(0.0001, b.lap - a.lap);
    const local = Math.min(1, Math.max(0, (coveredLaps - a.lap) / span));
    const eased = local * local * (3 - 2 * local);
    const posA = Math.max(0, a.order.indexOf(car));
    const posB = Math.max(0, b.order.indexOf(car));
    const smoothPos = posA + (posB - posA) * eased;
    // 接戦のゴール: 上位グループは車輪差まで詰まり、後方だけちぎれて離れる
    const finishApproach = Math.max(0, (coveredLaps - (MOTION.laps - 0.5)) / 0.5); // 0→1 (残り半周〜ゴール)
    const t = finishApproach * finishApproach;
    const tight = smoothPos < 3.5
      ? smoothPos * gapStep * 0.34 // 上位4車: 約1/3の車間=タイヤ差の攻防
      : (3.5 * 0.34 + (smoothPos - 3.5) * 1.7) * gapStep; // 後方: 大きく離される
    const gap = smoothPos * gapStep * (1 - t) + tight * t;
    // 直線は内・外・大外に持ち出して横に並ぶ追い比べ(縦1列にしない)
    const overtaking = posB < posA ? Math.sin(local * Math.PI) : 0;
    const lanes = [-3, 9, 16, 5]; // 1着=内粘り、2着=外、3着=大外、4着=中
    const lane = posB < 4 ? lanes[posB] : ((posB % 3) - 1) * 5;
    const fan = finishApproach * lane;
    return { gap, outside: overtaking * 12 + fan };
  };

  const tick = () => {
    const progress = Math.min(1, (performance.now() - started) / MOTION.duration);
    // 周回(打鐘まで2.5周)は時間の65%かけてゆっくり、打鐘からの1.5周は35%で一気に加速
    const bellRatio = MOTION.bellLap / MOTION.laps; // 0.625
    const eased = progress < 0.65
      ? (progress / 0.65) * bellRatio
      : bellRatio + ((progress - 0.65) / 0.35) * (1 - bellRatio);
    const covered = Math.min(MOTION.laps, eased * MOTION.laps);
    plan.cars.forEach((car) => {
      const node = riders.get(car);
      if (!node) return;
      const { gap, outside } = positionAt(car, covered);
      const point = bankPointAt(covered - gap);
      const x = point.x + point.nx * outside;
      const y = point.y + point.ny * outside;
      node.setAttribute("transform", `translate(${x.toFixed(1)}, ${y.toFixed(1)})`);
    });
    // 先頭誘導員: 隊列の少し前を走り、打鐘前に外へ膨れて退避する(実レースの流れ)
    if (pacer) {
      if (covered < PACER_RETREAT + 0.35) {
        const drift = Math.max(0, covered - PACER_RETREAT); // 退避開始からの経過
        const pacerLap = Math.min(covered, PACER_RETREAT) + 0.035 - drift * 0.12; // 退避中は減速して下がる
        const point = bankPointAt(pacerLap);
        const out = drift * 65; // 外帯線の外へ膨らむ
        pacer.setAttribute("transform", `translate(${(point.x + point.nx * out).toFixed(1)}, ${(point.y + point.ny * out).toFixed(1)})`);
        pacer.setAttribute("opacity", String(Math.max(0, 1 - drift * 2.4)));
      } else {
        pacer.setAttribute("opacity", "0");
      }
    }
    // 残り周回カウンタ(青板・赤板・打鐘は実レースの周回板と同じ意味)
    const remaining = Math.max(0, MOTION.laps - covered);
    let board = "周回中";
    if (remaining <= 0.02) board = "ゴール";
    else if (remaining <= 0.3) board = "直線";
    else if (remaining <= 1.0) board = "最終周回";
    else if (covered >= MOTION.bellLap) board = "🔔打鐘";
    else if (remaining <= 2.0) board = "赤板";
    else if (remaining <= 3.0) board = "青板";
    const remText = remaining <= 0.02 ? "" : `残り${(Math.ceil(remaining * 2) / 2).toFixed(1).replace(".0", "")}周`;
    lapCounter.innerHTML = `<b class="${covered >= MOTION.bellLap ? "is-bell" : ""}">${board}</b>${remText ? `<span>${remText}</span>` : ""}`;
    // 打鐘: 残り1周半のバック線通過で鐘が揺れる+ジャン音
    const bell = svg.querySelector(".bank-bell");
    if (bell) {
      if (covered >= MOTION.bellLap && covered < MOTION.bellLap + 0.35) {
        if (!bell.classList.contains("ringing")) {
          bell.classList.add("ringing");
          playBellSound();
        }
      } else {
        bell.classList.remove("ringing");
      }
    }
    const active = plan.captions.filter((item) => covered >= item.lap).pop();
    if (active) caption.innerHTML = active.text;
    if (progress >= 1 && state.motionTimers[raceKey]) {
      clearInterval(state.motionTimers[raceKey]);
      delete state.motionTimers[raceKey];
    }
  };
  tick();
  state.motionTimers[raceKey] = setInterval(tick, 33);
}

function motionTrackSvg() {
  const { x1, x2, cy, r, goalX, perimeter, segA, arcLen, straight } = BANK;
  // センターライン半径rに対するオフセットで各ラインを描く(rect rx=半径でスタジアム形状)
  const ring = (offset, cls, extra = "") => {
    const radius = r + offset;
    return `<rect x="${x1 - radius}" y="${cy - radius}" width="${x2 - x1 + radius * 2}" height="${radius * 2}" rx="${radius}" class="${cls}" ${extra}/>`;
  };
  // バックストレッチライン: ゴールからちょうど半周の地点(上側直線)
  const backX = x2 - (perimeter / 2 - segA - arcLen);
  return `
    ${ring(26, "bank-track")}
    ${ring(-22, "bank-refuge")}
    ${ring(-34, "bank-infield")}
    ${ring(-8, "bank-yellow-line")}
    ${ring(-16, "bank-outer-line")}
    ${ring(-22, "bank-inner-line")}
    <line x1="${goalX}" y1="${cy + r - 24}" x2="${goalX}" y2="${cy + r + 26}" class="goal-line" />
    <text x="${goalX + 6}" y="${cy + r + 22}" class="goal-text">GOAL</text>
    <line x1="${backX}" y1="${cy - r - 26}" x2="${backX}" y2="${cy - r + 24}" class="back-line" />
    <text x="${backX + 30}" y="${cy - r - 14}" class="back-text">バック線(打鐘)</text>
    <g class="bank-bell" transform="translate(${backX + 14}, ${cy - r - 22})">
      <g class="bell-inner">
        <path d="M0 -6 C4.5 -6 5.5 -1 5.5 2 L6.5 4 L-6.5 4 L-5.5 2 C-5.5 -1 -4.5 -6 0 -6 Z" class="bell-dome" />
        <circle cy="6" r="1.8" class="bell-clapper" />
      </g>
    </g>
  `;
}

let bellAudioCtx = null;

function playBellSound() {
  try {
    bellAudioCtx = bellAudioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const now = bellAudioCtx.currentTime;
    for (let i = 0; i < 4; i += 1) {
      const osc = bellAudioCtx.createOscillator();
      const gain = bellAudioCtx.createGain();
      osc.type = "triangle";
      osc.frequency.setValueAtTime(1560, now + i * 0.28);
      gain.gain.setValueAtTime(0.0001, now + i * 0.28);
      gain.gain.exponentialRampToValueAtTime(0.18, now + i * 0.28 + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.28 + 0.24);
      osc.connect(gain).connect(bellAudioCtx.destination);
      osc.start(now + i * 0.28);
      osc.stop(now + i * 0.28 + 0.26);
    }
  } catch {
    // 音が出せない環境では無音のまま(視覚アニメーションのみ)
  }
}

function motionRiderSvg(car) {
  const [fill, text] = CAR_COLORS[car] || ["#8d8f97", "#ffffff"];
  return `<g data-rider="${car}" class="motion-rider">
    <circle r="8" fill="${fill}" stroke="#18181a" stroke-width="1.1" />
    <text y="3.2" text-anchor="middle" fill="${text}" font-size="9" font-weight="700">${car}</text>
  </g>`;
}

// 先頭誘導員(ペーサー)。実際のレースと同じく隊列の前で風除けになり、打鐘前に外へ退避する。
function motionPacerSvg() {
  return `<g data-rider="__pacer__" class="motion-pacer">
    <circle r="8" fill="#e8e8e8" stroke="#555" stroke-width="1.1" stroke-dasharray="2.5 2" />
    <text y="3.2" text-anchor="middle" fill="#333" font-size="8" font-weight="800">誘</text>
  </g>`;
}

async function loadBankroll() {
  try {
    const res = await apiGet("/api/bankroll");
    const payload = await res.json();
    state.bankroll = payload;
    renderBankroll(payload);
    scheduleBankrollAutoRefresh(payload);
  } catch (error) {
    el("bankrollBody").innerHTML = `<div class="empty">運用状態を読み込めません: ${escapeHtml(error.message)}</div>`;
  }
}

function scheduleBankrollAutoRefresh(payload) {
  if (state.bankrollAutoTimer) {
    clearInterval(state.bankrollAutoTimer);
    state.bankrollAutoTimer = null;
  }
  // セッション運用中はオッズと候補レースをライブに近い間隔で更新する
  if (payload?.session?.status === "active") {
    state.bankrollAutoTimer = setInterval(loadBankroll, 40000);
  }
}

async function bankrollPost(path, body) {
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const payload = await res.json();
    if (payload.ok === false) {
      setStatus(payload.error || "運用操作に失敗しました");
      return null;
    }
    state.bankroll = payload;
    renderBankroll(payload);
    scheduleBankrollAutoRefresh(payload);
    return payload;
  } catch {
    setStatus("運用セッションはローカル版でのみ操作できます");
    return null;
  }
}

function changeBankrollStyle(style) {
  bankrollPost("/api/bankroll/set_style", { style }).then((payload) => {
    if (payload) {
      const label = (state.bankroll?.styles || []).find((s) => s.key === style)?.label || style;
      setStatus(`乗り方を「${label}」に変更しました`);
    }
  });
}

function startBankroll() {
  bankrollPost("/api/bankroll/start", {
    start_amount: Number(el("brStart").value || 0),
    target_amount: Number(el("brTarget").value || 0),
    style: state.bankrollStyle || "balance",
  }).then((payload) => {
    if (payload) setStatus("運用セッションを開始しました");
  });
}

function styleEstimate(style, start, target) {
  const races = (growth) => {
    if (!growth || growth <= 0 || !start || target <= start) return null;
    return Math.min(99, Math.ceil(Math.log(target / start) / Math.log(1 + growth)));
  };
  const cap = style.per_race_cap_pct / 100;
  const mainWeight = style.weights?.[0]?.[1] ?? 0.5;
  return {
    expected: races(cap * (style.min_ev - 1)),
    fast: races(cap * (mainWeight * style.assumed_main_odds - 1)),
  };
}

function updateStyleEstimates() {
  const start = Number(el("brStart")?.value || 0);
  const target = Number(el("brTarget")?.value || 0);
  (state.bankroll?.styles || []).forEach((style) => {
    const node = document.getElementById(`styleEst-${style.key}`);
    if (!node) return;
    if (style.race_limit) {
      node.textContent = `${style.race_limit}レース勝負・目標なし(伸ばせるだけ伸ばす)`;
      return;
    }
    const est = styleEstimate(style, start, target);
    node.textContent = est.fast
      ? `目安 ${est.fast}〜${est.expected ?? "-"}レースで達成`
      : "目標額を元手より大きくしてください";
  });
}

function selectBankrollStyle(key) {
  state.bankrollStyle = key;
  document.querySelectorAll(".style-card").forEach((node) => {
    node.classList.toggle("active", node.dataset.style === key);
  });
  const style = (state.bankroll?.styles || []).find((item) => item.key === key);
  if (style?.default_start && el("brStart")) {
    el("brStart").value = style.default_start;
  }
  updateStyleEstimates();
}

function commitBankroll() {
  const proposal = state.bankroll?.proposal;
  if (!proposal) return;
  if (!window.confirm(`${proposal.venue}${proposal.race_no}Rに合計${proposal.total_stake}円を購入した記録を付けます。実際の購入はWINTICKET等でご自身の確認のうえ行ってください。`)) {
    return;
  }
  bankrollPost("/api/bankroll/commit", { proposal }).then((payload) => {
    if (payload) setStatus("購入を記録しました。結果確定後に入力してください");
  });
}

function skipBankroll() {
  const proposal = state.bankroll?.proposal;
  if (!proposal) return;
  bankrollPost("/api/bankroll/skip", {
    race: proposal,
    reason: "手動見送り",
  }).then((payload) => {
    if (payload) setStatus("このレースを見送りました");
  });
}

function recordBankrollResult(outcome) {
  const pending = state.bankroll?.state?.pending_bet;
  if (!pending) return;
  let payout = 0;
  if (outcome === "won") {
    payout = Number(el("brPayout")?.value || 0);
    if (!payout) {
      setStatus("的中時は払戻額を入力してください");
      return;
    }
  }
  bankrollPost("/api/bankroll/result", { bet_id: pending.id, outcome, payout }).then((payload) => {
    if (payload) setStatus(outcome === "won" ? "的中を記録しました" : "不的中を記録しました");
  });
}

function stopBankroll() {
  if (!window.confirm("運用セッションを停止しますか？")) return;
  bankrollPost("/api/bankroll/stop", { reason: "手動停止" }).then((payload) => {
    if (payload) setStatus("運用セッションを停止しました");
  });
}

async function copyProposalTickets() {
  const text = state.bankroll?.proposal?.copy_text || "";
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    setStatus("買い目をコピーしました");
  } catch {
    setStatus("コピーできませんでした。手動で選択してください");
  }
}

function renderBankroll(payload) {
  renderOriginalDaily(payload.daily_history || []);
  renderFinance(payload.finance);
  const head = el("bankrollHeadActions");
  const body = el("bankrollBody");
  if (!head || !body) return; // 運用パネルがないページ(結果ページ等)
  const session = payload.session;
  const brState = payload.state;
  const yesterday = renderYesterdayResults(payload.yesterday);

  if (!session) {
    head.innerHTML = "";
    body.innerHTML = `${yesterday}${renderBankrollSetup(payload)}`;
    bindBankrollSetup();
    return;
  }

  if (session.status !== "active") {
    head.innerHTML = "";
    body.innerHTML = `${yesterday}${renderBankrollStopBanner(session, brState)}
      ${renderBankrollStatus(session, brState)}
      ${renderBankrollHistory(brState)}
      <div class="bankroll-restart">${renderBankrollSetup(payload)}</div>`;
    bindBankrollSetup();
    return;
  }

  head.innerHTML = `<button id="brStopBtn" class="button">停止</button>`;
  on("brStopBtn", "click", stopBankroll);

  const parts = [yesterday, renderBankrollStatus(session, brState)];
  if (payload.plan) {
    parts.push(renderOriginalPlan(payload.plan));
  }
  if (brState.pending_bet) {
    parts.push(renderBankrollPending(brState.pending_bet));
  } else if (payload.proposal) {
    parts.push(renderBankrollProposal(payload.proposal));
  } else {
    parts.push(`<div class="empty">${escapeHtml(payload.message || "提案できるレースがありません。しばらくして更新してください。")}</div>`);
  }
  if ((payload.judged_races || []).length) {
    parts.push(renderBankrollJudged(payload.judged_races));
  }
  parts.push(renderBankrollHistory(brState));
  body.innerHTML = parts.join("");

  on("brCommitBtn", "click", commitBankroll);
  on("brSkipBtn", "click", skipBankroll);
  on("brCopyBtn", "click", copyProposalTickets);
  on("brWonBtn", "click", () => recordBankrollResult("won"));
  on("brLostBtn", "click", () => recordBankrollResult("lost"));
  on("brRefreshBtn", "click", loadBankroll);
  document.querySelectorAll("[data-replace-slot]").forEach((btn) => {
    btn.addEventListener("click", () => togglePlanReplace(Number(btn.dataset.replaceSlot)));
  });
  document.querySelectorAll("[data-replace-confirm]").forEach((btn) => {
    btn.addEventListener("click", () => confirmPlanReplace(Number(btn.dataset.replaceConfirm)));
  });
}

function planStatusChip(slot) {
  const map = {
    planned: ["予定", "plan-chip-planned"],
    pending: ["結果待ち", "plan-chip-pending"],
    won: ["的中", "plan-chip-won"],
    lost: ["不的中", "plan-chip-lost"],
    skipped: ["見送り", "plan-chip-skip"],
    missed: ["未消化", "plan-chip-missed"],
  };
  const [label, cls] = map[slot.status] || [slot.status, ""];
  return `<span class="plan-chip ${cls}">${escapeHtml(label)}</span>`;
}

function renderOriginalPlan(plan) {
  const lockedAt = (plan.locked_at || "").slice(11, 16);
  const replacePool = (state.today?.forecasts || []).filter(
    (race) => !race.elapsed && !(plan.slots || []).some((slot) => slot.race_key === race.race_key)
  );
  const rows = (plan.slots || [])
    .map((slot, index) => {
      const changed = slot.original
        ? `<span class="plan-changed" title="変更: ${escapeAttr(slot.changed_at || "")}">変更済 (元: ${escapeHtml(slot.original.venue || "")}${escapeHtml(slot.original.race_no ?? "")}R ${escapeHtml(slot.original.start_time || "")})</span>`
        : "";
      const profit = slot.profit != null
        ? `<em class="${slot.profit >= 0 ? "ev-positive" : "ev-caution"}">${slot.profit >= 0 ? "+" : ""}${yen(slot.profit)}</em>`
        : "";
      const canReplace = slot.status === "planned" || slot.status === "missed";
      const replaceUi = canReplace && replacePool.length
        ? `<button type="button" class="button plan-replace-btn" data-replace-slot="${index}">変更</button>
           <span class="plan-replace-picker" id="planReplace-${index}" hidden>
             <select id="planReplaceSel-${index}">
               ${replacePool.map((race) => `<option value="${escapeAttr(race.race_key)}">${escapeHtml(race.venue)}${escapeHtml(race.race_no)}R ${escapeHtml(race.start_time || "")}</option>`).join("")}
             </select>
             <button type="button" class="button primary" data-replace-confirm="${index}">確定</button>
           </span>`
        : "";
      return `<div class="plan-row${slot.is_next ? " is-next" : ""}">
        <span class="time-chip">${escapeHtml(slot.start_time || "--:--")}</span>
        <strong>${escapeHtml(slot.venue || "")}${escapeHtml(slot.race_no ?? "")}R</strong>
        ${planStatusChip(slot)}
        ${profit}
        ${changed}
        ${replaceUi}
      </div>`;
    })
    .join("");
  return `<div class="original-plan">
    <div class="original-plan-head">
      <strong>本日の予定レース(朝に確定${lockedAt ? ` ${lockedAt}` : ""})</strong>
      <span>${(plan.slots || []).length}/${plan.race_limit || 10}R・差し替えると「変更済」が付きます</span>
    </div>
    ${rows || '<div class="empty">予定レースがありません。</div>'}
  </div>`;
}

function togglePlanReplace(index) {
  const picker = el(`planReplace-${index}`);
  if (picker) picker.hidden = !picker.hidden;
}

function confirmPlanReplace(index) {
  const sel = el(`planReplaceSel-${index}`);
  const race = (state.today?.forecasts || []).find((item) => item.race_key === sel?.value);
  if (!race) return;
  bankrollPost("/api/bankroll/replace_slot", {
    slot_index: index,
    race: { race_key: race.race_key, venue: race.venue, race_no: race.race_no, start_time: race.start_time },
  }).then((payload) => {
    if (payload) setStatus(`予定レースを${race.venue}${race.race_no}Rに変更しました`);
  });
}

function renderFinance(finance) {
  const kpis = el("financeKpis");
  if (!kpis || !finance) return;
  const profitClass = (v) => (v >= 0 ? "kpi-up" : "kpi-down");
  const roiPct = finance.roi != null ? `${(finance.roi * 100).toFixed(1)}%` : "-";
  const hitPct = finance.hit_rate != null ? `${(finance.hit_rate * 100).toFixed(1)}%` : "-";
  kpis.innerHTML = [
    { label: "現在資金", value: finance.current_balance != null ? yen(finance.current_balance) : "-", sub: "運用セッション残高", cls: "" },
    { label: "累計収支", value: `${finance.total_profit >= 0 ? "+" : ""}${yen(finance.total_profit)}`, sub: `投資 ${yen(finance.total_stake)} / 払戻 ${yen(finance.total_payout)}`, cls: profitClass(finance.total_profit) },
    { label: "回収率", value: roiPct, sub: "払戻 ÷ 投資", cls: finance.roi >= 1 ? "kpi-up" : "kpi-down" },
    { label: "的中率", value: hitPct, sub: `${finance.wins}勝${finance.losses}敗`, cls: "" },
  ].map((k) => `<div class="kpi-card">
      <span>${escapeHtml(k.label)}</span>
      <strong class="${k.cls}">${escapeHtml(k.value)}</strong>
      <em>${escapeHtml(k.sub)}</em>
    </div>`).join("");
  renderFundChart(finance.series || []);
}

function renderFundChart(series) {
  const svg = el("fundChart");
  if (!svg) return;
  const panel = el("fundChartPanel");
  if (series.length < 2) {
    if (panel) panel.hidden = true;
    return;
  }
  if (panel) panel.hidden = false;
  const W = 640, H = 180, PAD = 28;
  const values = series.map((p) => p.cumulative_profit);
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = Math.max(1, max - min);
  const x = (i) => PAD + (i * (W - PAD * 2)) / (series.length - 1);
  const y = (v) => H - PAD - ((v - min) * (H - PAD * 2)) / span;
  const points = series.map((p, i) => `${x(i).toFixed(1)},${y(p.cumulative_profit).toFixed(1)}`).join(" ");
  const zeroY = y(0);
  const last = series[series.length - 1];
  const lineColor = last.cumulative_profit >= 0 ? "var(--color-profit)" : "var(--color-loss)";
  svg.innerHTML = `
    <line x1="${PAD}" y1="${zeroY.toFixed(1)}" x2="${W - PAD}" y2="${zeroY.toFixed(1)}" class="chart-zero" />
    <polyline points="${points}" fill="none" stroke="${lineColor}" stroke-width="2.5" stroke-linejoin="round" />
    ${series.map((p, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(p.cumulative_profit).toFixed(1)}" r="3.5" fill="${lineColor}"><title>${escapeHtml(p.date)}: ${p.cumulative_profit >= 0 ? "+" : ""}${p.cumulative_profit}円</title></circle>`).join("")}
    <text x="${PAD}" y="14" class="chart-label">累計損益</text>
    <text x="${W - PAD}" y="${(y(last.cumulative_profit) - 8).toFixed(1)}" text-anchor="end" class="chart-value">${last.cumulative_profit >= 0 ? "+" : ""}${last.cumulative_profit}円</text>
  `;
}

function renderOriginalDaily(history) {
  const box = el("originalDaily");
  if (box) {
    if (!history.length || history.every((day) => !day.sessions.length)) {
      box.innerHTML = `<div class="empty">まだ運用記録がありません。車券コンサルの「オリジナル運用(自動)」が毎朝1万円×10Rで自動開始すると、ここに毎日の収支が貯まります。</div>`;
    } else {
      const rows = history
        .filter((day) => day.sessions.length)
        .map((day) => {
          const original = day.original;
          const profitClass = (value) => (value >= 0 ? "ev-positive" : "ev-caution");
          const originalCell = original
            ? `<td class="${profitClass(original.profit)}">${original.profit >= 0 ? "+" : ""}${yen(original.profit)}</td>
               <td>${yen(original.balance)}</td>
               <td>${original.wins}勝${original.losses}敗${original.skips ? `/見送${original.skips}` : ""}</td>
               <td>${original.target_reached ? "達成" : escapeHtml(original.stop_reason || (original.status === "active" ? "運用中" : "停止"))}</td>`
            : `<td colspan="4" class="muted">オリジナル運用なし</td>`;
          return `<tr>
            <td>${escapeHtml(day.date)}</td>
            ${originalCell}
            <td class="${profitClass(day.total_profit)}">${day.total_profit >= 0 ? "+" : ""}${yen(day.total_profit)}</td>
          </tr>`;
        })
        .join("");
      box.innerHTML = `<div class="table-wrap"><table class="data-table compact-table">
        <thead><tr><th>日付</th><th>オリジナル収支</th><th>最終残高</th><th>戦績</th><th>結果</th><th>全スタイル計</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>`;
    }
  }
  // 的中率サマリー(結果ページ)に本日のオリジナル収支を差し込む
  const summaryBox = el("resultsSummary");
  el("originalTodayMetric")?.remove();
  if (summaryBox) {
    const today = history[0];
    const original = today?.original;
    const wrapper = document.createElement("div");
    wrapper.innerHTML = metric([
      "オリジナル収支(本日)",
      original ? `${original.profit >= 0 ? "+" : ""}${yen(original.profit)}` : "-",
      original ? `残高 ${yen(original.balance)} / ${original.wins}勝${original.losses}敗` : "未運用",
      original ? (original.profit >= 0 ? "green" : "amber") : "slate",
    ]);
    const node = wrapper.firstElementChild;
    node.id = "originalTodayMetric";
    summaryBox.appendChild(node);
  }
}

function renderYesterdayResults(yesterday) {
  const sessions = yesterday?.sessions || [];
  if (!sessions.length) return "";
  const rows = sessions
    .map((s) => {
      const profitClass = s.profit >= 0 ? "ev-positive" : "ev-caution";
      const reached = s.target_reached ? "目標達成" : escapeHtml(s.stop_reason || (s.status === "active" ? "運用中だった" : "停止"));
      return `<div class="yesterday-row">
        <span class="style-tag">${escapeHtml(s.style_label)}</span>
        <span>元手 ${yen(s.config.start_amount)} → 最終 ${yen(s.balance)}</span>
        <strong class="${profitClass}">${s.profit >= 0 ? "+" : ""}${yen(s.profit)}</strong>
        <span>${s.wins}勝${s.losses}敗${s.skips ? ` / 見送り${s.skips}` : ""}</span>
        <em>${reached}</em>
      </div>`;
    })
    .join("");
  return `<div class="yesterday-results">
    <div class="yesterday-title">昨日(${escapeHtml(yesterday.date)})の運用結果</div>
    ${rows}
  </div>`;
}

function renderBankrollSetup(payload) {
  const config = payload.last_session?.config || payload.session?.config || {};
  const selected = state.bankrollStyle || config.style || "balance";
  state.bankrollStyle = selected;
  const lastNote = payload.last_session
    ? `<p class="bankroll-last">前回: ${escapeHtml(payload.last_session.stop_reason || "停止")} / 最終残高 ${yen(payload.last_session.state?.balance ?? 0)}</p>`
    : "";
  const styleCards = (payload.styles || [])
    .map(
      (style) => `<button type="button" class="style-card${style.key === selected ? " active" : ""}" data-style="${escapeAttr(style.key)}">
        <strong>${escapeHtml(style.label)}</strong>
        <span>${escapeHtml(style.description)}</span>
        <small>1R上限${style.per_race_cap_pct}% / 損失上限${style.daily_loss_limit_pct}% / ${style.max_consecutive_losses}連敗停止 / EV${style.min_ev}</small>
        <em id="styleEst-${escapeAttr(style.key)}"></em>
      </button>`
    )
    .join("");
  return `<div class="bankroll-setup">
    ${lastNote}
    <div class="style-cards">${styleCards}</div>
    <div class="bankroll-form">
      <label class="field compact"><span>元手</span><input id="brStart" type="number" min="300" step="100" value="${Number(config.start_amount || 1000)}" /></label>
      <label class="field compact"><span>目標額</span><input id="brTarget" type="number" min="400" step="100" value="${Number(config.target_amount || 3000)}" /></label>
      <label class="check-field is-disabled"><input type="checkbox" disabled /><span>自動購入(未対応・常時OFF)</span></label>
      <button id="brStartBtn" class="button primary">運用を開始</button>
    </div>
    <ul class="bankroll-rules">
      ${(state.bankroll?.rules || []).map((rule) => `<li>${escapeHtml(rule)}</li>`).join("")}
    </ul>
  </div>`;
}

function bindBankrollSetup() {
  on("brStartBtn", "click", startBankroll);
  on("brStart", "input", updateStyleEstimates);
  on("brTarget", "input", updateStyleEstimates);
  document.querySelectorAll(".style-card").forEach((node) => {
    node.addEventListener("click", () => selectBankrollStyle(node.dataset.style));
  });
  updateStyleEstimates();
}

function renderBankrollStopBanner(session, brState) {
  const profit = brState?.profit ?? 0;
  const tone = profit >= 0 ? "is-reached" : "is-short";
  return `<div class="bankroll-stop-banner ${tone}">
    <strong>停止: ${escapeHtml(session.stop_reason || "停止")}</strong>
    <span>最終残高 ${yen(brState?.balance ?? 0)}(${profit >= 0 ? "+" : ""}${yen(profit)})</span>
  </div>`;
}

// スタイル別コンサル: 堅実/バランス/冒険を選ぶと、そのスタイルでの今日の買い方を提案する。
// オリジナル(自動運用)とは別で、押した人にだけコンサルする。実際の運用はしない。
const CONSULT_STYLES = {
  kenjitsu: { label: "堅実", points: 3, cap: 0.10, minRank: 3, sort: "rank",
    desc: "本命が堅いレースだけ。点数を絞って的中率を最優先。" },
  balance: { label: "バランス", points: 5, cap: 0.20, minRank: 2, sort: "rank",
    desc: "本命中心に手広く。的中と配当のバランス型。" },
  bouken: { label: "冒険", points: 6, cap: 0.30, minRank: 1, sort: "ev",
    desc: "妙味・高配当を狙う。混戦でも大きく取りにいく。" },
};

function consultCombos(race, points) {
  const ranking = [...(race.entries || [])]
    .filter((e) => e.win_probability != null)
    .sort((a, b) => (b.win_probability || 0) - (a.win_probability || 0))
    .map((e) => Number(e.car_no))
    .filter((n) => Number.isFinite(n) && n > 0);
  if (ranking.length < 3) return [];
  const r1 = ranking[0];
  const seconds = [ranking[1], ranking[2]];
  const thirds = ranking.slice(1, 5);
  const combos = [];
  for (const b of seconds) {
    for (const c of thirds) {
      if (new Set([r1, b, c]).size === 3 && !combos.some((x) => x[0] === r1 && x[1] === b && x[2] === c)) {
        combos.push([r1, b, c]);
      }
    }
  }
  return combos.slice(0, points).map((cars) => ({ cars, label: cars.join("-") }));
}

function consultMoney() {
  const read = (k, d) => {
    try {
      const v = Number(localStorage.getItem(k));
      return Number.isFinite(v) && v > 0 ? v : d;
    } catch (_e) {
      return d;
    }
  };
  return { budget: read("kl_consult_budget", 3000), target: read("kl_consult_target", 5000) };
}

function renderStyleConsult() {
  const section = el("styleConsult");
  if (!section) return;
  const forecasts = (state.today?.forecasts || []).filter((r) => !r.elapsed);
  const done = (state.today?.forecasts || []).length - forecasts.length; // 発走済み数
  section.hidden = false;
  const active = state.consultStyle;
  const money = consultMoney();
  const buttons = Object.entries(CONSULT_STYLES)
    .map(([key, s]) => `<button type="button" class="consult-btn${key === active ? " active" : ""}" data-consult="${key}">${s.label}</button>`)
    .join("");
  section.innerHTML = `
    <div class="capital-head">
      <div><h3>スタイル別コンサル</h3></div>
      <button type="button" class="button consult-update" id="consultUpdate">↻ 結果を反映して更新</button>
    </div>
    <p class="consult-lead">オリジナルは自動で運用中です。ここは<b>自分で買いたい人向け</b>に、選んだ乗り方での狙い目と買い目を提案します(運用はしません)。${done > 0 ? `<span class="consult-done">発走済み${done}Rは除外</span>` : ""}</p>
    <div class="consult-money">
      <label>投資金額 <input type="number" id="consultBudget" value="${money.budget}" min="100" step="100" inputmode="numeric" />円</label>
      <label>目標金額 <input type="number" id="consultTarget" value="${money.target}" min="100" step="100" inputmode="numeric" />円</label>
    </div>
    <div class="consult-buttons">${buttons}</div>
    <div id="consultBody">${active ? consultBody(active, forecasts, money) : '<p class="consult-hint">↑ 乗り方を選ぶと、その日の狙い目を提案します。</p>'}</div>`;
  const saveMoney = (key, id) => {
    const input = el(id);
    if (!input) return;
    input.addEventListener("change", () => {
      try {
        localStorage.setItem(key, String(Math.max(100, Number(input.value) || 0)));
      } catch (_e) {}
      renderStyleConsult();
    });
  };
  saveMoney("kl_consult_budget", "consultBudget");
  saveMoney("kl_consult_target", "consultTarget");
  on("consultUpdate", "click", () => {
    setStatus("結果を反映中…");
    loadToday(); // 最新の結果・発走状況を取り直してコンサルを更新
  });
  section.querySelectorAll("[data-consult]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.consultStyle = state.consultStyle === btn.dataset.consult ? null : btn.dataset.consult;
      renderStyleConsult();
    });
  });
  section.querySelectorAll("[data-open-race]").forEach((btn) => {
    btn.addEventListener("click", () => openRaceByKey(btn.dataset.openRace));
  });
}

function consultBody(styleKey, forecasts, money) {
  const s = CONSULT_STYLES[styleKey];
  if (!s) return "";
  const budget = money?.budget || 3000;
  const target = money?.target || 0;
  let races = forecasts.filter((r) => Number(r.confidence?.rank || 1) >= s.minRank);
  if (s.sort === "ev") {
    races = races.sort((a, b) => (b.value?.ev || 0) - (a.value?.ev || 0) || (b.top3?.[0]?.probability || 0) - (a.top3?.[0]?.probability || 0));
  } else {
    races = races.sort((a, b) => (b.confidence?.rank || 0) - (a.confidence?.rank || 0) || (b.top3?.[0]?.probability || 0) - (a.top3?.[0]?.probability || 0));
  }
  races = races.slice(0, 6);
  // 選出後は発走時刻順に並べ替えて表示(狙い目の優先度は選出のみに使う)
  races = races.slice().sort((a, b) => String(a.start_time || "99:99").localeCompare(String(b.start_time || "99:99")));
  if (!races.length) {
    return `<div class="consult-plan">
      <p class="consult-meta">${escapeHtml(s.desc)}(1レース ${s.points}点)</p>
      <div class="empty">本日は「${s.label}」の狙い目レースがありません。${styleKey === "kenjitsu" ? "本命戦(堅いレース)が無い日です。" : "更新を押すか、時間を置いてご確認ください。"}</div>
    </div>`;
  }
  // 投資金額を狙い目レースに均等配分。1点=1レース額÷点数(100円単位、最低100円)。
  const perRace = Math.max(s.points * 100, Math.round(budget / races.length / 100) * 100);
  const unit = Math.max(100, Math.round(perRace / s.points / 100) * 100);
  let totalStake = 0;
  const cards = races
    .map((race) => {
      const top = race.top3?.[0] || {};
      const combos = consultCombos(race, s.points);
      const raceStake = unit * combos.length;
      totalStake += raceStake;
      const hit = race.hit_estimate?.trifecta;
      const ev = race.value && race.value.ev >= 1 ? `<span class="ev-chip">💎EV ${race.value.ev.toFixed(2)}</span>` : "";
      return `<button type="button" class="consult-race" data-open-race="${escapeAttr(race.race_key)}">
        <div class="consult-race-head">
          <b>${escapeHtml(race.venue || "")}${escapeHtml(race.race_no || "")}R</b>
          <span>${escapeHtml(race.start_time || "")}</span>
          ${badge(race.confidence?.label || "混戦", "confidence-badge")}${ev}
        </div>
        <div class="consult-pick">本命 ${car(top.car_no)} ${playerLink(top.name, top.player_id)} <em>AI勝率${percent(top.probability)}</em></div>
        <div class="consult-buy">${formationHtml(combos) || "-"}</div>
        <div class="consult-foot">
          <span>${combos.length}点 × ${unit.toLocaleString()}円 = <b>${raceStake.toLocaleString()}円</b></span>
          ${hit != null ? `<span class="hit-est">的中目安 ${percent(hit)}</span>` : ""}
        </div>
      </button>`;
    })
    .join("");
  const needProfit = Math.max(0, target - budget);
  const needRoi = totalStake > 0 && target > 0 ? target / totalStake : null;
  const goalNote = target > budget
    ? `目標 <b>${target.toLocaleString()}円</b>(+${needProfit.toLocaleString()}円)には全体で回収率 <b>${needRoi ? (needRoi * 100).toFixed(0) : "—"}%</b> が必要。`
    : "";
  return `<div class="consult-plan">
    <p class="consult-meta">${escapeHtml(s.desc)} — 狙い目 <b>${races.length}レース</b>、1レース <b>${s.points}点</b>、1点 <b>${unit.toLocaleString()}円</b>。想定投資 <b>${totalStake.toLocaleString()}円</b>。${goalNote}</p>
    <div class="consult-races">${cards}</div>
  </div>`;
}

function renderBankrollStatus(session, brState) {
  const config = session.config || {};
  const profit = brState.profit ?? 0;
  const profitClass = profit >= 0 ? "ev-positive" : "ev-caution";
  const styleLabel = styleLabelOf(config.style);
  const styleDef = (state.bankroll?.styles || []).find((item) => item.key === config.style);
  const raceLimit = styleDef?.race_limit;
  const estimate = brState.races_to_target || {};
  const estimateText = raceLimit
    ? `残り${Math.max(0, raceLimit - brState.wins - brState.losses)}R`
    : estimate.fast
    ? `${estimate.fast}〜${estimate.expected ?? "-"}R`
    : brState.balance >= config.target_amount ? "達成" : "-";
  return `<div class="bankroll-status">
    <div class="metric metric-teal"><span>残高</span><strong>${yen(brState.balance)}</strong><em>${raceLimit ? `${raceLimit}R勝負・伸ばせるだけ` : `目標 ${yen(config.target_amount)}`}</em></div>
    <div class="metric metric-${profit >= 0 ? "green" : "amber"}"><span>損益</span><strong class="${profitClass}">${profit >= 0 ? "+" : ""}${yen(profit)}</strong><em>元手 ${yen(config.start_amount)}</em></div>
    <div class="metric metric-blue"><span>${raceLimit ? "残りレース" : "達成目安"}</span><strong>${escapeHtml(estimateText)}</strong><em>${styleLabel}運用 / ${brState.wins}勝${brState.losses}敗</em></div>
    <div class="metric metric-purple"><span>連敗</span><strong>${brState.consecutive_losses}</strong><em>${config.max_consecutive_losses}で停止</em></div>
    <div class="metric metric-slate"><span>損失余地</span><strong>${yen(Math.max(0, brState.day_loss_limit - brState.day_loss))}</strong><em>上限 ${yen(brState.day_loss_limit)}</em></div>
  </div>`;
}

function styleLabelOf(key) {
  const style = (state.bankroll?.styles || []).find((item) => item.key === key);
  return style ? style.label : "バランス";
}

function renderBankrollProposal(proposal) {
  const top = proposal.top_pick || {};
  return `<article class="bankroll-proposal">
    <header class="plan-card-head">
      <div>
        <span class="status-dot">次の提案</span>
        <h4>${escapeHtml(proposal.venue)} ${escapeHtml(proposal.race_no)}R <span class="time-chip">${escapeHtml(proposal.start_time)}</span> <span class="bet-type-badge">${proposal.bet_type === "exacta" ? "2車単" : "3連単"}</span></h4>
        <p>${escapeHtml(proposal.scenario_headline || "")}</p>
      </div>
      <div class="plan-return">
        <strong>投資 ${yen(proposal.total_stake)}</strong>
        <span>EV ${escapeHtml(proposal.ev)} / 見込 ${yen(proposal.expected_return)}</span>
      </div>
    </header>
    <div class="bankroll-tickets">
      ${(proposal.tickets || []).map(renderBankrollTicket).join("")}
    </div>
    <div class="bankroll-proposal-meta">
      <span>本命 ${escapeHtml(top.car_no ?? "-")} ${escapeHtml(top.name || "")}</span>
      <span>信頼度 ${escapeHtml(proposal.confidence)}</span>
      <span>1R予算 ${yen(proposal.budget)}</span>
      <span class="${proposal.live_odds_count ? "odds-live" : "odds-estimated"}">${proposal.live_odds_count ? `ライブオッズ ${proposal.live_odds_count}点` : "推定オッズ"}</span>
    </div>
    <div class="bankroll-actions">
      <button id="brCopyBtn" class="button">買い目をコピー</button>
      <button id="brCommitBtn" class="button primary">購入を記録</button>
      <button id="brSkipBtn" class="button">見送る</button>
      <button id="brRefreshBtn" class="button">更新</button>
    </div>
    <p class="bankroll-note">自動購入は行いません。コピーした買い目をWINTICKET等で確認し、購入後に「購入を記録」を押してください。</p>
  </article>`;
}

function renderBankrollTicket(ticket) {
  const roleClass = ticket.role === "本線" ? "is-main" : ticket.role === "抑え" ? "is-cover" : "is-value";
  const oddsClass = ticket.odds_source === "live" ? "odds-live" : "odds-estimated";
  return `<div class="bankroll-ticket ${roleClass}">
    <span class="ticket-role">${escapeHtml(ticket.role)}</span>
    <b>${escapeHtml(ticket.label)}</b>
    <span>${yen(ticket.stake)}</span>
    <em class="${oddsClass}">${escapeHtml(ticket.odds)}倍 / ${ticket.odds_source === "live" ? "LIVE" : "推定"} / ${percent(ticket.hit_probability)}</em>
    <strong>見込 ${yen(ticket.projected_return)}</strong>
  </div>`;
}

function renderBankrollPending(bet) {
  return `<article class="bankroll-proposal is-pending">
    <header class="plan-card-head">
      <div>
        <span class="status-dot">結果待ち</span>
        <h4>${escapeHtml(bet.venue)} ${escapeHtml(bet.race_no)}R <span class="time-chip">${escapeHtml(bet.start_time || "--:--")}</span></h4>
      </div>
      <div class="plan-return"><strong>投資 ${yen(bet.total_stake)}</strong></div>
    </header>
    <div class="bankroll-tickets">
      ${(bet.tickets || []).map(renderBankrollTicket).join("")}
    </div>
    <div class="bankroll-actions">
      <label class="field compact"><span>払戻額</span><input id="brPayout" type="number" min="0" step="10" placeholder="的中時のみ" /></label>
      <button id="brWonBtn" class="button primary">的中</button>
      <button id="brLostBtn" class="button">不的中</button>
    </div>
  </article>`;
}

function renderBankrollJudged(judged) {
  return `<div class="bankroll-judged">
    <div class="judged-title">これからのレース判定</div>
    ${judged
      .map(
        (race) => `<div class="judged-row ${race.decision === "bet" ? "is-bet" : "is-skip"}${race.is_next ? " is-next" : ""}">
          <span class="time-chip">${escapeHtml(race.start_time || "--:--")}</span>
          <strong>${escapeHtml(race.venue)} ${escapeHtml(race.race_no)}R</strong>
          <span class="judged-decision">${race.decision === "bet" ? "買い" : "見送り"}</span>
          <span class="judged-reason">${escapeHtml(race.reason || "")}</span>
        </div>`
      )
      .join("")}
  </div>`;
}

function renderBankrollHistory(brState) {
  const bets = (brState?.bets || []).filter((bet) => bet.status !== "pending");
  if (!bets.length) return "";
  return `<div class="table-wrap bankroll-history">
    <table class="data-table compact-table">
      <thead><tr><th>レース</th><th>投資</th><th>払戻</th><th>結果</th></tr></thead>
      <tbody>
        ${bets
          .map((bet) => {
            const label =
              bet.status === "won" ? "的中" : bet.status === "lost" ? "不的中" : `見送り${bet.note ? `(${bet.note})` : ""}`;
            return `<tr>
              <td>${escapeHtml(bet.venue || "")} ${escapeHtml(bet.race_no ?? "")}R ${escapeHtml(bet.start_time || "")}</td>
              <td>${bet.total_stake ? yen(bet.total_stake) : "-"}</td>
              <td>${bet.status === "won" ? yen(bet.payout) : bet.status === "lost" ? yen(0) : "-"}</td>
              <td>${escapeHtml(label)}</td>
            </tr>`;
          })
          .join("")}
      </tbody>
    </table>
  </div>`;
}

function setStatus(text) {
  el("status").textContent = text;
}

function setTab(tab) {
  state.activeTab = tab;
  const panel = document.getElementById(`panel-${tab}`);
  if (panel) {
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function renderToday() {
  const payload = state.today;
  if (!payload) return;

  const summary = payload.summary || {};
  const learning = payload.learning_status || {};
  const schedule = payload.schedule_summary || {};
  renderMotionMaker();
  renderStyleConsult(); // 車券コンサル(専用ページ)。要素が無いページでは内部でno-op
  const consultMeta = el("consultMeta");
  if (consultMeta) {
    consultMeta.textContent = `${summary.target_date || ""} / ${summary.count ?? 0}レースを分析済み`;
  }
  if (!el("todayMeta")) return; // 本日の予想パネルがないページ(モーションメーカー・車券コンサル等)
  const greeting = el("greeting");
  if (greeting) {
    const hour = new Date().getHours();
    const hello = hour < 11 ? "おはようございます" : hour < 18 ? "こんにちは" : "こんばんは";
    const total = (summary.count ?? 0) + (summary.elapsed_count ?? 0);
    greeting.textContent = `${hello}。本日は${total}レースを分析済みです`;
  }
  el("todayMeta").textContent = `${summary.target_date || ""} ${summary.after || ""}以降 / ${payload.forecast_file ? "再学習済み" : "未作成"}`;
  el("todayMetrics").innerHTML = [
    ["予想レース", summary.count ?? 0, "本日対象", "teal"],
    ["信頼度 強", summary.high_confidence ?? 0, "軸候補", "green"],
    ["信頼度 中", summary.middle_confidence ?? 0, "相手選び", "blue"],
    ["混戦", summary.mixed ?? 0, "注意", "amber"],
    ["結果付き教師", learning.result_races ?? 0, "学習済み", "purple"],
    ["3年開催索引", schedule.total_events ?? "-", "公式日程", "slate"],
  ]
    .map(metric)
    .join("");
  const forecasts = payload.forecasts || [];
  // 会場の初期選択: ?venue= → ?openRace=のレースの会場 → 開催中/直近の会場
  const params = new URLSearchParams(window.location.search);
  const venueList = venueSummaries(forecasts);
  const validVenues = new Set(venueList.map((v) => v.venue));
  if (!state.selectedVenue || !validVenues.has(state.selectedVenue)) {
    const fromParam = params.get("venue");
    const openKey = params.get("openRace");
    const fromOpen = openKey ? forecasts.find((r) => r.race_key === openKey)?.venue : null;
    state.selectedVenue = (fromParam && validVenues.has(fromParam) && fromParam) || fromOpen || venueList[0]?.venue || null;
  }

  renderVenueTabs(forecasts);
  renderValueBoard(forecasts);
  renderAiPlayerPicks(forecasts);
  renderRecommended(payload.recommended_races || []);

  const rows = filterForecasts(forecasts);
  el("forecastList").innerHTML = rows.length
    ? renderVenueForecastSections(rows)
    : `<div class="empty">この会場の未発走レースはありません。上のタブで他場へ。</div>`;

  // 選手ページ等からの深いリンク(?openRace=race_key)で該当レースを開いてスクロール
  const openRaceKey = params.get("openRace");
  if (openRaceKey && !state.openedRaceOnce) {
    state.openedRaceOnce = true;
    const target = document.getElementById(safeRaceId(openRaceKey));
    if (target) {
      target.open = true;
      setTimeout(() => target.scrollIntoView({ behavior: "smooth", block: "start" }), 60);
    }
  }
}

function safeRaceId(raceKey) {
  return "race-" + String(raceKey || "").replace(/[^a-zA-Z0-9]/g, "-");
}

function minutesToStart(startTime) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(startTime || ""));
  if (!m) return null;
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), Number(m[1]), Number(m[2]));
  return Math.round((start - now) / 60000);
}

function confidenceDots(race) {
  const rank = Number(race.confidence?.rank || 1);
  const prob = Number(race.top3?.[0]?.probability || 0);
  let filled = rank >= 3 ? 4 : rank === 2 ? 3 : 2;
  if (prob >= 0.55) filled = Math.min(5, filled + 1);
  return "●".repeat(filled) + "○".repeat(5 - filled);
}

function suggestedStake() {
  const brState = state.bankroll?.state;
  const config = state.bankroll?.session?.config;
  if (!brState || !config || state.bankroll?.session?.status !== "active") return null;
  const budget = Math.floor((brState.balance * config.per_race_cap_pct) / 100 / 100) * 100;
  return budget > 0 ? budget : null;
}

// 今日の妙味: 実オッズ×較正済みAI確率の期待値(EV)が高いレースを並べる。
// EV>1 = 市場がAIの見立てより安く売っている目。オッズは取得時点のもので変動する。
function renderValueBoard(forecasts) {
  const section = el("valueBoard");
  if (!section) return;
  const rows = (forecasts || [])
    .filter((race) => race.value && !race.elapsed)
    .sort((a, b) => (b.value.ev || 0) - (a.value.ev || 0))
    .slice(0, 5);
  if (!rows.length) {
    section.innerHTML = "";
    section.hidden = true;
    return;
  }
  const ago = (iso) => {
    if (!iso) return "";
    const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    return mins < 60 ? `${mins}分前` : `${Math.floor(mins / 60)}時間前`;
  };
  const items = rows.map((race) => {
    const v = race.value;
    const evClass = v.ev >= 1.5 ? "is-hot" : v.ev >= 1.0 ? "is-good" : "";
    const cars = String(v.label || "").split("-");
    return `<button type="button" class="value-row ${evClass}" data-open-race="${escapeAttr(race.race_key)}">
      <span class="value-race">${escapeHtml(race.venue || "")}${escapeHtml(race.race_no || "")}R <small>${escapeHtml(race.start_time || "")}</small></span>
      <span class="value-ticket">${cars.map(car).join('<em class="fm-to">ー</em>')}</span>
      <span class="value-odds">${v.odds}倍 <small>${ago(v.taken_at)}</small></span>
      <span class="value-prob">AI ${(v.prob * 100).toFixed(1)}%</span>
      <span class="value-ev">EV ${v.ev.toFixed(2)}</span>
    </button>`;
  });
  section.hidden = false;
  section.innerHTML = `
    <header class="value-board-head">
      <div>
        <h3>💎 今日の妙味 <span class="help-tip" tabindex="0" data-tip="EV=実オッズ×AIの的中確率。1.00を超えていれば「市場がAIの見立てより安く売っている」目。締切が近いほどオッズは信頼できます。">?</span></h3>
        <p>実オッズ × AI確率の期待値が高い2車単。オッズは取得時点のもので変動します。</p>
      </div>
    </header>
    <div class="value-rows">${items.join("")}</div>`;
  section.querySelectorAll("[data-open-race]").forEach((btn) => {
    btn.addEventListener("click", () => openRaceByKey(btn.dataset.openRace));
  });
}

// AIが自主的に選ぶ「一押し選手」と「初日ベタ買い候補」。
// 好調・昇級予定・新人の好調・高いAI勝率を加点して選手単位でスコアリングする(レース単位のvalueBoardとは別軸)。
function aiPlayerScore(entry, race) {
  let score = Number(entry.win_probability || 0) * 2.4;
  const reasons = [];
  if (entry.form?.label === "好調") {
    score += 1.8;
    reasons.push("🔥好調");
  }
  if (entry.class_move === "up") {
    score += 1.6;
    reasons.push("昇級⬆");
  }
  const isRookie = entry.term === 129 || entry.term === 130;
  if (isRookie && entry.form?.label !== "不調") {
    score += 1.0;
    reasons.push(`🌱${entry.term}期`);
  }
  const isDay1 = race.is_day1 || race.day_index === 1;
  const flatBet = isDay1 && (entry.form?.label === "好調" || isRookie || Number(entry.win_probability || 0) >= 0.34);
  return { score, reasons, isDay1, flatBet };
}

function renderAiPlayerPicks(forecasts) {
  const section = el("aiPicksBoard");
  if (!section) return;
  const active = (forecasts || []).filter((r) => !r.elapsed);
  const candidates = [];
  for (const race of active) {
    for (const entry of race.entries || []) {
      if (!entry.player_id) continue;
      const { score, reasons, flatBet } = aiPlayerScore(entry, race);
      if (!reasons.length) continue; // 根拠が無い選手は挙げない(捏造しない)
      candidates.push({ entry, race, score, reasons, flatBet });
    }
  }
  if (!candidates.length) {
    section.hidden = true;
    return;
  }
  const seen = new Set();
  const dedup = candidates
    .sort((a, b) => b.score - a.score)
    .filter((c) => {
      if (seen.has(c.entry.player_id)) return false;
      seen.add(c.entry.player_id);
      return true;
    });
  const picks = dedup.slice(0, 5);
  const day1 = dedup.filter((c) => c.flatBet).slice(0, 5);

  // button内にaタグをネストできないため div+role=button にし、選手名リンクだけクリック伝播を止める
  const card = (c) => `<div class="consult-race" tabindex="0" role="button" data-open-race="${escapeAttr(c.race.race_key)}">
    <div class="consult-race-head">
      <b class="ai-pick-name">${playerLink(c.entry.name, c.entry.player_id)}</b>
      <span>${escapeHtml(c.race.venue || "")}${escapeHtml(c.race.race_no || "")}R ${escapeHtml(c.race.start_time || "")}</span>
    </div>
    <div class="consult-pick">${c.reasons.map((r) => `<span class="reason-chip">${escapeHtml(r)}</span>`).join("")} <em>AI勝率${percent(c.entry.win_probability)}</em></div>
  </div>`;

  section.hidden = false;
  section.innerHTML = `
    <div class="capital-head">
      <div><h3>🎯 AIの一押し選手 <span class="help-tip" tabindex="0" data-tip="AIが好調・昇級予定・新人の勢いなどから自主的に選んだ注目選手です。全レースの網羅ではありません。">?</span></h3></div>
      <span class="page-meta">好調・昇級予定・新人の勢いから自動選出</span>
    </div>
    <div class="consult-races">${picks.map(card).join("")}</div>
    ${day1.length ? `<div class="ai-picks-day1">
      <div class="capital-head"><div><h3>今節初日のベタ買い候補</h3></div><span class="page-meta">初日から追いたい注目選手</span></div>
      <div class="consult-races">${day1.map(card).join("")}</div>
    </div>` : ""}`;
  section.querySelectorAll(".ai-pick-name a").forEach((a) => a.addEventListener("click", (event) => event.stopPropagation()));
  section.querySelectorAll("[data-open-race]").forEach((btn) => {
    btn.addEventListener("click", () => openRaceByKey(btn.dataset.openRace));
    btn.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        btn.click();
      }
    });
  });
}

function renderRecommended(races) {
  const section = el("recommendedSection");
  if (!section) return;
  if (!races.length) {
    section.innerHTML = "";
    section.hidden = true;
    return;
  }
  section.hidden = false;
  const stake = suggestedStake();
  const cards = races.slice(0, 3).map((race) => {
    const top = race.top3?.[0] || {};
    const mins = minutesToStart(race.start_time);
    const minsText = mins == null ? "" : mins <= 0 ? "発走済み" : `発走まで ${mins}分`;
    return `<article class="market-card">
      <header>
        <strong>${escapeHtml(race.venue || "")} ${escapeHtml(race.race_no || "")}R</strong>
        <span class="market-countdown">${escapeHtml(minsText)}</span>
      </header>
      <div class="market-main">
        <span class="market-label">AI本命</span>
        <div class="market-pick">${car(top.car_no)} <b>${escapeHtml(top.name || "-")}</b></div>
        <div class="market-prob"><em>${percent(top.probability)}</em><small>勝率予測</small></div>
      </div>
      <div class="market-meta">
        <span>信頼度 <b class="market-dots">${confidenceDots(race)}</b></span>
        ${stake ? `<span>推奨投資 <b>${yen(stake)}</b></span>` : ""}
      </div>
      <button type="button" class="button market-open" data-open-race="${escapeAttr(race.race_key)}">予想を見る</button>
    </article>`;
  }).join("");
  section.innerHTML = `
    <div class="recommended-head">
      <span class="recommended-dot">AIオススメ</span>
      <h3>注目レース</h3>
    </div>
    <div class="market-grid">${cards}</div>
  `;
  section.querySelectorAll("[data-open-race]").forEach((btn) => {
    btn.addEventListener("click", () => openRaceByKey(btn.dataset.openRace));
  });
}

function renderVenueForecastSections(rows) {
  return groupForecastsByVenue(rows)
    .map((group) => {
      const strong = group.races.filter((race) => race.confidence?.rank >= 2).length;
      const first = group.races[0];
      const last = group.races[group.races.length - 1];
      return `<section class="venue-section">
        <header class="venue-section-head">
          <div>
            <span class="status-dot">受付中</span>
            <h3>${escapeHtml(group.venue)}</h3>
            <p>${group.races.length}レース / ${escapeHtml(first.start_time || "--:--")} - ${escapeHtml(last.start_time || "--:--")}</p>
          </div>
          <div class="venue-section-stats">
            ${badge(`自信 ${strong}`, "confidence-badge")}
            <span>${escapeHtml(first.race_date || "")}</span>
          </div>
        </header>
        <div class="venue-ribbon-list">
          ${group.races.map(renderForecastCard).join("")}
        </div>
      </section>`;
    })
    .join("");
}

function groupForecastsByVenue(rows) {
  const groups = new Map();
  for (const race of rows) {
    const venue = race.venue || "未設定";
    if (!groups.has(venue)) groups.set(venue, []);
    groups.get(venue).push(race);
  }
  return [...groups.entries()]
    .map(([venue, races]) => ({
      venue,
      // 会場内は開始時刻の早い順
      races: races.sort((a, b) => String(a.start_time || "99:99").localeCompare(String(b.start_time || "99:99"))),
    }))
    // 会場は最初のレースの開始時刻が早い順
    .sort((a, b) => String(a.races[0]?.start_time || "99:99").localeCompare(String(b.races[0]?.start_time || "99:99")));
}


// 会場ごとの状況をまとめる(タブと会場ページ用)
function venueSummaries(forecasts) {
  const venues = new Map();
  for (const race of forecasts) {
    const venue = race.venue || "未設定";
    const current = venues.get(venue) || { venue, total: 0, elapsed: 0, upcoming: [] };
    current.total += 1;
    if (race.elapsed) current.elapsed += 1;
    else current.upcoming.push(race);
    venues.set(venue, current);
  }
  return [...venues.values()]
    .map((item) => {
      item.upcoming.sort((a, b) => String(a.start_time || "99:99").localeCompare(String(b.start_time || "99:99")));
      item.isLive = item.elapsed > 0 && item.upcoming.length > 0;
      item.done = item.upcoming.length === 0;
      item.nextTime = item.upcoming[0]?.start_time || "--:--";
      item.nextRaceNo = item.upcoming[0]?.race_no || "-";
      return item;
    })
    .sort((a, b) => {
      if (a.done !== b.done) return a.done ? 1 : -1; // 終了会場は最後
      if (a.isLive !== b.isLive) return a.isLive ? -1 : 1; // 開催中を先頭
      return String(a.nextTime).localeCompare(String(b.nextTime));
    });
}

// netkeirin風: 上部の会場タブ。クリックでその会場の予想ページ(1会場分だけ表示)へ切替
function renderVenueTabs(forecasts) {
  const nav = el("venueTabs");
  if (!nav) return;
  const items = venueSummaries(forecasts);
  if (!items.length) {
    nav.innerHTML = "";
    return;
  }
  nav.innerHTML = items
    .map(
      (item) => `<button type="button" class="venue-tab${item.venue === state.selectedVenue ? " active" : ""}${item.isLive ? " is-live" : ""}${item.done ? " is-done" : ""}" data-venue="${escapeAttr(item.venue)}">
        <b>${item.isLive ? '<i class="live-dot"></i>' : ""}${escapeHtml(item.venue)}</b>
        <small>${item.done ? "終了" : `${escapeHtml(item.nextRaceNo)}R ${escapeHtml(item.nextTime)}`}</small>
      </button>`
    )
    .join("");
  nav.querySelectorAll("[data-venue]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.selectedVenue = btn.dataset.venue;
      const url = new URL(window.location.href);
      url.searchParams.set("venue", state.selectedVenue);
      url.searchParams.delete("openRace");
      window.history.replaceState(null, "", url.toString());
      renderToday();
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  });
}

// どのページ・どの会場のレースでも開けるヘルパー。
// レース一覧が無いページや別会場のレースなら、会場を切り替えてから開く。
function openRaceByKey(raceKey) {
  const race = (state.today?.forecasts || []).find((r) => r.race_key === raceKey);
  const target = document.getElementById(safeRaceId(raceKey));
  if (target) {
    target.open = true;
    target.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  if (race && el("forecastList")) {
    state.selectedVenue = race.venue || state.selectedVenue;
    renderToday();
    const after = document.getElementById(safeRaceId(raceKey));
    if (after) {
      after.open = true;
      setTimeout(() => after.scrollIntoView({ behavior: "smooth", block: "start" }), 60);
    }
    return;
  }
  // レース一覧が無いページ(車券コンサル等) → トップの該当レースへ
  window.location.href = `index.html?openRace=${encodeURIComponent(raceKey)}`;
}

function filterForecasts(rows) {
  const confidenceFilter = el("confidenceFilter");
  const confidence = confidenceFilter ? confidenceFilter.value : "all";
  return rows.filter((race) => {
    if (race.elapsed) return false; // 発走済みは一覧から外す(モーションメーカーでは選べる)
    if (state.selectedVenue && (race.venue || "未設定") !== state.selectedVenue) return false; // 選択中の会場だけ
    if (confidence !== "all" && race.confidence?.label !== confidence) return false;
    return true;
  });
}

function renderBankCard(race) {
  const bank = race.bank;
  if (!bank) return "";
  const km = bank.kimarite || {};
  const kmBar = ["逃げ", "捲り", "差し"]
    .map((key) => {
      const pct = Math.round((km[key] || 0) * 100);
      const cls = key === "逃げ" ? "km-nige" : key === "捲り" ? "km-makuri" : "km-sashi";
      return `<div class="km-seg ${cls}" style="width:${pct}%" title="${key} ${pct}%"><span>${key}${pct}</span></div>`;
    })
    .join("");
  const biasClass = (bank.bank_bias || 0) > 0.1 ? "is-nige" : (bank.bank_bias || 0) < -0.1 ? "is-sashi" : "is-flat";
  const chips = [];
  if (bank.track_distance) chips.push(`周長${bank.track_distance}m`);
  if (bank.straight) chips.push(`みなし直線${bank.straight}m`);
  if (bank.is_indoor) chips.push("屋内");
  if (race.weather?.weather) chips.push(`天気: ${race.weather.weather}${race.weather.is_rain ? "☔" : ""}`);
  return `<section class="bank-card ${biasClass}">
    <div class="bank-card-head">
      <h4>${escapeHtml(bank.name || race.venue || "")}バンク</h4>
      <span class="bank-tendency">${escapeHtml(bank.tendency || "")}</span>
    </div>
    <div class="bank-chips">${chips.map((c) => `<span>${escapeHtml(c)}</span>`).join("")}</div>
    ${Object.keys(km).length ? `<div class="km-bar" aria-label="決まり手分布">${kmBar}</div>` : ""}
    ${bank.net_notes ? `<p class="bank-notes">${escapeHtml(bank.net_notes)}</p>` : ""}
  </section>`;
}

function renderForecastCard(race) {
  const top = race.top3?.[0] || {};
  const second = race.top3?.[1] || {};
  const third = race.top3?.[2] || {};
  const confidence = race.confidence || {};
  const tickets = (race.tickets || []).map(ticketChip).join("");
  const primaryTickets = (race.tickets || [])
    .slice(0, 4)
    .map((ticket) => ticket.label)
    .join(" / ");
  const lines = (race.lines || []).map(lineItem).join("");
  const signals = (race.comment_signals || []).map((signal) => `<li>${escapeHtml(signal)}</li>`).join("");

  return `<details class="race-ribbon ${confidenceClass(confidence.label)}" id="${safeRaceId(race.race_key)}">
    <summary class="ribbon-summary">
      <span class="ribbon-time">${escapeHtml(race.start_time || "--:--")}</span>
      <strong>${escapeHtml(race.race_no || "")}R</strong>
      <span class="ribbon-main">${car(top.car_no)} ${escapeHtml(top.name || "-")} <em class="prob-tag">AI勝率${percent(top.probability)}</em>${race.hit_estimate ? `<em class="prob-tag hit">2車単的中目安${percent(race.hit_estimate.exacta)}</em>` : ""}</span>
      <span class="ribbon-tickets">${escapeHtml(primaryTickets || "-")}</span>
      ${race.class_group ? badge(race.class_group, race.is_girls ? "girls-badge" : "class-badge") : ""}
      ${race.weather?.is_rain ? badge("雨", "rain-badge") : ""}
      ${payoutBadge(race)}
      ${race.value && race.value.ev >= 1 ? `<span class="ev-chip">💎EV ${race.value.ev.toFixed(2)}</span>` : ""}
      ${badge(confidence.label || "混戦", "confidence-badge")}
    </summary>
    <div class="ribbon-body">
      <article class="race-card ribbon-card ${confidenceClass(confidence.label)}">
        <header class="race-card-head">
          <div class="race-title-block">
            <span class="time-chip">${escapeHtml(race.start_time || "--:--")}</span>
            <h3>${escapeHtml(race.venue || "")} ${escapeHtml(race.race_no || "")}R</h3>
            <p>${escapeHtml(race.event || race.race_class || "")}</p>
          </div>
          <div class="confidence-summary">
            ${badge(confidence.label || "混戦", "confidence-badge")}
            ${payoutBadge(race)}
            ${race.scenario?.pattern ? badge(race.scenario.pattern, "pattern-badge") : ""}
            <span>${escapeHtml(confidence.reason || "")}</span>
          </div>
        </header>

        <div class="prediction-summary">
          <div class="main-pick">
            <span>本命 / AI勝率</span>
            <strong>${car(top.car_no)} ${playerLink(top.name, top.player_id, rookieBadge(top.term))}${classMoveBadge(top.class_move)} ${top.form ? playerFormChip(top.form) : ""}</strong>
            <em>${percent(top.probability)}<small class="unit-label">AI推定勝率</small></em>
          </div>
          <div class="top3-row">
            ${rankPill(1, top)}
            ${rankPill(2, second)}
            ${rankPill(3, third)}
          </div>
          <div class="bet-block">
            <div class="bet-label">3連単候補 <small>フォーメーション(＝順不同 / ー流し)</small>${race.hit_estimate ? `<span class="hit-est">的中目安 ${percent(race.hit_estimate.trifecta)}</span>` : ""}</div>
            ${formationHtml(race.tickets || []) || `<div class="ticket-row">${tickets}</div>`}
            <details class="ticket-detail"><summary>全${(race.tickets || []).length}点を見る</summary><div class="ticket-row">${tickets}</div></details>
          </div>
          ${(race.exacta && race.exacta.length) ? `<div class="bet-block">
            <div class="bet-label">2車単候補 <small>軸1着固定・的中率重視</small>${race.hit_estimate ? `<span class="hit-est">的中目安 ${percent(race.hit_estimate.exacta)}</span>` : ""}</div>
            ${formationHtml(race.exacta) || ""}
            <details class="ticket-detail"><summary>全${race.exacta.length}点を見る</summary><div class="ticket-row exacta-row">${race.exacta.map((t) => `<span class="ticket-chip exacta-chip${t.suji ? " is-suji" : ""}">${escapeHtml(t.label)}${t.suji ? '<em class="suji-tag">スジ</em>' : ""}${t.ev ? `<em class="ev-mini">${t.live_odds}倍 EV${t.ev.toFixed(2)}</em>` : ""}</span>`).join("")}</div></details>
          </div>` : ""}
        </div>

        ${renderLineDiagram(race.lines || [], race.top3 || [])}

        <details class="details reason-details">
          <summary>📖 展開と根拠を見る <small>S取り・ライン・バンク・展開再生</small></summary>
          <div class="forecast-grid">
            <section class="analysis-block">
              <h4>展開</h4>
              <p class="headline">${escapeHtml(race.scenario?.headline || "")}</p>
              ${(race.scenario?.sequence || []).length ? `<ol class="scenario-steps">${race.scenario.sequence.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}</ol>` : ""}
              <p>${escapeHtml(race.scenario?.flow || "")}</p>
              <p>${escapeHtml(race.scenario?.watch || "")}</p>
              <p class="risk-text">${escapeHtml(race.scenario?.upset || "")}</p>
            </section>
            <section class="analysis-block">
              <h4>ライン/関係性</h4>
              <ul class="line-list">${lines}</ul>
            </section>
          </div>
          ${renderBankCard(race)}
          <div class="race-motion" data-motion-race="${escapeAttr(race.race_key || "")}">
            <button type="button" class="button motion-btn" data-race="${escapeAttr(race.race_key || "")}">▶ 展開を再生</button>
            <div class="motion-stage" hidden>
              <div class="motion-caption">周回中</div>
              <svg class="motion-svg" viewBox="0 0 640 300" role="img" aria-label="展開予想アニメーション"></svg>
            </div>
          </div>
        </details>

        <details class="details">
          <summary>コメント根拠と全選手</summary>
          <div class="detail-grid">
            <section>
              <h4>コメントの心理シグナル</h4>
              <ul class="signal-list">${signals}</ul>
            </section>
            <section>
              <h4>出走表</h4>
              <div class="mini-table">${renderMiniEntries(race.entries || [])}</div>
            </section>
          </div>
          <a class="source-link" href="${escapeAttr(race.url || "#")}" target="_blank" rel="noreferrer">WINTICKETで開く</a>
        </details>
      </article>
    </div>
  </details>`;
}

// 選手の調子チップ(実着順+JKA公式の得点推移から。データ不足は「—」で捏造しない)
function playerFormChip(form) {
  if (!form) return '<span class="muted">—</span>';
  const cls = form.label === "好調" ? "hot" : form.label === "不調" ? "cold" : "even";
  const icon = form.label === "好調" ? "🔥" : form.label === "不調" ? "🥶" : "➖";
  const parts = [];
  if (form.races) parts.push(`直近${form.races}走`);
  if (form.top3_rate != null) parts.push(`3着内${Math.round(form.top3_rate * 100)}%`);
  if (form.avg_finish != null) parts.push(`平均${form.avg_finish}着`);
  if (form.official_delta != null) {
    parts.push(`公式: 近4ヶ月${form.score_recent} vs 今期${form.score_now}(${form.official_delta >= 0 ? "+" : ""}${form.official_delta})`);
  } else if (form.score_delta != null) {
    parts.push(`得点${form.score_delta >= 0 ? "+" : ""}${form.score_delta}`);
  }
  return `<span class="form-icon ${cls}" title="${escapeAttr(parts.join(" / "))}">${icon}${form.label}</span>`;
}

// 直近着順のミニ推移(左が古く右が最新。1着=金/2-3着=緑/それ以外=灰)
function finishTrail(form) {
  if (!form || !(form.finishes || []).length) return "";
  return `<span class="finish-trail">${form.finishes
    .slice(-8)
    .map((f) => `<i class="${f === 1 ? "f1" : f <= 3 ? "f3" : "fx"}">${f}</i>`)
    .join("")}</span>`;
}

function renderMiniEntries(entries) {
  return `<table class="data-table compact-table">
    <thead>
      <tr>
        <th>車</th>
        <th>選手</th>
        <th>脚質</th>
        <th>得点</th>
        <th>調子<span class="help-tip" tabindex="0" data-tip="当ラボが蓄積した実戦の着順(左が古く右が最新)と競走得点の増減から判定。3走未満は判定しません。">?</span></th>
        <th>コメント</th>
        <th>AI根拠</th>
      </tr>
    </thead>
    <tbody>
      ${entries
        .map(
          (row) => {
            const od = row.form ? (row.form.official_delta != null ? row.form.official_delta : row.form.score_delta) : null;
            return `<tr>
            <td>${car(row.car_no)}</td>
            <td><strong>${playerLink(row.name, row.player_id, rookieBadge(row.term))}</strong>${classMoveBadge(row.class_move)}<br><span class="muted">${escapeHtml(row.prefecture || "")} ${escapeHtml(row.class || "")}</span></td>
            <td>${escapeHtml(row.style || "")}</td>
            <td>${num(row.racing_score)}${od != null ? `<br><span class="score-delta ${od >= 0 ? "kpi-up" : "kpi-down"}" title="${row.form.official_delta != null ? "JKA公式: 直近4ヶ月得点と今期適用得点の差" : "当ラボ収集分の得点変化"}">${od >= 0 ? "▲" : "▼"}${Math.abs(od)}</span>` : ""}</td>
            <td>${playerFormChip(row.form)}${finishTrail(row.form)}</td>
            <td>${escapeHtml(row.comment || "")}</td>
            <td>${(row.reasons || []).map((reason) => `<span class="reason-chip">${escapeHtml(reason)}</span>`).join("")}</td>
          </tr>`;
          }
        )
        .join("")}
    </tbody>
  </table>`;
}

function renderLearn() {
  if (!el("learnGrid")) return;
  const status = state.learning || {};
  const model = state.learnedModel || {};
  const training = model.training || {};
  const metrics = model.metrics || {};
  const schedule = state.today?.schedule_summary || {};
  const items = [
    ["保存レース", status.races ?? 0],
    ["保存選手", status.entries ?? 0],
    ["結果付きレース", status.result_races ?? 0],
    ["教師行", status.result_entries ?? training.rows ?? 0],
    ["予想保存", status.predictions ?? 0],
    ["3年開催索引", schedule.total_events ?? "-"],
    ["Top1精度", metrics.top1_accuracy != null ? percent(metrics.top1_accuracy) : "-"],
    ["LogLoss", metrics.log_loss ?? "-"],
    ["DB", status.db_path || "-"],
  ];
  el("learnModelLabel").textContent = model.name || "transparent-baseline + online-logistic-win";
  el("learnGrid").innerHTML = [
    ["レース", status.races ?? 0, "保存", "teal"],
    ["教師", status.result_entries ?? training.rows ?? 0, "行", "purple"],
    ["結果付き", status.result_races ?? 0, "レース", "green"],
    ["3年索引", schedule.total_events ?? "-", "開催", "slate"],
  ]
    .map(metric)
    .join("");
  el("learnBody").innerHTML = items
    .map(([key, value]) => `<tr><td>${escapeHtml(key)}</td><td>${escapeHtml(value)}</td></tr>`)
    .join("");
}

function metric([label, value, caption, tone = "slate"]) {
  return `<div class="metric metric-${tone}">
    <span>${escapeHtml(label)}</span>
    <strong>${escapeHtml(value)}</strong>
    ${caption ? `<em>${escapeHtml(caption)}</em>` : ""}
  </div>`;
}

function badge(text, className = "") {
  return `<span class="badge ${className}">${escapeHtml(text)}</span>`;
}

// 新人(129期・130期)マーク。デビュー間もない選手は下位級で強いことが多い注目枠。
function rookieBadge(term) {
  const t = Number(term);
  if (t !== 129 && t !== 130) return "";
  return `<span class="rookie-badge" title="${t}期・デビュー間もない新人">🌱${t}期</span>`;
}

// 次期級班の昇降(JKA公式の次期級班より)
function classMoveBadge(move) {
  if (move === "up") return '<span class="move-badge up" title="次期は昇級予定(公式)">昇級⬆</span>';
  if (move === "down") return '<span class="move-badge down" title="次期は降級予定(公式)">降級⬇</span>';
  return "";
}

// 選手名を個人ページへのリンクにする(player_idが無ければ素のテキスト)
function playerLink(name, playerId, extra) {
  const label = `${escapeHtml(name || "-")}${extra || ""}`;
  if (!playerId) return label;
  return `<a href="player.html?id=${encodeURIComponent(playerId)}" class="player-link">${label}</a>`;
}

function rankPill(rank, row) {
  if (!row || row.car_no == null) return "";
  return `<span class="rank-pill"><b>${rank}</b>${car(row.car_no)}<span>${playerLink(row.name, row.player_id, rookieBadge(row.term))}</span><em>${percent(row.probability)}</em></span>`;
}

function ticketChip(ticket) {
  const suji = ticket.suji ? '<em class="suji-tag">スジ</em>' : "";
  return `<span class="ticket-chip${ticket.suji ? " is-suji" : ""}">${escapeHtml(ticket.label)}${suji}</span>`;
}

// 買い目を競輪新聞式のフォーメーション表記にまとめる。
// 軸(1着)ごとに1行=別線が別行で見やすい。＝は順不同(ボックス)、ーは流し。
function formationHtml(combos) {
  const cars = (combos || []).map((t) => (t.cars || []).map(Number)).filter((c) => c.length >= 2 && c.every(Number.isFinite));
  if (!cars.length) return "";
  const isTri = cars[0].length >= 3;

  // 1着ごとに 2着集合・3着集合をまとめる
  const byFirst = new Map();
  for (const c of cars) {
    if (!byFirst.has(c[0])) byFirst.set(c[0], { p2: new Set(), p3: new Set() });
    const g = byFirst.get(c[0]);
    g.p2.add(c[1]);
    if (isTri && Number.isFinite(c[2])) g.p3.add(c[2]);
  }
  let rows = [...byFirst.entries()].map(([first, g]) => ({
    firsts: [first],
    p2: [...g.p2],
    p3: isTri ? [...g.p3] : [],
  }));

  // 2車ヘッドの対称ボックス(A→Bのみ / B→Aのみ・3着が同じ)を A＝B に統合
  const key = (arr) => [...arr].sort((x, y) => x - y).join(",");
  const out = [];
  const used = new Set();
  for (let i = 0; i < rows.length; i += 1) {
    if (used.has(i)) continue;
    let boxed = false;
    for (let j = i + 1; j < rows.length; j += 1) {
      if (used.has(j)) continue;
      const A = rows[i];
      const B = rows[j];
      const a = A.firsts[0];
      const b = B.firsts[0];
      const symmetric = A.p2.length === 1 && B.p2.length === 1 && A.p2[0] === b && B.p2[0] === a;
      const same3 = !isTri || key(A.p3) === key(B.p3);
      if (symmetric && same3) {
        out.push({ firsts: [a, b], box: true, p2: [], p3: A.p3 });
        used.add(i);
        used.add(j);
        boxed = true;
        break;
      }
    }
    if (!boxed && !used.has(i)) out.push({ ...rows[i], box: false });
  }

  const grp = (arr) => arr.map(car).join("");
  const rowHtml = (r) => {
    const head = r.box ? r.firsts.map(car).join('<em class="fm-eq">＝</em>') : car(r.firsts[0]);
    const parts = [head];
    if (r.p2 && r.p2.length) parts.push(grp(r.p2));
    if (isTri && r.p3 && r.p3.length) parts.push(grp(r.p3));
    return `<div class="formation-row">${parts.join('<em class="fm-to">ー</em>')}</div>`;
  };
  return `<div class="formation">${out.map(rowHtml).join("")}</div>`;
}

function renderLineDiagram(lines, top3) {
  if (!lines.length) return "";
  const topCars = new Set(top3.map((row) => Number(row.car_no)));
  // 同じ車番が複数ラインに重複して入るデータがあるため、先に出たラインを正として重複除去
  const seenCars = new Set();
  const dedupedLines = lines
    .map((line) => ({
      ...line,
      members: (line.members || []).filter((member) => {
        const no = Number(member.car_no);
        if (!Number.isFinite(no) || seenCars.has(no)) return false;
        seenCars.add(no);
        return true;
      }),
    }))
    .filter((line) => line.members.length);
  // WINTICKET本家と同じ「一列」表示: 全ラインを1行に横並び(左が先頭)
  const chains = dedupedLines
    .map((line, index) => {
      const color = (index % 5) + 1;
      const members = (line.members || [])
        .map((member) => {
          const highlight = topCars.has(Number(member.car_no)) ? " is-highlight" : "";
          const inner = `${car(member.car_no)}<small>${escapeHtml((member.name || "").slice(0, 4))}</small>`;
          // 選手ページへのリンク(player_idが無い選手はそのまま表示)
          return member.player_id
            ? `<a class="line-flat-member${highlight}" href="player.html?id=${encodeURIComponent(member.player_id)}" title="${escapeAttr(member.name || "")} ${escapeAttr(member.style || "")} — 選手ページへ">${inner}</a>`
            : `<span class="line-flat-member${highlight}" title="${escapeAttr(member.name || "")} ${escapeAttr(member.style || "")}">${inner}</span>`;
        })
        .join("");
      return `<span class="line-flat-chain line-color-${color}">${members}</span>`;
    })
    .join("");
  return `<div class="line-flat" aria-label="並び予想">
    <div class="line-flat-head">
      <span>並び予想</span>
      <em>左が先頭 / かたまり=ライン</em>
    </div>
    <div class="line-flat-row">${chains}</div>
  </div>`;
}

function lineItem(line) {
  return `<li><b>${escapeHtml(line.label)}</b><span>${escapeHtml(line.relation)}</span></li>`;
}

function car(value) {
  const carNo = Number(value);
  const className = Number.isFinite(carNo) ? ` car-${carNo}` : "";
  return `<span class="car${className}">${escapeHtml(value ?? "-")}</span>`;
}

function confidenceClass(label) {
  if (label === "本命") return "is-strong";
  if (label === "順当") return "is-medium";
  return "is-mixed";
}

// 高配当になりそうな予想か判定する。
// 本命(AI勝率)が抜けていない=どの車にもチャンスがある=当たれば配当が付く。
// さらに本命の車番が大きい(外枠・非人気になりやすい)ほど妙味と見る。
function payoutTier(race) {
  const top = (race.top3 || [])[0] || {};
  const p = Number(top.probability || 0);
  if (!p) return null;
  const outerAxis = Number(top.car_no || 0) >= 5; // 5番以降を軸=人気薄になりやすい
  if (p <= 0.18 || (p <= 0.20 && outerAxis)) return "super"; // 大穴級(希少)
  if (p <= 0.22 || (p <= 0.26 && outerAxis)) return "high";  // 高配当ねらい
  return null;
}

function payoutBadge(race) {
  const tier = payoutTier(race);
  if (!tier) return "";
  const label = tier === "super" ? "💰大穴級" : "💰高配当";
  return `<span class="payout-badge${tier === "super" ? " is-super" : ""}">${label}</span>`;
}

function formatLineup(lineup) {
  if (!lineup.length) return "並び未取得";
  return lineup.map((line) => line.join("-")).join(" / ");
}

function percent(value) {
  const n = Number(value || 0);
  return `${Math.round(n * 1000) / 10}%`;
}

function score(value) {
  return `${Math.round(Number(value || 0) * 1000) / 10}`;
}

function num(value) {
  if (value === null || value === undefined || value === "") return "-";
  const n = Number(value);
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

function yen(value) {
  return `${Math.round(Number(value || 0)).toLocaleString("ja-JP")}円`;
}

function isRiskTone(tone) {
  return tone === "不安大" || tone === "不安含み";
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
