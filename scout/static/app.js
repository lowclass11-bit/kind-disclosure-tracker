"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "");
const pct = (x) => (x == null ? "n/a" : (x >= 0 ? "+" : "") + (x * 100).toFixed(1) + "%");

const TABS = [
  ["rec", "추천"], ["kr", "한국"], ["us", "미국"], ["sector", "업종"], ["hot", "제외됨"],
];
const WORK = ["facts", "scenario", "bear", "memo", "script", "watch", "briefing"];
const WORK_LABEL = { watch: "추적표" };

const S = { state: null, data: null, tab: "rec", work: "facts", sel: null, file: null, wasRunning: false, docText: "" };

async function api(path, body) {
  const opt = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `요청 실패 (${r.status})`);
  return j;
}

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 4000);
}

// ---------------------------------------------------------------- 마크다운 (제목·목록·표·굵게·링크만)
function inline(s) {
  return esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}
function md(text) {
  const out = [];
  let list = null, table = false;
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } if (table) { out.push("</table>"); table = false; } };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    let m;
    if (!line.trim()) { close(); continue; }
    if ((m = line.match(/^(#{1,3}) (.*)/))) { close(); out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); continue; }
    if (line.startsWith("|")) {
      if (/^\|[-| :]+\|$/.test(line)) continue;
      const cells = line.slice(1, -1).split("|").map((c) => c.trim());
      if (!table) { close(); out.push("<table>"); table = true; out.push("<tr>" + cells.map((c) => `<th>${inline(c)}</th>`).join("") + "</tr>"); }
      else out.push("<tr>" + cells.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>");
      continue;
    }
    if ((m = line.match(/^[-*] (.*)/))) { if (list !== "ul") { close(); out.push("<ul>"); list = "ul"; } out.push(`<li>${inline(m[1])}</li>`); continue; }
    if ((m = line.match(/^\d+\. (.*)/))) { if (list !== "ol") { close(); out.push("<ol>"); list = "ol"; } out.push(`<li>${inline(m[1])}</li>`); continue; }
    if ((m = line.match(/^> ?(.*)/))) { close(); out.push(`<blockquote>${inline(m[1])}</blockquote>`); continue; }
    close();
    out.push(`<p>${inline(line)}</p>`);
  }
  close();
  return out.join("");
}

// ---------------------------------------------------------------- 왼쪽: 진행
function renderLeft() {
  const st = S.state, job = st.job;
  const status = $("status");
  status.className = "status" + (job.running ? " run" : job.error ? " err" : "");
  $("statusText").textContent = job.running ? (job.progress || job.steps[job.step] || "수집 중") : job.error ? "오류" : st.has_data ? "준비됨" : "대기";
  $("btnCollect").disabled = job.running;
  $("btnCollect").textContent = job.running ? "받는 중…" : st.has_data ? "① 자료 다시 받기" : "① 자료 받기";
  $("dataDate").textContent = st.data_date ? st.data_date.replace("T", " ").slice(0, 16) + " 기준" : "";

  const done = !job.running && st.has_data && !job.error;
  $("steps").innerHTML = job.steps.map((name, i) => {
    const cls = done || (job.running && i < job.step) ? "done" : job.running && i === job.step ? "now" : "";
    return `<li class="${cls}">${"①②③④⑤⑥"[i]} ${esc(name)}</li>`;
  }).join("");

  const src = Object.keys(job.sources).length ? job.sources : st.saved_sources;
  const names = Object.keys(src);
  $("sources").innerHTML = names.length ? names.map((n) => {
    const v = src[n];
    const cls = v.state === "받음" ? "ok" : v.state === "실패" ? "fail" : "need";
    const right = v.state === "받음" && v.count != null ? `받음 ${Number(v.count).toLocaleString()}건` : v.state;
    return `<li><span>${esc(n)}${v.note ? `<small>${esc(v.note)}</small>` : ""}</span><span class="${cls}">${esc(right)}</span></li>`;
  }).join("") : '<li class="muted">아직 받은 자료가 없습니다.</li>';

  $("log").innerHTML = job.log.map((l) => `<li><time>${esc(l.t)}</time>${esc(l.m)}</li>`).join("");

  const sel = $("style");
  if (!sel.options.length) {
    sel.innerHTML = Object.entries(st.styles).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  }
  sel.value = st.config.style;
  sel.disabled = job.running;
  $("useAi").disabled = !st.ai;
  if (!st.ai) $("useAi").checked = false;
}

function renderMacro() {
  const m = S.data && S.data.macro;
  if (!m || !m.rows.length) return;
  $("macro").innerHTML = m.rows.map((r) =>
    `<li><span><b>${esc(r.name)}</b><small>${esc(r.note)}</small></span><span>${esc(r.change)}${r.tag ? `<small class="tag">${esc(r.tag)}</small>` : ""}</span></li>`).join("");
}

// ---------------------------------------------------------------- 가운데: 후보
function listItems() {
  const d = S.data;
  if (!d || !d.stocks) return [];
  const q = $("search").value.trim().toLowerCase();
  let items;
  if (S.tab === "sector") items = d.sectors;
  else if (S.tab === "hot") items = d.stocks.filter((s) => s.hot);
  else if (S.tab === "kr") items = d.stocks.filter((s) => s.candidate && s.market === "KR");
  else if (S.tab === "us") items = d.stocks.filter((s) => s.candidate && s.market === "US");
  else {
    // 추천: 서로 다른 근거 축이 두 개 이상 겹친 종목, 한 업종이 목록을 독점하지 않게 업종당 3개까지
    const seen = {};
    items = d.stocks.filter((s) => s.candidate && s.confirm >= 2).filter((s) => (seen[s.sector_id] = (seen[s.sector_id] || 0) + 1) <= 3);
  }
  if (q) items = items.filter((s) => (s.name + " " + (s.ticker || "") + " " + (s.sector || "")).toLowerCase().includes(q));
  const key = $("sort").value;
  const val = (s) => (key === "score" ? s.score : key === "confirm" ? (s.confirm || 0) * 1000 + s.score : s.pillars[key]);
  return [...items].sort((a, b) => val(b) - val(a)).slice(0, 120);
}

const HINTS = {
  rec: "아직 주가가 조용한데 서로 다른 근거(스마트머니·실적 추정·촉매)가 둘 이상 겹친 종목입니다.",
  kr: "한국 후보 전체 — 소외도 40점 이상이면서 근거 축이 하나 이상 있는 종목.",
  us: "미국 후보 전체 — 소외도 40점 이상이면서 근거 축이 하나 이상 있는 종목.",
  sector: "업종 단위 점수 — 구성 종목의 소외도 중앙값과 상위 절반의 신호 평균.",
  hot: "이미 많이 올라 '잠잠하다'는 조건에서 벗어난 종목 (6개월 초과수익 25% 초과, 3개월 40% 초과, 또는 52주 고점 부근).",
};

function card(s) {
  const P = S.state.pillars;
  const bars = Object.keys(P).map((k) =>
    `<div class="bar ${k}">${esc(P[k])} ${Math.round(s.pillars[k])}<i><b style="width:${Math.max(0, Math.min(100, s.pillars[k]))}%"></b></i></div>`).join("");
  const top = s.ev.filter((e) => e.pts !== 0).slice(0, 3);
  const evs = (top.length ? top : s.ev.slice(0, 2)).map((e) =>
    `<li class="${e.p}${e.pts < 0 ? " neg" : ""}">${esc(e.t)}</li>`).join("");
  const isSector = s.kind === "sector";
  const sub = isSector
    ? `${s.market === "KR" ? "한국" : "미국"} 업종 · ${s.n}종목${s.etf ? " · ETF " + esc(s.etf) : ""}`
    : `${esc(s.ticker)} · ${esc(s.sector)} · 6개월 ${pct(s.ret_6m)}`;
  const badges = [];
  if (!isSector && s.confirm >= 2) badges.push(`<span class="badge">근거 ${s.confirm}축</span>`);
  if (s.hot) badges.push('<span class="badge warn">이미 오름</span>');
  if (!isSector && s.below_200 && (s.ret_1m || 0) < 0) badges.push('<span class="badge warn">하락 추세</span>');
  if (isSector && s.leaders.length) badges.push(`<span class="badge">${esc(s.leaders.slice(0, 2).map((l) => l.name).join(", "))}</span>`);
  const url = safeUrl(s.url);
  return `<div class="card${S.sel === s.id ? " sel" : ""}" data-id="${esc(s.id)}">
    <div class="card-top"><div><h4>${esc(s.name)}</h4><div class="sub">${sub}</div></div><div class="score" title="종합 점수">${Math.round(s.score)}</div></div>
    <div class="bars">${bars}</div>
    <ul class="evs">${evs}</ul>
    <div class="card-foot"><button type="button" data-act="pick">${S.sel === s.id ? "선택됨 ✓" : "선택"}</button>
      ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">시세 ↗</a>` : ""}${badges.join("")}</div>
  </div>`;
}

function renderCards() {
  $("tabs").innerHTML = TABS.map(([k, v]) => `<button role="tab" data-tab="${k}" aria-selected="${S.tab === k}">${v}</button>`).join("");
  $("listHint").textContent = HINTS[S.tab];
  if (!S.data || !S.data.stocks) {
    $("cards").innerHTML = '<div class="empty">왼쪽의 「① 자료 받기」를 누르면 후보가 여기에 나타납니다.</div>';
    return;
  }
  const items = listItems();
  $("cards").innerHTML = items.length ? items.map(card).join("") : '<div class="empty">조건에 맞는 후보가 없습니다.</div>';
}

function findItem(id) {
  const d = S.data;
  return d && (d.stocks.find((s) => s.id === id) || d.sectors.find((s) => s.id === id));
}

// ---------------------------------------------------------------- 오른쪽: 작업실
function renderWorkTabs() {
  const K = S.state.kinds;
  $("workTabs").innerHTML = WORK.map((k) =>
    `<button role="tab" data-work="${k}" aria-selected="${S.work === k}">${esc(WORK_LABEL[k] || K[k])}</button>`).join("");
}

async function runWork(useAi) {
  renderWorkTabs();
  if (S.work === "watch") return renderWatch();
  if (!S.data || !S.data.stocks) return;
  if (S.work !== "briefing" && !S.sel) {
    $("workMeta").textContent = "가운데에서 후보를 하나 선택하세요.";
    $("doc").innerHTML = "";
    $("docActions").hidden = true;
    return;
  }
  $("workMeta").textContent = useAi ? "Claude가 쓰는 중입니다. 1~2분 걸릴 수 있습니다…" : "만드는 중…";
  try {
    const r = await api("/api/work", { kind: S.work, id: S.sel, note: $("note").value, ai: useAi });
    S.file = r.file;
    S.docText = r.text;
    $("doc").innerHTML = (r.warn ? `<p class="warn">${esc(r.warn)}</p>` : "") + md(r.text);
    $("doc").scrollTop = 0;
    $("workMeta").textContent = `${r.mode} · ${r.file} 로 저장됨`;
    $("dlMd").href = `/api/download?file=${encodeURIComponent(r.file)}`;
    $("dlDocx").href = `/api/download?file=${encodeURIComponent(r.file)}&fmt=docx`;
    $("docActions").hidden = false;
    const it = findItem(S.sel);
    $("btnWatch").hidden = S.work === "briefing" || !it || it.kind !== "stock";
    loadFiles();
  } catch (e) {
    $("workMeta").textContent = "";
    toast(e.message);
  }
}

async function renderWatch(rows) {
  $("docActions").hidden = true;
  $("workMeta").textContent = "발굴한 날의 가격을 기록해 두고, 이후 수익률을 시장과 비교합니다. 6개월 뒤 내 발굴이 맞았는지 대조하세요.";
  try {
    rows = rows || (await api("/api/watchlist"));
  } catch (e) { return toast(e.message); }
  if (!rows.length) {
    $("doc").innerHTML = '<p class="muted">아직 추적 중인 종목이 없습니다. 종목을 선택해 문서를 만든 뒤 「추적표에 추가」를 누르세요.</p>';
    return;
  }
  const cls = (x) => (x == null ? "" : x >= 0 ? "up" : "down");
  $("doc").innerHTML = `<table class="wl"><tr><th>종목</th><th>발굴일</th><th>발굴가</th><th>현재가</th><th>수익률</th><th>시장 대비</th><th>재평가일</th><th></th></tr>` +
    rows.map((v) => `<tr><td><b>${esc(v.name)}</b><br><span class="muted">${esc(v.sector)} · ${v.days}일째</span></td>
      <td>${esc(v.added)}</td><td>${Number(v.price0).toLocaleString()}</td><td>${v.price ? Number(v.price).toLocaleString() : "n/a"}</td>
      <td class="${cls(v.ret)}">${pct(v.ret)}</td><td class="${cls(v.excess)}">${pct(v.excess)}</td><td>${esc(v.review)}</td>
      <td><button type="button" data-remove="${esc(v.id)}" aria-label="${esc(v.name)} 삭제">삭제</button></td></tr>`).join("") +
    `</table><p><a class="btn" href="/api/watchlist.csv">엑셀용 CSV 내려받기</a></p>`;
}

async function loadFiles() {
  try {
    const files = await api("/api/outputs");
    $("files").innerHTML = files.length
      ? files.map((f) => `<li><a href="/api/download?file=${encodeURIComponent(f)}">${esc(f)}</a></li>`).join("")
      : '<li class="muted">아직 없습니다.</li>';
  } catch { /* 목록은 부가 기능 */ }
}

// ---------------------------------------------------------------- 상태 갱신
async function loadData() {
  S.data = await api("/api/data");
  renderMacro();
  renderCards();
}

async function poll() {
  try {
    S.state = await api("/api/state");
  } catch {
    $("statusText").textContent = "서버 연결 끊김";
    return setTimeout(poll, 3000);
  }
  renderLeft();
  const running = S.state.job.running;
  if (S.wasRunning && !running) {
    if (S.state.job.error) toast("수집 중 오류: " + S.state.job.error);
    else { await loadData(); toast("자료를 다 받았습니다."); }
  }
  S.wasRunning = running;
  setTimeout(poll, running ? 1000 : 5000);
}

// ---------------------------------------------------------------- 이벤트
$("btnCollect").onclick = async () => {
  try { await api("/api/collect", {}); S.wasRunning = true; S.state.job.running = true; renderLeft(); }
  catch (e) { toast(e.message); }
};
$("style").onchange = async (e) => {
  await api("/api/config", { style: e.target.value });
  toast("투자 스타일을 바꿨습니다. 자료를 다시 받으면 반영됩니다.");
};
$("tabs").onclick = (e) => { const t = e.target.dataset.tab; if (t) { S.tab = t; renderCards(); } };
$("search").oninput = renderCards;
$("sort").onchange = renderCards;
$("cards").onclick = (e) => {
  if (e.target.dataset.act !== "pick") return;
  S.sel = e.target.closest(".card").dataset.id;
  const it = findItem(S.sel);
  $("pickName").textContent = it ? it.name : "없음";
  renderCards();
  if (S.work === "watch" || S.work === "briefing") S.work = "facts";
  runWork(false);
};
$("pick").onsubmit = (e) => { e.preventDefault(); runWork($("useAi").checked); };
$("workTabs").onclick = (e) => { const w = e.target.dataset.work; if (w) { S.work = w; runWork(false); } };
$("doc").onclick = async (e) => {
  const id = e.target.dataset.remove;
  if (id) renderWatch(await api("/api/watchlist", { remove: id }));
};
$("btnCopy").onclick = async () => {
  try { await navigator.clipboard.writeText(S.docText); toast("복사했습니다."); }
  catch { toast("복사하지 못했습니다. 내려받기를 이용하세요."); }
};
$("btnWatch").onclick = async () => {
  try { await api("/api/watchlist", { id: S.sel, note: $("note").value }); toast("추적표에 추가했습니다."); }
  catch (e) { toast(e.message); }
};
$("btnSettings").onclick = () => {
  const f = $("settingsForm");
  for (const [k, v] of Object.entries(S.state.config)) if (f.elements[k]) f.elements[k].value = v;
  $("settings").showModal();
};
$("settings").addEventListener("close", async () => {
  if ($("settings").returnValue !== "save") return;
  const f = $("settingsForm"), body = {};
  for (const el of f.elements) if (el.name) body[el.name] = el.value;
  try { await api("/api/config", body); toast("저장했습니다."); S.state = await api("/api/state"); renderLeft(); }
  catch (e) { toast(e.message); }
});

(async function init() {
  S.state = await api("/api/state");
  renderLeft();
  renderWorkTabs();
  await loadData();
  loadFiles();
  S.wasRunning = S.state.job.running;
  setTimeout(poll, 1000);
})();
