const state = {
  today: null,
  bundle: null,
  activeTab: "today",
  learning: null,
  learnedModel: null,
  capitalPlan: null,
  capitalPlanTimer: null,
};

const STATIC_API = {
  "/api/today": "static-api/today.json",
  "/api/sample": "static-api/sample.json",
  "/api/learn/status": "static-api/learn-status.json",
  "/api/capital_plan": "static-api/capital-plan.json",
};

const el = (id) => document.getElementById(id);

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
    return fetch(fallback);
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
        error: "公開版では保存・手動学習は使えません。ローカル版で実行してください。",
      }),
    };
  }
}

document.addEventListener("DOMContentLoaded", () => {
  el("refreshTodayBtn").addEventListener("click", loadToday);
  el("sampleBtn").addEventListener("click", loadSample);
  el("fetchBtn").addEventListener("click", fetchRace);
  el("storeBtn").addEventListener("click", storeCurrentRace);
  el("recordBtn").addEventListener("click", recordManualResult);
  el("planBtn").addEventListener("click", loadCapitalPlan);
  el("autoRollToggle").addEventListener("change", updateCapitalPlanTimer);
  el("searchInput").addEventListener("input", renderToday);
  el("confidenceFilter").addEventListener("change", renderToday);
  loadLearnStatus();
  loadToday();
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

async function loadSample() {
  setTab("race");
  setStatus("サンプル読み込み中");
  const res = await apiGet("/api/sample");
  const bundle = await res.json();
  if (!bundle.ok) {
    setStatus(bundle.error || "読み込み失敗");
    return;
  }
  state.bundle = bundle;
  renderRace();
  setStatus("サンプルを表示中");
}

async function fetchRace() {
  const url = el("raceUrl").value.trim();
  if (!url) {
    setStatus("URLを入力してください");
    return;
  }
  setTab("race");
  setStatus("取得中");
  try {
    const res = await apiGet(`/api/fetch?url=${encodeURIComponent(url)}`);
    const bundle = await res.json();
    if (!bundle.ok) {
      setStatus(bundle.error || "取得失敗");
      return;
    }
    state.bundle = bundle;
    renderRace();
    const count = bundle.race?.raw_quality?.entrant_count ?? bundle.race?.entrants?.length ?? 0;
    setStatus(`${count}選手を取得`);
  } catch (error) {
    setStatus(`取得失敗: ${error.message}`);
  }
}

async function storeCurrentRace() {
  const url = el("raceUrl").value.trim();
  if (!url) {
    setStatus("URLを入力してください");
    return;
  }
  setTab("race");
  setStatus("保存中");
  try {
    const res = await apiGet(`/api/learn/fetch_store?url=${encodeURIComponent(url)}`);
    const payload = await res.json();
    if (!payload.ok) {
      setStatus(payload.error || "保存失敗");
      return;
    }
    state.bundle = { race: payload.race, prediction: payload.prediction };
    state.learning = payload.status;
    state.learnedModel = payload.model;
    renderRace();
    renderLearn();
    setStatus(payload.has_result ? "保存して学習しました" : "保存しました");
  } catch (error) {
    setStatus(`保存失敗: ${error.message}`);
  }
}

async function recordManualResult() {
  const url = el("raceUrl").value.trim();
  const order = el("resultOrder").value.trim();
  if (!url || !order) {
    setStatus("URLと着順を入力してください");
    return;
  }
  setTab("race");
  setStatus("結果学習中");
  try {
    const res = await apiPost("/api/learn/manual_result", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, order }),
    });
    const payload = await res.json();
    if (!payload.ok) {
      setStatus(payload.error || "学習失敗");
      return;
    }
    state.bundle = { race: payload.race, prediction: payload.prediction };
    state.learning = payload.status;
    state.learnedModel = payload.model;
    renderRace();
    renderLearn();
    setStatus(`結果で学習: ${payload.order.join("-")}`);
  } catch (error) {
    setStatus(`学習失敗: ${error.message}`);
  }
}

async function loadLearnStatus() {
  try {
    const res = await apiGet("/api/learn/status");
    const payload = await res.json();
    if (payload.ok) {
      state.learning = payload.status;
      renderLearn();
    }
  } catch {
    state.learning = null;
  }
}

async function loadCapitalPlan() {
  const start = Number(el("startAmount").value || 0);
  const target = Number(el("targetAmount").value || 0);
  const maxRaces = Number(el("maxPlanRaces").value || 2);
  const liveOdds = el("liveOddsToggle").checked ? "1" : "0";
  if (!start || !target) {
    el("planResult").innerHTML = `<div class="empty">元手と目標額を入力してください。</div>`;
    return;
  }
  setStatus("資金プランを作成中");
  el("planResult").innerHTML = `<div class="empty">ライブオッズと候補を確認中です。</div>`;
  try {
    const params = new URLSearchParams({
      start: String(start),
      target: String(target),
      max_races: String(maxRaces),
      live_odds: liveOdds,
    });
    const res = await apiGet(`/api/capital_plan?${params.toString()}`);
    const payload = await res.json();
    if (!payload.ok) {
      el("planResult").innerHTML = `<div class="empty">${escapeHtml(payload.error || "資金プランを作れませんでした。")}</div>`;
      setStatus("資金プラン作成エラー");
      return;
    }
    state.capitalPlan = payload;
    renderCapitalPlan(payload);
    setStatus(`${payload.plans?.length || 0}件の資金プランを表示中`);
  } catch (error) {
    el("planResult").innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    setStatus("資金プラン作成エラー");
  }
}

function updateCapitalPlanTimer() {
  if (state.capitalPlanTimer) {
    clearInterval(state.capitalPlanTimer);
    state.capitalPlanTimer = null;
  }
  if (!el("autoRollToggle").checked) {
    setStatus("運用モードを停止しました");
    return;
  }
  loadCapitalPlan();
  state.capitalPlanTimer = setInterval(loadCapitalPlan, 60000);
  setStatus("運用モードで自動更新中");
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
  renderVenueBoard(payload.forecasts || []);

  const rows = filterForecasts(payload.forecasts || []);
  el("forecastList").innerHTML = rows.length
    ? renderVenueForecastSections(rows)
    : `<div class="empty">条件に合うレースがありません。</div>`;
}

function renderVenueForecastSections(rows) {
  return groupForecastsByVenue(rows)
    .map((group) => {
      const strong = group.races.filter((race) => race.confidence?.label === "強").length;
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
            ${badge(`強 ${strong}`, "confidence-badge")}
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
      races: races.sort((a, b) => String(a.start_time || "99:99").localeCompare(String(b.start_time || "99:99"))),
    }))
    .sort((a, b) => String(a.races[0]?.start_time || "99:99").localeCompare(String(b.races[0]?.start_time || "99:99")));
}

function renderCapitalPlan(payload) {
  const odds = payload.odds || {};
  const planSummary = payload.summary || {};
  const plans = payload.plans || [];
  const summary = `<div class="plan-meta">
    <span>${escapeHtml(payload.input?.start_amount || 0)}円 → ${escapeHtml(payload.input?.target_amount || 0)}円</span>
    <span>ライブ ${odds.fetched || 0}/${odds.attempted || 0}</span>
    <span>対象 ${escapeHtml(planSummary.active_forecast_count ?? 0)}R / 終了除外 ${escapeHtml(planSummary.elapsed_forecast_count ?? 0)}R</span>
    ${planSummary.next_race_time ? `<span>次 ${escapeHtml(planSummary.next_race_time)}</span>` : ""}
    <span>${el("autoRollToggle").checked ? "運用モードON" : "手動更新"}</span>
    <span>${escapeHtml((planSummary.data_used || []).slice(0, 4).join(" / "))}</span>
  </div>`;
  if (!plans.length) {
    el("planResult").innerHTML = `${summary}<div class="empty">条件に近い候補がありません。目標額か最大レース数を調整してください。</div>`;
    return;
  }
  el("planResult").innerHTML = `${summary}<div class="plan-grid">${plans.map(renderPlanCard).join("")}</div>`;
}

function renderPlanCard(plan) {
  const reached = plan.shortfall === 0 ? "is-reached" : "is-short";
  return `<article class="plan-card ${reached}">
    <header class="plan-card-head">
      <div>
        <span class="status-dot">${escapeHtml(plan.risk)}リスク</span>
        <h4>${escapeHtml(plan.title)}</h4>
      </div>
      <div class="plan-return">
        <strong>${yen(plan.projected_return)}</strong>
        <span>${escapeHtml(plan.multiplier)}倍 / 的中目安 ${percent(plan.hit_probability)}</span>
      </div>
    </header>
    <div class="plan-legs">
      ${(plan.races || []).map(renderPlanLeg).join("")}
    </div>
    ${(plan.warnings || []).length ? `<div class="plan-warnings">${plan.warnings.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>` : ""}
  </article>`;
}

function renderPlanLeg(leg) {
  const oddsClass = leg.odds_source === "live" ? "odds-live" : "odds-estimated";
  const ev = expectedReturn(leg);
  const edge = ev - Number(leg.stake || 0);
  const evClass = edge >= 0 ? "ev-positive" : "ev-caution";
  return `<div class="plan-leg">
    <div class="plan-race">
      <span class="time-chip">${escapeHtml(leg.start_time || "--:--")}</span>
      <strong>${escapeHtml(leg.venue || "")} ${escapeHtml(leg.race_no || "")}R</strong>
      <span>${escapeHtml(leg.confidence || "")}</span>
    </div>
    <div class="plan-ticket">
      <b>${escapeHtml(leg.ticket || "")}</b>
      <span class="${oddsClass}">${escapeHtml(leg.odds_str || leg.odds)}倍 ${leg.odds_source === "live" ? "LIVE" : "推定"}</span>
      ${leg.popularity ? `<span>${escapeHtml(leg.popularity)}人気</span>` : ""}
    </div>
    <div class="plan-money">
      <span>賭け ${yen(leg.stake)}</span>
      <strong>見込 ${yen(leg.projected_return)}</strong>
      <em class="ev-chip ${evClass}">期待値目安 ${yen(ev)}</em>
    </div>
    <div class="plan-reasons">
      ${(leg.rationale || []).map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
      ${(leg.ex_signals || []).map((item) => `<span class="ex-signal">${escapeHtml(item)}</span>`).join("")}
    </div>
  </div>`;
}

function expectedReturn(leg) {
  const stake = Number(leg.stake || 0);
  const odds = Number(leg.odds || 0);
  const hitProbability = Number(leg.hit_probability || 0);
  return Math.round(stake * odds * hitProbability);
}

function renderVenueBoard(forecasts) {
  const venues = new Map();
  for (const race of forecasts) {
    const venue = race.venue || "未設定";
    const current = venues.get(venue) || {
      venue,
      count: 0,
      firstTime: race.start_time || "--:--",
      raceNo: race.race_no || "-",
      strong: 0,
    };
    current.count += 1;
    if ((race.start_time || "99:99") < (current.firstTime || "99:99")) {
      current.firstTime = race.start_time || "--:--";
      current.raceNo = race.race_no || "-";
    }
    if (race.confidence?.label === "強") current.strong += 1;
    venues.set(venue, current);
  }
  el("venueBoard").innerHTML = [...venues.values()]
    .sort((a, b) => String(a.firstTime).localeCompare(String(b.firstTime)))
    .map(
      (item) => `<article class="venue-chip">
        <span class="status-dot">受付中</span>
        <strong>${escapeHtml(item.venue)}</strong>
        <small>${escapeHtml(item.raceNo)}R 発走 ${escapeHtml(item.firstTime)} / ${item.count}レース</small>
        <em>強 ${item.strong}</em>
      </article>`
    )
    .join("");
}

function filterForecasts(rows) {
  const text = el("searchInput").value.trim().toLowerCase();
  const confidence = el("confidenceFilter").value;
  return rows.filter((race) => {
    if (confidence !== "all" && race.confidence?.label !== confidence) return false;
    if (!text) return true;
    const haystack = [
      race.venue,
      race.event,
      race.race_no,
      ...(race.top3 || []).map((row) => row.name),
      ...(race.entries || []).map((row) => row.comment),
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(text);
  });
}

function renderForecastCard(race) {
  const top = race.top3?.[0] || {};
  const second = race.top3?.[1] || {};
  const third = race.top3?.[2] || {};
  const confidence = race.confidence || {};
  const tickets = (race.tickets || []).map(ticketChip).join("");
  const primaryTickets = (race.tickets || [])
    .slice(0, 3)
    .map((ticket) => ticket.label)
    .join(" / ");
  const lines = (race.lines || []).map(lineItem).join("");
  const signals = (race.comment_signals || []).map((signal) => `<li>${escapeHtml(signal)}</li>`).join("");

  return `<details class="race-ribbon ${confidenceClass(confidence.label)}">
    <summary class="ribbon-summary">
      <span class="ribbon-time">${escapeHtml(race.start_time || "--:--")}</span>
      <strong>${escapeHtml(race.race_no || "")}R</strong>
      <span class="ribbon-main">${car(top.car_no)} ${escapeHtml(top.name || "-")} <em>${percent(top.probability)}</em></span>
      <span class="ribbon-tickets">${escapeHtml(primaryTickets || "-")}</span>
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
            <span>${escapeHtml(confidence.reason || "")}</span>
          </div>
        </header>

        <div class="prediction-summary">
          <div class="main-pick">
            <span>本命</span>
            <strong>${car(top.car_no)} ${escapeHtml(top.name || "-")}</strong>
            <em>${percent(top.probability)}</em>
          </div>
          <div class="top3-row">
            ${rankPill(1, top)}
            ${rankPill(2, second)}
            ${rankPill(3, third)}
          </div>
          <div class="ticket-row">${tickets}</div>
        </div>

        ${renderLineDiagram(race.lines || [], race.top3 || [])}

        <div class="forecast-grid">
          <section class="analysis-block">
            <h4>展開</h4>
            <p class="headline">${escapeHtml(race.scenario?.headline || "")}</p>
            <p>${escapeHtml(race.scenario?.flow || "")}</p>
            <p>${escapeHtml(race.scenario?.watch || "")}</p>
            <p class="risk-text">${escapeHtml(race.scenario?.upset || "")}</p>
          </section>
          <section class="analysis-block">
            <h4>ライン/関係性</h4>
            <ul class="line-list">${lines}</ul>
          </section>
        </div>

        <details class="details">
          <summary>コメント根拠と全選手</summary>
          <div class="detail-grid">
            <section>
              <h4>コメント・心理シグナル</h4>
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

function renderMiniEntries(entries) {
  return `<table class="data-table compact-table">
    <thead>
      <tr>
        <th>車</th>
        <th>選手</th>
        <th>脚質</th>
        <th>得点</th>
        <th>コメント</th>
      </tr>
    </thead>
    <tbody>
      ${entries
        .map(
          (row) => `<tr>
            <td>${car(row.car_no)}</td>
            <td><strong>${escapeHtml(row.name)}</strong><br><span class="muted">${escapeHtml(row.prefecture || "")} ${escapeHtml(row.class || "")}</span></td>
            <td>${escapeHtml(row.style || "")}</td>
            <td>${num(row.racing_score)}</td>
            <td>${escapeHtml(row.comment || "")}</td>
          </tr>`
        )
        .join("")}
    </tbody>
  </table>`;
}

function renderRace() {
  if (!state.bundle) return;
  const race = state.bundle.race;
  const prediction = state.bundle.prediction;

  el("sourceLabel").textContent = race.source?.name || "source";
  el("raceTitle").textContent = race.title || `${race.venue || ""} ${race.race_no || ""}R`;
  el("raceMeta").innerHTML = [race.date, race.venue, race.event, race.race_class, race.weather]
    .filter(Boolean)
    .map((item) => `<span>${escapeHtml(item)}</span>`)
    .join("");

  const top = prediction.rankings?.[0];
  el("metricStrip").innerHTML = [
    ["出走", `${race.entrants?.length || 0}`, "選手"],
    ["ライン", `${race.lineup?.length || 0}`, "並び"],
    ["本命", top ? `${top.car_no} ${top.name}` : "-", "最上位"],
  ]
    .map(metric)
    .join("");

  el("modelLabel").textContent = prediction.model?.warning || "";
  renderRankings(prediction.rankings || []);
  renderEntries(race.entrants || []);
  renderTickets(prediction.tickets || []);
  renderNotes(prediction.race_notes || []);
  state.learnedModel = prediction.model || state.learnedModel;
  renderLearn();
  el("lineupLabel").textContent = formatLineup(race.lineup || []);
}

function renderRankings(rows) {
  el("rankingBody").innerHTML = rows
    .map((row, index) => {
      const toneClass = isRiskTone(row.emotion?.tone) ? "tone risk" : "tone";
      return `<tr>
        <td><span class="mark">${index + 1}</span></td>
        <td>${car(row.car_no)}</td>
        <td><strong>${escapeHtml(row.name)}</strong><br><span class="muted">${escapeHtml(row.prefecture || "")} ${escapeHtml(row.class || "")}</span></td>
        <td class="prob">${percent(row.win_probability)}</td>
        <td><span class="${toneClass}">${escapeHtml(row.emotion?.tone || "中立")}</span><br>${escapeHtml(row.comment || "")}</td>
        <td><div class="reason-list">${(row.reasons || []).map((reason) => `<span>${escapeHtml(reason)}</span>`).join("")}</div></td>
      </tr>`;
    })
    .join("");
}

function renderEntries(rows) {
  el("entriesBody").innerHTML = rows
    .map((row) => {
      const stats = row.stats || {};
      return `<tr>
        <td>${car(row.car_no)}</td>
        <td><strong>${escapeHtml(row.name)}</strong><br><span class="muted">${escapeHtml(row.prefecture || "")} ${row.age || ""}歳 ${row.term || ""}期</span></td>
        <td>${escapeHtml(row.class || "")}</td>
        <td>${escapeHtml(row.style || "")}</td>
        <td>${num(row.racing_score)}</td>
        <td>${stats.start_count ?? 0}/${stats.home_count ?? 0}/${stats.back_count ?? 0}</td>
        <td>${num(stats.win_rate)}%</td>
        <td>${num(stats.two_rate)}%</td>
        <td>${num(stats.three_rate)}%</td>
        <td>${escapeHtml(row.comment || "")}</td>
      </tr>`;
    })
    .join("");
}

function renderTickets(tickets) {
  el("tickets").innerHTML = tickets
    .map(
      (ticket) => `<div class="ticket">
        <strong>${escapeHtml(ticket.label)}</strong>
        <span>${ticket.score != null ? score(ticket.score) : ""}</span>
      </div>`
    )
    .join("");
}

function renderNotes(notes) {
  el("raceNotes").innerHTML = notes.map((note) => `<li>${escapeHtml(note)}</li>`).join("");
}

function renderLearn() {
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

function rankPill(rank, row) {
  if (!row || row.car_no == null) return "";
  return `<span class="rank-pill"><b>${rank}</b>${car(row.car_no)}<span>${escapeHtml(row.name)}</span><em>${percent(row.probability)}</em></span>`;
}

function ticketChip(ticket) {
  return `<span class="ticket-chip">${escapeHtml(ticket.label)}${ticket.score != null ? `<em>${score(ticket.score)}</em>` : ""}</span>`;
}

function renderLineDiagram(lines, top3) {
  if (!lines.length) return "";
  const topCars = new Set(top3.map((row) => Number(row.car_no)));
  return `<div class="line-diagram" aria-label="ライン図">
    <div class="diagram-title">
      <span>ライン図</span>
      <strong>隊列と仕掛けどころ</strong>
    </div>
    <div class="line-tracks">
      ${lines.map((line, index) => renderLineTrack(line, index, topCars)).join("")}
    </div>
  </div>`;
}

function renderLineTrack(line, index, topCars) {
  const members = line.members || [];
  const color = (index % 5) + 1;
  const memberNodes = members
    .map((member, memberIndex) => {
      const highlight = topCars.has(Number(member.car_no)) ? " is-highlight" : "";
      const arrow = memberIndex < members.length - 1 ? `<span class="line-arrow">→</span>` : "";
      return `<span class="line-node${highlight}">
        ${car(member.car_no)}
        <span class="node-copy">
          <b>${escapeHtml(member.name || "")}</b>
          <small>${escapeHtml(member.style || "")}</small>
        </span>
      </span>${arrow}`;
    })
    .join("");
  const role = members.length <= 1 ? "単騎" : `${members.length}車ライン`;
  return `<div class="line-track line-color-${color}">
    <div class="line-track-label">
      <b>${escapeHtml(line.label)}</b>
      <span>${role}</span>
    </div>
    <div class="line-nodes">${memberNodes}</div>
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
  if (label === "強") return "is-strong";
  if (label === "中") return "is-medium";
  return "is-mixed";
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
