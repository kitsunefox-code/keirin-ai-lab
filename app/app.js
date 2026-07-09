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
  document.addEventListener("click", (event) => {
    const btn = event.target.closest(".motion-btn");
    if (btn) toggleRaceMotion(btn.dataset.race);
  });
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
      if (payload.model) state.learnedModel = payload.model;
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

function renderRecordBar(record) {
  const bar = el("aiRecordBar");
  if (!bar || !record) return;
  const cell = (item) => {
    if (!item || !item.settled) {
      return `<div class="record-cell"><span>${escapeHtml(item?.label || "")}</span><strong>-</strong><em>確定待ち</em></div>`;
    }
    return `<div class="record-cell">
      <span>${escapeHtml(item.label)}</span>
      <strong>本命 ${percent(item.honmei_rate)}</strong>
      <em>3連単 ${item.trifecta_hits}本(${percent(item.trifecta_rate)}) / 車券圏 ${percent(item.in_top3_rate)} / ${item.settled}R</em>
    </div>`;
  };
  bar.innerHTML = `<div class="record-title">AI成績</div>${cell(record.today)}${cell(record.week)}${cell(record.year)}`;
}

function renderResults(payload) {
  const summary = payload.summary || {};
  renderRecordBar(payload.record);
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
const MOTION = { duration: 10000, laps: 2 };
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
  // 全車 = 出走表 ∪ ライン ∪ top3 (どれかに欠けがあっても全員走らせる)
  const cars = [
    ...new Set([
      ...entries.map((entry) => Number(entry.car_no)),
      ...lineup.flat(),
      ...(race.top3 || []).map((row) => Number(row.car_no)),
    ].filter((car) => Number.isFinite(car) && car > 0)),
  ];

  const initial = normalizeOrder(lineup.flat(), cars);
  const top3 = (race.top3 || []).map((row) => Number(row.car_no)).filter(Boolean);
  const rest = cars.filter((car) => !top3.includes(car)).sort((a, b) => (probs.get(b) || 0) - (probs.get(a) || 0));
  const final = normalizeOrder([...top3, ...rest], cars);

  const escapeLine =
    lineup.find((line) => styles.get(line[0]) === "逃") ||
    lineup.find((line) => line.length >= 2) ||
    (lineup.length ? [lineup[0]].flat() : [initial[0]].filter(Boolean));

  return { cars, lineup, initial, final, escapeLine, probs, styles, names };
}

function buildMotionPlan(race) {
  const base = computeMotionBase(race);
  const { cars, lineup, initial, final, escapeLine } = base;
  const afterBell = normalizeOrder([...escapeLine, ...initial], cars);

  const topCar = final[0];
  const attackLine = lineup.find((line) => line.includes(topCar) && line !== escapeLine);
  let backStretch;
  if (attackLine) {
    const others = afterBell.filter((car) => !attackLine.includes(car));
    backStretch = normalizeOrder([...others.slice(0, 1), ...attackLine, ...others.slice(1)], cars);
  } else {
    backStretch = afterBell;
  }

  // キーフレームとキャプションは「先頭の走破周回数」基準。
  // 打鐘=残り1周半(2周走のうち0.5周消化)、最終バック=残り半周(1.5周消化)。
  return {
    cars,
    keyframes: [
      { lap: 0.0, order: initial },
      { lap: 0.5, order: afterBell },
      { lap: 1.5, order: backStretch },
      { lap: 1.9, order: final },
      { lap: 2.0, order: final },
    ],
    captions: [
      { lap: 0.0, text: "青板、先頭員の後ろで隊列を組んで周回" },
      { lap: 0.5, text: "🔔 打鐘!残り1周半、先行ラインが踏み込む" },
      { lap: 1.0, text: "残り1周、ホームストレッチライン通過" },
      { lap: 1.5, text: "最終バック、まくり勢が外から仕掛ける" },
      { lap: 1.87, text: "直線勝負!内圏線と外帯線の間で追い比べ" },
      { lap: 1.99, text: `ゴール!1着予想 ${topCar || "-"} ${escapeHtml((race.top3?.[0]?.name) || "")}` },
    ],
  };
}

/* ---- モーションメーカー: 自分で展開を組む ---- */

function buildCustomMotionPlan(race, opts) {
  const base = computeMotionBase(race);
  const { cars, lineup, initial, names, probs } = base;

  // 主導権ライン(ユーザー選択。未選択ならAIの先行ライン)
  const leadLine = (opts.leadLine && opts.leadLine.length ? opts.leadLine : base.escapeLine).filter((car) => cars.includes(car));

  const bellLap = opts.earlyAttack ? 0.32 : 0.5;
  let afterBell;
  const captions = [{ lap: 0.0, text: "青板、先頭員の後ろで隊列を組んで周回" }];

  if (opts.tsuppari && initial.length) {
    // つっぱり先行: 元の先頭ラインが主導権を譲らず、仕掛けたラインは2番手グループへ
    const frontLine = lineup.find((line) => line.includes(initial[0])) || [initial[0]];
    const others = initial.filter((car) => !frontLine.includes(car) && !leadLine.includes(car));
    afterBell = normalizeOrder([...frontLine, ...leadLine, ...others], cars);
    captions.push({ lap: bellLap, text: opts.earlyAttack ? "打鐘前から動くが…つっぱり先行!前は譲らない!" : "🔔 打鐘!つっぱり先行、前は譲らない!" });
  } else {
    afterBell = normalizeOrder([...leadLine, ...initial], cars);
    captions.push({
      lap: bellLap,
      text: opts.earlyAttack
        ? `打鐘前から${leadLine[0] || "-"}番ラインがカマシ気味に動く!`
        : `🔔 打鐘!${leadLine[0] || "-"}番ラインが主導権を取る`,
    });
  }
  if (opts.earlyAttack) {
    captions.push({ lap: 0.5, text: "🔔 打鐘!早めの主導権争いでピッチが上がる" });
  }
  captions.push({ lap: 1.0, text: "残り1周、ホームストレッチライン通過" });

  // ちぎられ: 指定車をライン後方から切り離して後退させる
  let backStretch = afterBell;
  if (opts.chigirareCar && cars.includes(opts.chigirareCar)) {
    const withoutCar = afterBell.filter((car) => car !== opts.chigirareCar);
    backStretch = normalizeOrder([...withoutCar, opts.chigirareCar], cars);
    captions.push({ lap: 1.4, text: `${opts.chigirareCar} ${escapeHtml(names.get(opts.chigirareCar) || "")}が番手からちぎれた!` });
  } else {
    captions.push({ lap: 1.5, text: "最終バック、勝負どころ!" });
  }

  // 結末: 自分で決める or AIが考える
  let finalOrder;
  let goalText;
  if (opts.finishMode === "manual" && opts.finishTop3?.length) {
    const picked = opts.finishTop3.filter((car) => cars.includes(car));
    const rest = backStretch.filter((car) => !picked.includes(car));
    if (opts.chigirareCar && !picked.includes(opts.chigirareCar)) {
      const idx = rest.indexOf(opts.chigirareCar);
      if (idx >= 0) {
        rest.splice(idx, 1);
        rest.push(opts.chigirareCar);
      }
    }
    finalOrder = normalizeOrder([...picked, ...rest], cars);
    goalText = `ゴール!あなたの結末 ${picked.map((car) => `${car}${escapeHtml(names.get(car) || "")}`).join(" → ")}`;
  } else {
    // AIが考える: 予測確率順。ただしちぎられ車は圏外へ
    let aiFinal = base.final;
    if (opts.chigirareCar) {
      aiFinal = normalizeOrder([...aiFinal.filter((car) => car !== opts.chigirareCar), opts.chigirareCar], cars);
    }
    finalOrder = aiFinal;
    goalText = `ゴール!AIの結末 1着 ${finalOrder[0] || "-"} ${escapeHtml(names.get(finalOrder[0]) || "")}`;
  }
  captions.push({ lap: 1.87, text: "直線勝負!" });
  captions.push({ lap: 1.99, text: goalText });

  return {
    cars,
    keyframes: [
      { lap: 0.0, order: initial },
      { lap: bellLap, order: afterBell },
      { lap: opts.chigirareCar ? 1.4 : 1.5, order: backStretch },
      { lap: 1.9, order: finalOrder },
      { lap: 2.0, order: finalOrder },
    ],
    captions,
  };
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
        <select id="mmRace">${races.map((r) => `<option value="${escapeAttr(r.race_key)}"${r.race_key === selKey ? " selected" : ""}>${escapeHtml(r.race_no)}R ${escapeHtml(r.start_time || "")}</option>`).join("")}</select>
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
    cars: (line.members || []).map((m) => Number(m.car_no)).filter(Boolean),
  })).filter((line) => line.cars.length);
  const carOption = (car) => `<option value="${car}">${car} ${escapeHtml(base.names.get(car) || "")}</option>`;
  const aiTop3 = base.final.slice(0, 3);
  return `
    <div class="mm-row">
      <label class="field compact"><span>主導権を取るライン</span>
        <select id="mmLeadLine">
          <option value="">AIに任せる</option>
          ${lines.map((line, i) => `<option value="${i}">${escapeHtml(line.label)}ライン</option>`).join("")}
        </select>
      </label>
      <label class="field compact"><span>ちぎられる選手</span>
        <select id="mmChigirare">
          <option value="">なし</option>
          ${base.cars.map(carOption).join("")}
        </select>
      </label>
    </div>
    <div class="mm-row mm-checks">
      <label class="check-field"><input type="checkbox" id="mmTsuppari" /><span>つっぱり先行(前が譲らない)</span></label>
      <label class="check-field"><input type="checkbox" id="mmEarly" /><span>打鐘前から動く(カマシ)</span></label>
    </div>
    <div class="mm-row mm-finish">
      <span class="mm-label">結末:</span>
      <label class="check-field"><input type="radio" name="mmFinish" id="mmFinishAi" checked /><span>AIが考える</span></label>
      <label class="check-field"><input type="radio" name="mmFinish" id="mmFinishManual" /><span>自分で決める</span></label>
      <span id="mmFinishPicks" hidden>
        <label class="field compact"><span>1着</span><select id="mm1st">${base.cars.map((car) => `<option value="${car}"${car === aiTop3[0] ? " selected" : ""}>${car} ${escapeHtml(base.names.get(car) || "")}</option>`).join("")}</select></label>
        <label class="field compact"><span>2着</span><select id="mm2nd">${base.cars.map((car) => `<option value="${car}"${car === aiTop3[1] ? " selected" : ""}>${car} ${escapeHtml(base.names.get(car) || "")}</option>`).join("")}</select></label>
        <label class="field compact"><span>3着</span><select id="mm3rd">${base.cars.map((car) => `<option value="${car}"${car === aiTop3[2] ? " selected" : ""}>${car} ${escapeHtml(base.names.get(car) || "")}</option>`).join("")}</select></label>
      </span>
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
      tsuppari: !!el("mmTsuppari")?.checked,
      earlyAttack: !!el("mmEarly")?.checked,
      chigirareCar: Number(el("mmChigirare")?.value) || null,
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
  svg.innerHTML = motionTrackSvg() + plan.cars.map((car) => motionRiderSvg(car)).join("");
  const riders = new Map(plan.cars.map((car) => [car, svg.querySelector(`[data-rider="${car}"]`)]));

  const started = performance.now();
  const gapStep = 0.014; // 車間(周回の割合)

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
    const gap = (posA + (posB - posA) * eased) * gapStep;
    const overtaking = posB < posA ? Math.sin(local * Math.PI) : 0;
    return { gap, outside: overtaking * 12 };
  };

  const tick = () => {
    const progress = Math.min(1, (performance.now() - started) / MOTION.duration);
    // 打鐘後にピッチが上がる: 序盤ゆっくり→終盤加速(eased は 0..1)
    const eased = progress < 0.4 ? progress * 0.7 : 0.28 + (progress - 0.4) * 1.2;
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
    // 打鐘: 残り1周半のバック線通過で鐘が揺れる+ジャン音
    const bell = svg.querySelector(".bank-bell");
    if (bell) {
      if (covered >= 0.5 && covered < 0.85) {
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

function renderBankrollStatus(session, brState) {
  const config = session.config || {};
  const profit = brState.profit ?? 0;
  const profitClass = profit >= 0 ? "ev-positive" : "ev-caution";
  const styleLabel = styleLabelOf(config.style);
  const estimate = brState.races_to_target || {};
  const estimateText = estimate.fast
    ? `${estimate.fast}〜${estimate.expected ?? "-"}R`
    : brState.balance >= config.target_amount ? "達成" : "-";
  return `<div class="bankroll-status">
    <div class="metric metric-teal"><span>残高</span><strong>${yen(brState.balance)}</strong><em>目標 ${yen(config.target_amount)}</em></div>
    <div class="metric metric-${profit >= 0 ? "green" : "amber"}"><span>損益</span><strong class="${profitClass}">${profit >= 0 ? "+" : ""}${yen(profit)}</strong><em>元手 ${yen(config.start_amount)}</em></div>
    <div class="metric metric-blue"><span>達成目安</span><strong>${escapeHtml(estimateText)}</strong><em>${styleLabel}運用 / ${brState.wins}勝${brState.losses}敗</em></div>
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
  renderRecommended(payload.recommended_races || []);
  renderMotionMaker();

  const rows = filterForecasts(payload.forecasts || []);
  el("forecastList").innerHTML = rows.length
    ? renderVenueForecastSections(rows)
    : `<div class="empty">条件に合うレースがありません。</div>`;
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
  section.innerHTML = `
    <div class="recommended-head">
      <span class="status-dot recommended-dot">AIオススメ</span>
      <h3>本日の狙い目</h3>
      <p>信頼度と本命確率、買い目スコアから絞ったレースです。</p>
    </div>
    <div class="venue-ribbon-list">${races.map(renderForecastCard).join("")}</div>
  `;
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
  if (bank.is_indoor) chips.push("屋内(ミッドナイト)");
  if (race.hour_label && race.hour_type !== "hourTypeNormal") chips.push(race.hour_label);
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

  return `<details class="race-ribbon ${confidenceClass(confidence.label)}">
    <summary class="ribbon-summary">
      <span class="ribbon-time">${escapeHtml(race.start_time || "--:--")}</span>
      <strong>${escapeHtml(race.race_no || "")}R</strong>
      <span class="ribbon-main">${car(top.car_no)} ${escapeHtml(top.name || "-")} <em>${percent(top.probability)}</em></span>
      <span class="ribbon-tickets">${escapeHtml(primaryTickets || "-")}</span>
      ${race.class_group ? badge(race.class_group, race.is_girls ? "girls-badge" : "class-badge") : ""}
      ${race.hour_label && race.hour_type !== "hourTypeNormal" ? badge(race.hour_label, "hour-badge") : ""}
      ${race.weather?.is_rain ? badge("雨", "rain-badge") : ""}
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
            ${race.scenario?.pattern ? badge(race.scenario.pattern, "pattern-badge") : ""}
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
        ${renderBankCard(race)}

        <div class="race-motion" data-motion-race="${escapeAttr(race.race_key || "")}">
          <button type="button" class="button motion-btn" data-race="${escapeAttr(race.race_key || "")}">▶ 展開を再生</button>
          <div class="motion-stage" hidden>
            <div class="motion-caption">周回中</div>
            <svg class="motion-svg" viewBox="0 0 640 300" role="img" aria-label="展開予想アニメーション"></svg>
          </div>
        </div>

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
        <th>AI根拠</th>
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
            <td>${(row.reasons || []).map((reason) => `<span class="reason-chip">${escapeHtml(reason)}</span>`).join("")}</td>
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
