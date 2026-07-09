const state = {
  today: null,
  bundle: null,
  activeTab: "today",
  learning: null,
  learnedModel: null,
  bankroll: null,
  results: null,
};

const STATIC_API = {
  "/api/today": "static-api/today.json",
  "/api/sample": "static-api/sample.json",
  "/api/learn/status": "static-api/learn-status.json",
  "/api/bankroll": "static-api/bankroll.json",
  "/api/results": "static-api/results.json",
};

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
        error: "公開版では保存や手動学習は使えません。ローカル版で実行してください。",
      }),
    };
  }
}

document.addEventListener("DOMContentLoaded", () => {
  on("refreshTodayBtn", "click", loadToday);
  on("confidenceFilter", "change", renderToday);
  loadLearnStatus();
  loadToday();
  loadBankroll();
  loadResults();
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
      renderLearn();
    }
  } catch {
    state.learning = null;
  }
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

function renderResults(payload) {
  const summary = payload.summary || {};
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

  el("resultsSummary").innerHTML = [
    ["本命的中", summary.honmei_rate != null ? percent(summary.honmei_rate) : "-", `${summary.honmei_hits ?? 0}/${summary.settled ?? 0}`, "green"],
    ["車券圏内", summary.in_top3_rate != null ? percent(summary.in_top3_rate) : "-", "本命が3着内", "blue"],
    ["3連単的中", summary.trifecta_hits ?? 0, summary.trifecta_rate != null ? percent(summary.trifecta_rate) : "-", "amber"],
    ["結果待ち", summary.pending ?? 0, `全${summary.races ?? 0}レース`, "slate"],
  ]
    .map(metric)
    .join("");

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
    <span class="result-pick">${car(pick.car_no)} ${escapeHtml(pick.name || "")} <em>${percent(pick.probability)}</em></span>
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

async function loadBankroll() {
  try {
    const res = await apiGet("/api/bankroll");
    const payload = await res.json();
    state.bankroll = payload;
    renderBankroll(payload);
  } catch (error) {
    el("bankrollBody").innerHTML = `<div class="empty">運用状態を読み込めません: ${escapeHtml(error.message)}</div>`;
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
    return payload;
  } catch {
    setStatus("運用セッションはローカル版でのみ操作できます");
    return null;
  }
}

function startBankroll() {
  bankrollPost("/api/bankroll/start", {
    start_amount: Number(el("brStart").value || 0),
    target_amount: Number(el("brTarget").value || 0),
    per_race_cap_pct: Number(el("brCap").value || 20),
    daily_loss_limit_pct: Number(el("brLossLimit").value || 30),
    max_consecutive_losses: Number(el("brMaxLosses").value || 3),
    min_ev: Number(el("brMinEv").value || 1.2),
  }).then((payload) => {
    if (payload) setStatus("運用セッションを開始しました");
  });
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
  const head = el("bankrollHeadActions");
  const body = el("bankrollBody");
  const session = payload.session;
  const brState = payload.state;

  if (!session) {
    head.innerHTML = "";
    body.innerHTML = renderBankrollSetup(payload);
    bindBankrollSetup();
    return;
  }

  if (session.status !== "active") {
    head.innerHTML = "";
    body.innerHTML = `${renderBankrollStopBanner(session, brState)}
      ${renderBankrollStatus(session, brState)}
      ${renderBankrollHistory(brState)}
      <div class="bankroll-restart">${renderBankrollSetup(payload)}</div>`;
    bindBankrollSetup();
    return;
  }

  head.innerHTML = `<button id="brStopBtn" class="button">停止</button>`;
  on("brStopBtn", "click", stopBankroll);

  const parts = [renderBankrollStatus(session, brState)];
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
}

function renderBankrollSetup(payload) {
  const config = payload.last_session?.config || payload.session?.config || {};
  const lastNote = payload.last_session
    ? `<p class="bankroll-last">前回: ${escapeHtml(payload.last_session.stop_reason || "停止")} / 最終残高 ${yen(payload.last_session.state?.balance ?? 0)}</p>`
    : "";
  return `<div class="bankroll-setup">
    ${lastNote}
    <div class="bankroll-form">
      <label class="field compact"><span>元手</span><input id="brStart" type="number" min="300" step="100" value="${Number(config.start_amount || 1000)}" /></label>
      <label class="field compact"><span>目標額</span><input id="brTarget" type="number" min="400" step="100" value="${Number(config.target_amount || 3000)}" /></label>
      <label class="field compact"><span>1R上限%</span><input id="brCap" type="number" min="5" max="50" step="5" value="${Number(config.per_race_cap_pct || 20)}" /></label>
      <label class="field compact"><span>損失上限%</span><input id="brLossLimit" type="number" min="10" max="80" step="5" value="${Number(config.daily_loss_limit_pct || 30)}" /></label>
      <label class="field compact"><span>連敗停止</span><input id="brMaxLosses" type="number" min="1" max="10" value="${Number(config.max_consecutive_losses || 3)}" /></label>
      <label class="field compact"><span>EV下限</span><input id="brMinEv" type="number" min="0.5" max="2" step="0.05" value="${Number(config.min_ev || 1.2)}" /></label>
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
}

function renderBankrollStopBanner(session, brState) {
  const profit = brState?.profit ?? 0;
  const tone = profit >= 0 ? "is-reached" : "is-short";
  return `<div class="bankroll-stop-banner ${tone}">
    <strong>停止: ${escapeHtml(session.stop_reason || "停止")}</strong>
    <span>最終残高 ${yen(brState?.balance ?? 0)}(${profit >= 0 ? "+" : ""}${yen(profit)})</span>
  </div>`;
}

function renderBankrollStatus(session, brState) {
  const config = session.config || {};
  const profit = brState.profit ?? 0;
  const profitClass = profit >= 0 ? "ev-positive" : "ev-caution";
  return `<div class="bankroll-status">
    <div class="metric metric-teal"><span>残高</span><strong>${yen(brState.balance)}</strong><em>目標 ${yen(config.target_amount)}</em></div>
    <div class="metric metric-${profit >= 0 ? "green" : "amber"}"><span>損益</span><strong class="${profitClass}">${profit >= 0 ? "+" : ""}${yen(profit)}</strong><em>元手 ${yen(config.start_amount)}</em></div>
    <div class="metric metric-blue"><span>目標進捗</span><strong>${Math.round((brState.target_progress || 0) * 100)}%</strong><em>${brState.wins}勝${brState.losses}敗</em></div>
    <div class="metric metric-purple"><span>連敗</span><strong>${brState.consecutive_losses}</strong><em>${config.max_consecutive_losses}で停止</em></div>
    <div class="metric metric-slate"><span>損失余地</span><strong>${yen(Math.max(0, brState.day_loss_limit - brState.day_loss))}</strong><em>上限 ${yen(brState.day_loss_limit)}</em></div>
  </div>`;
}

function renderBankrollProposal(proposal) {
  const top = proposal.top_pick || {};
  return `<article class="bankroll-proposal">
    <header class="plan-card-head">
      <div>
        <span class="status-dot">次の提案</span>
        <h4>${escapeHtml(proposal.venue)} ${escapeHtml(proposal.race_no)}R <span class="time-chip">${escapeHtml(proposal.start_time)}</span></h4>
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
  return `<div class="bankroll-ticket ${roleClass}">
    <span class="ticket-role">${escapeHtml(ticket.role)}</span>
    <b>${escapeHtml(ticket.label)}</b>
    <span>${yen(ticket.stake)}</span>
    <em>${escapeHtml(ticket.odds)}倍 / ${percent(ticket.hit_probability)}</em>
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
    if (race.confidence?.label === "強" || race.confidence?.label === "強") current.strong += 1;
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
  const confidenceFilter = el("confidenceFilter");
  const confidence = confidenceFilter ? confidenceFilter.value : "all";
  return rows.filter((race) => {
    if (confidence !== "all" && race.confidence?.label !== confidence) return false;
    return true;
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
  if (label === "強" || label === "強") return "is-strong";
  if (label === "中" || label === "中") return "is-medium";
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
