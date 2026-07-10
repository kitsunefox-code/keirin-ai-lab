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
  const page = document.body.dataset.page || "home";
  if (page === "home") {
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
  }
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
  const roiText = (roi) => (roi == null ? "—" : `${(roi * 100).toFixed(1)}%`);
  const roiClass = (roi) => (roi == null ? "" : roi >= 1 ? "kpi-up" : "kpi-down");
  const cell = (item) => {
    if (!item || !item.settled) {
      return `<div class="record-cell"><span>${escapeHtml(item?.label || "")}</span><strong>-</strong><em>確定待ち</em></div>`;
    }
    return `<div class="record-cell">
      <span>${escapeHtml(item.label)}</span>
      <strong>回収率 <b class="${roiClass(item.exacta_roi)}">${roiText(item.exacta_roi)}</b></strong>
      <em>本命 ${percent(item.honmei_rate)} / 2車単${item.exacta_priced ? `${item.exacta_priced}R集計` : "集計待ち"} / ${item.settled}R</em>
    </div>`;
  };
  bar.innerHTML = `<div class="record-title">AI成績</div>${cell(record.week)}${cell(record.last_week)}${cell(record.total)}`;
}

function renderResults(payload) {
  const summary = payload.summary || {};
  renderRecordBar(payload.record);
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
    { lap: 0.8, text: "青板、先頭員の後ろで落ち着いて周回" },
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
  const captions = [{ lap: 0.0, text: sName }, { lap: 1.0, text: "青板、隊列は落ち着いて周回" }];
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

  const parts = [yesterday, renderBankrollStatus(session, brState), renderStyleSwitcher(session)];
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

  document.querySelectorAll("[data-style-switch]").forEach((btn) => {
    btn.addEventListener("click", () => changeBankrollStyle(btn.dataset.styleSwitch));
  });
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
      box.innerHTML = `<div class="empty">まだ運用記録がありません。ホームの「バンクロール運用」でオリジナル(1万円×10R)を開始すると、ここに毎日の収支が貯まります。</div>`;
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

function renderStyleSwitcher(session) {
  const current = session.config?.style || "balance";
  const styles = (state.bankroll?.styles || []).filter((s) => s.key !== "original");
  const buttons = styles
    .map(
      (s) => `<button type="button" class="style-switch${s.key === current ? " active" : ""}" data-style-switch="${escapeAttr(s.key)}">${escapeHtml(s.label)}</button>`
    )
    .join("");
  const orig = current === "original" ? `<span class="style-switch-note">オリジナル運用中。乗り方を変えると通常運用へ切り替わります。</span>` : "";
  return `<div class="style-switcher">
    <span class="style-switch-label">乗り方(運用中でも変更可)</span>
    <div class="style-switch-row">${buttons}</div>
    ${orig}
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
  if (!el("todayMeta")) return; // 本日の予想パネルがないページ(モーションメーカー等)
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
  renderVenueBoard(payload.forecasts || []);
  renderRecommended(payload.recommended_races || []);

  const rows = filterForecasts(payload.forecasts || []);
  el("forecastList").innerHTML = rows.length
    ? renderVenueForecastSections(rows)
    : `<div class="empty">条件に合うレースがありません。</div>`;
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
    btn.addEventListener("click", () => {
      const target = document.getElementById(safeRaceId(btn.dataset.openRace));
      if (target) {
        target.open = true;
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  });
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
      // 会場内は開始時刻の早い順
      races: races.sort((a, b) => String(a.start_time || "99:99").localeCompare(String(b.start_time || "99:99"))),
    }))
    // 会場は最初のレースの開始時刻が早い順
    .sort((a, b) => String(a.races[0]?.start_time || "99:99").localeCompare(String(b.races[0]?.start_time || "99:99")));
}


function renderVenueBoard(forecasts) {
  if (!el("venueBoard")) return;
  const venues = new Map();
  for (const race of forecasts) {
    if (race.elapsed) continue;
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
    if (race.elapsed) return false; // 発走済みは一覧から外す(モーションメーカーでは選べる)
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

  return `<details class="race-ribbon ${confidenceClass(confidence.label)}" id="${safeRaceId(race.race_key)}">
    <summary class="ribbon-summary">
      <span class="ribbon-time">${escapeHtml(race.start_time || "--:--")}</span>
      <strong>${escapeHtml(race.race_no || "")}R</strong>
      <span class="ribbon-main">${car(top.car_no)} ${escapeHtml(top.name || "-")} <em class="prob-tag">AI勝率${percent(top.probability)}</em></span>
      <span class="ribbon-tickets">${escapeHtml(primaryTickets || "-")}</span>
      ${race.class_group ? badge(race.class_group, race.is_girls ? "girls-badge" : "class-badge") : ""}
      ${race.hour_label && race.hour_type !== "hourTypeNormal" ? badge(race.hour_label, "hour-badge") : ""}
      ${race.weather?.is_rain ? badge("雨", "rain-badge") : ""}
      ${payoutBadge(race)}
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
            <strong>${car(top.car_no)} ${escapeHtml(top.name || "-")}</strong>
            <em>${percent(top.probability)}<small class="unit-label">AI推定勝率</small></em>
          </div>
          <div class="top3-row">
            ${rankPill(1, top)}
            ${rankPill(2, second)}
            ${rankPill(3, third)}
          </div>
          <div class="bet-block">
            <div class="bet-label">3連単候補 <small>フォーメーション(＝順不同 / ー流し)</small></div>
            ${formationHtml(race.tickets || []) || `<div class="ticket-row">${tickets}</div>`}
            <details class="ticket-detail"><summary>全${(race.tickets || []).length}点を見る</summary><div class="ticket-row">${tickets}</div></details>
          </div>
          ${(race.exacta && race.exacta.length) ? `<div class="bet-block">
            <div class="bet-label">2車単候補 <small>軸1着固定・的中率重視</small></div>
            ${formationHtml(race.exacta) || ""}
            <details class="ticket-detail"><summary>全${race.exacta.length}点を見る</summary><div class="ticket-row exacta-row">${race.exacta.map((t) => `<span class="ticket-chip exacta-chip${t.suji ? " is-suji" : ""}">${escapeHtml(t.label)}${t.suji ? '<em class="suji-tag">スジ</em>' : ""}</span>`).join("")}</div></details>
          </div>` : ""}
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

function rankPill(rank, row) {
  if (!row || row.car_no == null) return "";
  return `<span class="rank-pill"><b>${rank}</b>${car(row.car_no)}<span>${escapeHtml(row.name)}</span><em>${percent(row.probability)}</em></span>`;
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
          return `<span class="line-flat-member${highlight}" title="${escapeAttr(member.name || "")} ${escapeAttr(member.style || "")}">
            ${car(member.car_no)}
            <small>${escapeHtml((member.name || "").slice(0, 4))}</small>
          </span>`;
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
  if (label === "強" || label === "強") return "is-strong";
  if (label === "中" || label === "中") return "is-medium";
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
