/* KIND 공시 트래커 - 정적 대시보드 (GitHub Pages) */
(() => {
  "use strict";

  const CATS = {
    earnings: { name: "실적 변화", short: "실적↑", color: "var(--earn)" },
    capex: { name: "시설투자·공급계약", short: "시설투자", color: "var(--capex)" },
    stake: { name: "대주주·내부자 지분 매입", short: "지분매입", color: "var(--stake)" },
  };
  const DIR_LABEL = { up: "▲ 상승", mixed: "◆ 혼조", down: "▼ 감소", buy: "▲ 매수", sell: "▼ 매도", flat: "– 변동없음" };
  const POLL_MS = 10 * 60 * 1000;

  // ---------------------------------------------------------------- storage (실패해도 동작)
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
  };

  const state = {
    index: null,
    items: [],
    tab: store.get("tab", "today"),
    market: store.get("market", "ALL"),
    signalOnly: store.get("signalOnly", true),
    showAmended: store.get("showAmended", true),
    day: null,
    watch: new Set(store.get("watch", [])),
    histLimit: 200,
  };

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------------------------------------------------------------- data
  async function fetchJSON(path) {
    const r = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
    if (!r.ok) throw new Error(`${path} ${r.status}`);
    return r.json();
  }

  async function load() {
    const [index, items] = await Promise.all([fetchJSON("data/index.json"), fetchJSON("data/all.json")]);
    state.index = index;
    state.items = items;
    if (!state.day || !index.days.some((d) => d.date === state.day)) state.day = index.latest;
  }

  function passes(it, { ignoreSignal = false } = {}) {
    if (state.market !== "ALL" && it.market !== state.market) return false;
    if (!state.showAmended && it.amended) return false;
    if (!ignoreSignal && state.signalOnly && !it.signal) return false;
    return true;
  }

  // ---------------------------------------------------------------- rendering helpers
  const weekday = (d) => "일월화수목금토"[new Date(d + "T12:00:00Z").getUTCDay()];
  const fmtDate = (d) => `${d.slice(5).replace("-", "/")} (${weekday(d)})`;

  function card(it, { showDate = false } = {}) {
    const watched = state.watch.has(it.corp);
    const dir = it.direction ? `<span class="dir ${it.direction}">${DIR_LABEL[it.direction] || ""}</span>` : "";
    return `<article class="card ${watched ? "watched" : ""} ${it.signal ? "" : "dim"}">
      <div class="l1">
        <button class="star ${watched ? "on" : ""}" data-star="${esc(it.corp)}" title="관심종목">${watched ? "★" : "☆"}</button>
        <span class="corp">${esc(it.corp)}</span>
        <span class="tag ${it.market}">${it.market === "KOSPI" ? "코스피" : "코스닥"}</span>
        <span class="tag cat-${it.category}">${esc(it.subtype)}</span>
        ${it.amended ? `<span class="tag amend">${esc(it.amend_tag || "정정")}</span>` : ""}
        ${it.subsidiary ? `<span class="tag">자회사</span>` : ""}
        <span class="time">${showDate ? fmtDate(it.date) + " " : ""}${esc(it.time)}</span>
      </div>
      <div class="title">${esc(it.title)}${it.filer && it.filer !== it.corp ? ` · 제출: ${esc(it.filer)}` : ""}</div>
      <div class="summary">${dir} ${esc(it.summary || "")}</div>
      ${detailLine(it)}
      <div class="links"><a href="${it.url}" target="_blank" rel="noopener">DART 원문</a><a href="${it.kind_url}" target="_blank" rel="noopener">KIND 원문</a></div>
    </article>`;
  }

  function detailLine(it) {
    const m = it.metrics || {};
    const bits = [];
    if (it.category === "earnings") {
      if (m.fs_type) bits.push(m.fs_type);
      if (m.reason) bits.push(m.reason);
    } else if (it.category === "capex") {
      if (m.name) bits.push(m.name);
      if (m.purpose) bits.push(m.purpose);
      if (m.start || m.end) bits.push(`${m.start || ""} ~ ${m.end || ""}`);
    } else if (it.category === "stake") {
      if (m.relation) bits.push(m.relation);
      if (m.reason_text) bits.push(m.reason_text);
      if (m.method) bits.push(m.method);
    }
    return bits.length ? `<div class="muted" style="font-size:12px;margin-top:2px">${esc(bits.join(" · ").slice(0, 220))}</div>` : "";
  }

  // ---------------------------------------------------------------- tabs
  function renderMeta() {
    const ix = state.index;
    if (!ix || !ix.latest) { $("#meta").textContent = "아직 수집된 데이터가 없습니다."; return; }
    const d = ix.days[0];
    $("#meta").textContent = `최근 수집 ${ix.updated_at.replace("T", " ").slice(0, 16)} KST · 최신 거래일 ${ix.latest} · 소스 ${d.source}`;
  }

  function renderToday() {
    const sel = $("#daySel");
    sel.innerHTML = state.index.days.map((d) => `<option value="${d.date}">${d.date} (${weekday(d.date)})</option>`).join("");
    sel.value = state.day;
    const dayMeta = state.index.days.find((d) => d.date === state.day);
    $("#daySource").textContent = dayMeta ? `전체 공시 ${dayMeta.total_disclosures.toLocaleString()}건 중 추적 대상 · 소스 ${dayMeta.source}` : "";

    const dayItems = state.items.filter((it) => it.date === state.day && passes(it, { ignoreSignal: true }));
    $("#kpis").innerHTML = Object.entries(CATS).map(([k, c]) => {
      const all = dayItems.filter((it) => it.category === k);
      const sig = all.filter((it) => it.signal);
      return `<div class="kpi" style="--c:${c.color}">
        <div class="label">${c.name}</div>
        <div class="val num">${sig.length}<span class="muted" style="font-size:14px;font-weight:500"> / ${all.length}</span></div>
        <div class="sub">${k === "stake" ? "매수 확인" : "상승·신규"} / 전체</div>
      </div>`;
    }).join("");

    $("#todayCols").innerHTML = Object.entries(CATS).map(([k, c]) => {
      const list = dayItems.filter((it) => it.category === k && passes(it));
      list.sort((a, b) => (state.watch.has(b.corp) - state.watch.has(a.corp)) || b.time.localeCompare(a.time));
      return `<div class="col" style="--c:${c.color}">
        <h2>${c.name} <span class="count">${list.length}</span></h2>
        ${list.length ? list.map((it) => card(it)).join("") : `<div class="empty">해당 공시 없음</div>`}
      </div>`;
    }).join("");
  }

  function renderOverlap() {
    const win = +$("#overlapWin").value;
    const dates = new Set(state.index.days.slice(0, win).map((d) => d.date));
    const byCorp = new Map();
    for (const it of state.items) {
      if (!dates.has(it.date) || !it.signal || !passes(it, { ignoreSignal: true })) continue;
      if (!byCorp.has(it.corp)) byCorp.set(it.corp, []);
      byCorp.get(it.corp).push(it);
    }
    const rows = [...byCorp.entries()]
      .map(([corp, list]) => ({ corp, list, cats: new Set(list.map((x) => x.category)) }))
      .filter((r) => r.cats.size >= 2)
      .sort((a, b) => b.cats.size - a.cats.size || b.list.length - a.list.length);

    $("#overlapList").innerHTML = rows.length ? rows.map((r) => {
      const watched = state.watch.has(r.corp);
      return `<div class="ov">
        <div class="ov-head">
          <button class="star ${watched ? "on" : ""}" data-star="${esc(r.corp)}">${watched ? "★" : "☆"}</button>
          <span class="corp">${esc(r.corp)}</span>
          <span class="tag ${r.list[0].market}">${r.list[0].market === "KOSPI" ? "코스피" : "코스닥"}</span>
          ${[...r.cats].map((c) => `<span class="tag cat-${c}">${CATS[c].short}</span>`).join("")}
          <span class="muted">${r.list.length}건</span>
        </div>
        <ul>${r.list.sort((a, b) => b.date.localeCompare(a.date)).map((it) =>
          `<li><span class="muted num">${it.date.slice(5)}</span> <span class="tag cat-${it.category}">${esc(it.subtype)}</span> ${esc(it.summary)} <a href="${it.url}" target="_blank" rel="noopener">원문</a></li>`).join("")}</ul>
      </div>`;
    }).join("") : `<div class="empty">최근 ${win} 거래일 동안 2개 이상 카테고리에 걸린 종목이 없습니다.</div>`;
  }

  function historyRows() {
    const q = $("#q").value.trim().toLowerCase();
    const cat = $("#hCat").value;
    const from = $("#hFrom").value, to = $("#hTo").value;
    return state.items.filter((it) => passes(it)
      && (!cat || it.category === cat)
      && (!from || it.date >= from) && (!to || it.date <= to)
      && (!q || `${it.corp} ${it.title} ${it.filer} ${it.summary}`.toLowerCase().includes(q)))
      .sort((a, b) => b.date.localeCompare(a.date) || b.time.localeCompare(a.time));
  }

  function renderHistory() {
    const rows = historyRows();
    $("#hCount").textContent = `${rows.length.toLocaleString()}건`;
    $("#hTable tbody").innerHTML = rows.slice(0, state.histLimit).map((it) => `<tr>
      <td class="num">${it.date}<br><span class="muted">${esc(it.time)}</span></td>
      <td><span class="tag ${it.market}">${it.market === "KOSPI" ? "코스피" : "코스닥"}</span></td>
      <td><button class="star ${state.watch.has(it.corp) ? "on" : ""}" data-star="${esc(it.corp)}">${state.watch.has(it.corp) ? "★" : "☆"}</button> <b>${esc(it.corp)}</b></td>
      <td><span class="tag cat-${it.category}">${esc(it.subtype)}</span>${it.amended ? ' <span class="tag amend">정정</span>' : ""}</td>
      <td><a href="${it.url}" target="_blank" rel="noopener">${esc(it.title)}</a></td>
      <td>${it.direction ? `<span class="dir ${it.direction}">${DIR_LABEL[it.direction] || ""}</span> ` : ""}${esc(it.summary)}</td>
    </tr>`).join("") || `<tr><td colspan="6" class="muted">검색 결과 없음</td></tr>`;
    $("#moreBtn").hidden = rows.length <= state.histLimit;
  }

  function exportCSV() {
    const rows = historyRows();
    const head = ["일자", "시간", "시장", "종목", "카테고리", "세부유형", "공시명", "방향", "요약", "시그널", "링크"];
    const lines = [head, ...rows.map((it) => [it.date, it.time, it.market, it.corp, CATS[it.category].name, it.subtype,
      it.title, DIR_LABEL[it.direction] || "", it.summary, it.signal ? "Y" : "N", it.url])]
      .map((r) => r.map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`).join(","));
    const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `disclosures_${state.index.latest}.csv` });
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function renderWatch() {
    $("#watchCount").textContent = state.watch.size || "";
    const corps = [...new Set(state.items.map((it) => it.corp))].sort();
    $("#corpList").innerHTML = corps.map((c) => `<option value="${esc(c)}">`).join("");
    $("#watchChips").innerHTML = [...state.watch].map((c) =>
      `<span class="chip">${esc(c)}<button data-unwatch="${esc(c)}" title="삭제">✕</button></span>`).join("");
    if (!state.watch.size) {
      $("#watchList").innerHTML = `<div class="empty">관심종목을 추가하면 해당 종목의 추적 공시를 모아 보여줍니다.</div>`;
      return;
    }
    $("#watchList").innerHTML = [...state.watch].map((c) => {
      const list = state.items.filter((it) => it.corp === c && passes(it, { ignoreSignal: true }))
        .sort((a, b) => b.date.localeCompare(a.date) || b.time.localeCompare(a.time));
      return `<div class="watch-group"><h3>${esc(c)} <span class="count">${list.length}</span></h3>
        ${list.length ? list.slice(0, 30).map((it) => card(it, { showDate: true })).join("") : `<div class="empty">수집 기간 내 추적 공시 없음</div>`}</div>`;
    }).join("");
  }

  function render() {
    renderMeta();
    if (!state.index || !state.index.latest) {
      $("#tab-today").innerHTML = `<div class="empty">아직 수집된 공시가 없습니다. GitHub Actions에서 daily-collect 워크플로를 한 번 실행해 주세요.</div>`;
      return;
    }
    $$(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === state.tab));
    $$(".panel").forEach((p) => { p.hidden = p.id !== `tab-${state.tab}`; });
    $$("#marketSeg button").forEach((b) => b.classList.toggle("on", b.dataset.v === state.market));
    $("#signalOnly").checked = state.signalOnly;
    $("#showAmended").checked = state.showAmended;
    ({ today: renderToday, overlap: renderOverlap, history: renderHistory, watch: renderWatch })[state.tab]();
    $("#watchCount").textContent = state.watch.size || "";
  }

  // ---------------------------------------------------------------- 새 공시 알림
  function newSinceLastVisit() {
    const seen = new Set(store.get("seenIds", []));
    const first = seen.size === 0;
    const recentDates = new Set(state.index.days.slice(0, 3).map((d) => d.date));
    const recent = state.items.filter((it) => recentDates.has(it.date));
    const fresh = first ? [] : recent.filter((it) => !seen.has(it.id));
    return { fresh, recentIds: recent.map((it) => it.id) };
  }

  function showBanner() {
    const { fresh, recentIds } = newSinceLastVisit();
    if (!store.get("seenIds", null)) store.set("seenIds", recentIds);
    if (!fresh.length) { $("#banner").hidden = true; return fresh; }
    const sig = fresh.filter((it) => it.signal);
    const watched = fresh.filter((it) => state.watch.has(it.corp));
    let msg = `📬 마지막 확인 이후 새 공시 ${fresh.length}건 (상승·매수 시그널 ${sig.length}건)`;
    if (watched.length) msg += ` · ⭐ 관심종목 ${[...new Set(watched.map((w) => w.corp))].join(", ")}`;
    $("#bannerText").textContent = msg;
    $("#banner").hidden = false;
    $("#bannerOk").onclick = () => { store.set("seenIds", recentIds); $("#banner").hidden = true; };
    return fresh;
  }

  function notify(fresh) {
    if (!("Notification" in window) || Notification.permission !== "granted" || !fresh.length) return;
    const sig = fresh.filter((it) => it.signal);
    const counts = Object.keys(CATS).map((k) => `${CATS[k].short} ${sig.filter((it) => it.category === k).length}`).join(" · ");
    const watched = [...new Set(fresh.filter((it) => state.watch.has(it.corp)).map((it) => it.corp))];
    const n = new Notification("KIND 공시 트래커 - 오늘의 공시 도착", {
      body: `${counts}${watched.length ? `\n⭐ 관심종목: ${watched.join(", ")}` : ""}`,
      tag: `kind-${state.index.latest}`,
    });
    n.onclick = () => { window.focus(); state.tab = "today"; state.day = state.index.latest; render(); };
  }

  function setupNotifyButton() {
    const btn = $("#notifyBtn");
    const sync = () => {
      if (!("Notification" in window)) { btn.textContent = "🔕 알림 미지원 브라우저"; btn.disabled = true; return; }
      btn.textContent = Notification.permission === "granted" ? "🔔 알림 켜짐" : "🔔 알림 켜기";
    };
    btn.onclick = async () => {
      if (!("Notification" in window)) return;
      if (Notification.permission !== "granted") await Notification.requestPermission();
      sync();
      if (Notification.permission === "granted") {
        new Notification("알림이 켜졌습니다", { body: "이 탭이 열려 있으면 매일 19시 전후 새 공시가 올라올 때 알려드립니다." });
      }
    };
    sync();
  }

  async function poll() {
    try {
      const ix = await fetchJSON("data/index.json");
      if (state.index && ix.updated_at !== state.index.updated_at) {
        const followLatest = state.day === state.index.latest;
        await load();
        if (followLatest) state.day = state.index.latest;
        render();
        notify(showBanner());
      }
    } catch (e) { console.warn("poll failed", e); }
  }

  // ---------------------------------------------------------------- events
  function bind() {
    $$(".tabs button").forEach((b) => b.addEventListener("click", () => { state.tab = b.dataset.tab; store.set("tab", state.tab); render(); }));
    $$("#marketSeg button").forEach((b) => b.addEventListener("click", () => { state.market = b.dataset.v; store.set("market", state.market); render(); }));
    $("#signalOnly").addEventListener("change", (e) => { state.signalOnly = e.target.checked; store.set("signalOnly", state.signalOnly); render(); });
    $("#showAmended").addEventListener("change", (e) => { state.showAmended = e.target.checked; store.set("showAmended", state.showAmended); render(); });
    $("#daySel").addEventListener("change", (e) => { state.day = e.target.value; render(); });
    $("#overlapWin").addEventListener("change", render);
    let t;
    const reHist = () => { clearTimeout(t); t = setTimeout(() => { state.histLimit = 200; renderHistory(); }, 150); };
    ["#q", "#hCat", "#hFrom", "#hTo"].forEach((s) => $(s).addEventListener("input", reHist));
    $("#moreBtn").addEventListener("click", () => { state.histLimit += 200; renderHistory(); });
    $("#csvBtn").addEventListener("click", exportCSV);

    document.addEventListener("click", (e) => {
      const star = e.target.closest("[data-star]");
      if (star) { toggleWatch(star.dataset.star); return; }
      const un = e.target.closest("[data-unwatch]");
      if (un) toggleWatch(un.dataset.unwatch);
    });
    $("#watchForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const v = $("#watchInput").value.trim();
      if (v) { state.watch.add(v); saveWatch(); $("#watchInput").value = ""; render(); }
    });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  }

  function saveWatch() { store.set("watch", [...state.watch]); }
  function toggleWatch(corp) {
    state.watch.has(corp) ? state.watch.delete(corp) : state.watch.add(corp);
    saveWatch();
    render();
  }

  // ---------------------------------------------------------------- init
  (async function init() {
    bind();
    setupNotifyButton();
    try {
      await load();
    } catch (e) {
      $("#meta").textContent = "데이터를 불러오지 못했습니다. 첫 수집이 아직 실행되지 않았을 수 있습니다.";
      console.error(e);
      return;
    }
    render();
    showBanner();
    setInterval(poll, POLL_MS);
  })();
})();
