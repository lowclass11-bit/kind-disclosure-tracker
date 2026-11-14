"use strict";
// 발굴 지도 — data/dashboard.json 하나를 읽어 그린다. 서버 없이 GitHub Pages에서 동작.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "");
const pct = (x, d = 0) => (x == null ? "n/a" : (x >= 0 ? "+" : "") + (x * 100).toFixed(d) + "%");
const sig = (p) => (p.smart + p.rev + p.cat) / 3;
const MK = { KR: "한국", US: "미국" };
const VIEWS = ["kr", "us", "sectors", "briefing", "watch", "excluded"];
const Q = [["quiet", "얼마나 소외됐나"], ["smart", "누가 사고 있나"], ["rev", "실적 기대가 오르나"], ["cat", "예정된 계기가 있나"]];

let D = null;
const S = { view: "kr", market: "KR", sel: {}, sec: {}, q: "", sort: "rank", limit: 15 };

function toast(msg) {
  const t = $("toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => (t.hidden = true), 3500);
}
const store = {
  get() { try { return JSON.parse(localStorage.getItem("scout.watch") || "[]"); } catch { return []; } },
  set(v) { try { localStorage.setItem("scout.watch", JSON.stringify(v)); return true; } catch { return false; } },
};

// ---------------------------------------------------------------- 마크다운 (제목·목록·표·굵게·링크)
function inline(s) {
  return esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}
function md(text) {
  const out = []; let list = null, table = false;
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } if (table) { out.push("</table>"); table = false; } };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd(); let m;
    if (!line.trim()) { close(); continue; }
    if ((m = line.match(/^(#{1,3}) (.*)/))) { close(); out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); continue; }
    if (line.startsWith("|")) {
      if (/^\|[-| :]+\|$/.test(line)) continue;
      const cells = line.slice(1, -1).split("|").map((c) => c.trim());
      const tag = table ? "td" : "th";
      if (!table) { close(); out.push("<table>"); table = true; }
      out.push("<tr>" + cells.map((c) => `<${tag}>${inline(c)}</${tag}>`).join("") + "</tr>");
      continue;
    }
    if ((m = line.match(/^[-*] (.*)/))) { if (list !== "ul") { close(); out.push("<ul>"); list = "ul"; } out.push(`<li>${inline(m[1])}</li>`); continue; }
    if ((m = line.match(/^\d+\. (.*)/))) { if (list !== "ol") { close(); out.push("<ol>"); list = "ol"; } out.push(`<li>${inline(m[1])}</li>`); continue; }
    if ((m = line.match(/^> ?(.*)/))) { close(); out.push(`<blockquote>${inline(m[1])}</blockquote>`); continue; }
    close(); out.push(`<p>${inline(line)}</p>`);
  }
  close();
  return out.join("");
}

// ---------------------------------------------------------------- 점도표
const narrow = () => innerWidth < 700;
function quantile(arr, q) {
  if (!arr.length) return 0;
  const a = [...arr].sort((x, y) => x - y);
  return a[Math.min(a.length - 1, Math.floor(a.length * q))];
}
function scale(pts, o = {}) {
  const N = narrow(), W = N ? 380 : (o.W || 720), H = N ? 360 : (o.H || 470);
  const L = N ? 30 : 46, R = W - 12, T = 16, B = H - (N ? 34 : 44);
  const x0 = o.x0 ?? 40;
  const ymax = Math.max(30, Math.ceil((Math.max(0, ...pts.map((p) => p.y)) * 1.12) / 10) * 10);
  return { W, H, L, R, T, B, x0, ymax, fs: N ? 10.5 : 12,
    X: (v) => L + (R - L) * (Math.max(v, x0) - x0) / (100 - x0), Y: (v) => B - (B - T) * Math.min(v, ymax) / ymax };
}
function axes(s, xl, yl) {
  let g = "";
  for (let v = s.x0; v <= 100; v += 20) g += `<line x1="${s.X(v)}" x2="${s.X(v)}" y1="${s.T}" y2="${s.B}" stroke="var(--line)"/><text class="tick" x="${s.X(v)}" y="${s.B + 15}" text-anchor="middle">${v}</text>`;
  for (let v = 0; v <= s.ymax; v += s.ymax > 60 ? 20 : 10) g += `<line x1="${s.L}" x2="${s.R}" y1="${s.Y(v)}" y2="${s.Y(v)}" stroke="var(--line)"/><text class="tick" x="${s.L - 6}" y="${s.Y(v) + 4}" text-anchor="end">${v}</text>`;
  g += `<text class="tick" x="${(s.L + s.R) / 2}" y="${s.H - 4}" text-anchor="middle">${xl}</text>`;
  g += `<text class="tick" x="12" y="${(s.T + s.B) / 2}" transform="rotate(-90 12 ${(s.T + s.B) / 2})" text-anchor="middle">${yl}</text>`;
  return g;
}
function labels(s, pts) {
  const placed = [], gap = s.fs + 3; let g = "";
  for (const p of [...pts].sort((a, b) => b.y - a.y)) {
    const w = [...p.name].reduce((n, ch) => n + (ch.charCodeAt(0) > 255 ? 1 : 0.6), 0) * s.fs;
    let x = s.X(p.x) + (p.pad || 9), y = s.Y(p.y) + 4, guard = 0;
    if (x + w > s.R) x = s.X(p.x) - (p.pad || 9) - w;
    while (placed.some(([px, py, pw]) => x < px + pw + 4 && px < x + w + 4 && Math.abs(py - y) < gap) && guard++ < 30) y += gap;
    placed.push([x, y, w]);
    if (y - s.Y(p.y) > 8) g += `<line x1="${s.X(p.x)}" y1="${s.Y(p.y)}" x2="${x + (x > s.X(p.x) ? -2 : w + 2)}" y2="${y - 4}" stroke="var(--mute)" stroke-width=".7" opacity=".6"/>`;
    g += `<text class="lbl" style="font-size:${s.fs}px" x="${x}" y="${y}">${esc(p.name)}</text>`;
  }
  return g;
}
const TRAIL = '<defs><linearGradient id="trail" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--acc)" stop-opacity=".6"/><stop offset="1" stop-color="var(--acc)" stop-opacity="0"/></linearGradient></defs>';

/** 종목 지도. focus가 있으면 그 종목들만 진하게(업종별 보기). */
function stockMap(list, { selId, labelIds, focus } = {}) {
  // 미국 종목은 회사명이 길어 지도에서는 티커로 표시한다 (점에 마우스를 올리면 회사명)
  const pts = list.map((s) => ({ s, x: s.pillars.quiet, y: sig(s.pillars), name: s.market === "US" ? s.ticker : s.name }));
  const sc = scale(pts);
  const zx = Math.max(sc.x0, quantile(pts.map((p) => p.x), 0.5)), zy = quantile(pts.map((p) => p.y), 0.6);
  const inZone = (p) => p.x >= zx && p.y >= zy;
  const col = (c) => (c >= 3 ? "var(--acc)" : c === 2 ? "var(--acc2)" : "var(--d1)");
  let g = TRAIL;
  if (!focus) {
    g += `<rect x="${sc.X(zx)}" y="${sc.T}" width="${sc.R - sc.X(zx)}" height="${sc.Y(zy) - sc.T}" rx="14" fill="var(--zone)"/>`;
  }
  g += axes(sc, "더 소외됨 (주가가 조용함) →", "근거가 강함 →");
  if (!focus) {
    g += `<text class="zone-t" x="${sc.R - 10}" y="${sc.T + 20}" text-anchor="end">${narrow() ? "발굴 구역" : "발굴 구역 · 조용한데 근거가 쌓임"}</text>`;
    if (!narrow()) g += `<text class="q" x="${sc.R - 10}" y="${sc.B - 10}" text-anchor="end">그냥 싼 종목 · 아직 근거 부족</text><text class="q" x="${sc.L + 10}" y="${sc.T + 20}">근거는 있지만 덜 소외됨</text>`;
  }
  const order = [...pts].sort((a, b) => a.y - b.y);
  // 물기둥: 근거가 강할수록 길다. 위로 떠오르는 힘을 뜻한다.
  for (const p of order) {
    const on = focus ? focus.has(p.s.id) : inZone(p);
    if (!on) continue;
    const len = (p.y / sc.ymax) * (sc.B - sc.T) * 0.34;
    g += `<rect x="${sc.X(p.x) - 2.5}" y="${sc.Y(p.y)}" width="5" height="${len.toFixed(1)}" rx="2.5" fill="url(#trail)"/>`;
  }
  for (const p of order) {
    const x = sc.X(p.x), y = sc.Y(p.y), dim = focus && !focus.has(p.s.id);
    const r = dim ? 2.6 : focus ? 6.5 : inZone(p) ? 5.5 : 3.6;
    const tip = `<title>${esc(p.s.name)} · ${Math.round(p.s.score)}점</title>`;
    g += dim ? `<circle cx="${x}" cy="${y}" r="${r}" fill="var(--dim)"/>`
      : `<circle class="pt" data-id="${esc(p.s.id)}" cx="${x}" cy="${y}" r="${r}" fill="${col(p.s.confirm)}" stroke="var(--card)" stroke-width="1">${tip}</circle>`;
  }
  const sel = pts.find((p) => p.s.id === selId);
  if (sel) g += `<circle cx="${sc.X(sel.x)}" cy="${sc.Y(sel.y)}" r="11" fill="none" stroke="var(--ink)" stroke-width="2"/>`;
  const want = new Set(labelIds || []);
  if (sel) want.add(sel.s.id);
  g += labels(sc, pts.filter((p) => want.has(p.s.id)).map((p) => ({ ...p, pad: p === sel ? 15 : 9 })));
  return { svg: `<svg viewBox="0 0 ${sc.W} ${sc.H}" role="img" aria-label="소외도와 근거 강도로 본 종목 분포">${g}</svg>`, zone: pts.filter(inZone).map((p) => p.s) };
}

// ---------------------------------------------------------------- 종목 패널
const MACRO_EV = "거시 환경이 유리";
const SHORT = { quiet: "소외도", smart: "수급", rev: "실적", cat: "계기" };
const fmtPts = (v) => (v > 0 ? "+" : v < 0 ? "−" : "±") + Math.abs(Math.round(v * 10) / 10);
function evLine(e, { common = false } = {}) {
  const u = safeUrl(e.url);
  const meta = [esc(e.src), e.d ? esc(e.d) : ""].filter(Boolean).join(" · ");
  const cls = e.pts < 0 ? "neg" : e.pts > 0 ? "pos" : "zero";
  return `<li class="ev ${cls}"><span class="pts">${e.pts === 0 ? "참고" : fmtPts(e.pts)}</span><span class="body">${esc(e.t)}${common ? ' <span class="chip">업종 공통 · 근거 판정 제외</span>' : ""}<small>${meta}${u ? ` · <a href="${esc(u)}" target="_blank" rel="noopener noreferrer">원문 ↗</a>` : ""}</small></span></li>`;
}

/** 소외도: 가격 지표로 계산한 기본 점수의 내역 + 가점 근거 */
function quietDetail(s) {
  const q = s.quiet_parts, evs = s.ev.filter((e) => e.p === "quiet");
  let h = "";
  if (q) {
    const turnTxt = [q.rel1_up ? "최근 1개월 시장보다 강함" : "최근 1개월 시장보다 약함", q.above50 ? "50일선 위" : "50일선 아래"].join(", ");
    const rows = [
      ["6개월 시장 대비 수익률", `${pct(q.rel6, 1)}${q.beta ? ` (베타 ${q.beta.toFixed(2)} 감안)` : ""}`, q.a, 35, "+20%면 0점, −30%면 100점"],
      ["52주 범위 안의 위치", `${Math.round(q.pos52 * 100)}% (0%=1년 최저가)`, q.b, 25, "90%면 0점, 10%면 100점"],
      ["최근 20일 거래량 ÷ 1년 평균", `${Math.round(q.vol * 100)}%`, q.c, 15, "130%면 0점, 60%면 100점"],
      ["최근 변동성 ÷ 1년 평균", `${Math.round(q.vola * 100)}%`, q.d, 10, "130%면 0점, 60%면 100점"],
      ["바닥 다지기", turnTxt, q.turn, 15, "두 조건 각 50점"],
    ];
    h += `<div class="tbl-wrap"><table class="calc"><tr><th>항목</th><th>값</th><th>점수</th><th>비중</th><th>기여</th></tr>${rows.map(([n, v, sc, w, rule]) =>
      `<tr><td>${n}<small>${rule}</small></td><td>${esc(v)}</td><td>${Math.round(sc)}</td><td>${w}%</td><td>${(sc * w / 100).toFixed(1)}</td></tr>`).join("")}
      <tr class="sum"><td colspan="4">가격 지표로 계산한 기본 점수</td><td>${(s.quiet_base ?? q.base).toFixed(1)}</td></tr></table></div>`;
  }
  const extra = evs.filter((e) => e.pts !== 0);
  if (extra.length) h += `<ul class="evs-all">${extra.map((e) => evLine(e)).join("")}</ul>`;
  const notes = evs.filter((e) => e.pts === 0);
  if (notes.length) h += `<ul class="evs-all">${notes.map((e) => evLine(e)).join("")}</ul>`;
  h += `<p class="sumline">기본 ${(s.quiet_base ?? 0).toFixed(1)}${extra.length ? " + 가점 " + extra.reduce((n, e) => n + e.pts, 0).toFixed(1) : ""} = <b>${Math.round(s.pillars.quiet)}점</b> (100점 상한)</p>`;
  return h;
}

/** 스마트머니·실적·촉매: 근거 하나하나의 점수 합계 */
function axisDetail(s, k) {
  const evs = s.ev.filter((e) => e.p === k);
  if (!evs.length) return '<p class="sumline muted">확인된 근거가 없어 0점입니다.</p>';
  const total = evs.reduce((n, e) => n + e.pts, 0);
  let h = `<ul class="evs-all">${evs.map((e) => evLine(e, { common: e.t.startsWith(MACRO_EV) })).join("")}</ul>`;
  h += `<p class="sumline">합계 ${total.toFixed(1)} → <b>${Math.round(s.pillars[k])}점</b>${total > 100 ? " (100점 상한)" : total < 0 ? " (0점 하한)" : ""}</p>`;
  const bar = (D.bar && D.bar[s.market] && D.bar[s.market][k]) ?? 25, core = s.core ? s.core[k] : null;
  if (core != null) {
    const ok = core >= bar;
    h += `<p class="judge ${ok ? "ok" : ""}">${ok ? "✓ 근거 있음" : "✗ 근거로 치지 않음"} · 종목 고유 근거 ${core.toFixed(1)}점 ${ok ? "≥" : "<"} 기준 ${bar}점${core !== total ? " (업종 공통 거시 가점 제외)" : ""}</p>`;
  }
  return h;
}

function trendHtml(s) {
  const tb = D.trend_base, t = s.trend;
  const recent = s.recent_ev ? ` · 최근 14일 안에 나온 근거 ${s.recent_ev}건` : "";
  if (!tb) return `<div class="trend"><b>근거 추세</b> 비교할 지난 기록이 아직 없습니다. 다음 수집부터 지난주 대비 변화가 표시됩니다.${recent}</div>`;
  if (!t) return `<div class="trend"><b>근거 추세</b> ${esc(tb.date)}에는 관찰 대상이 아니어서 비교할 수 없습니다.${recent}</div>`;
  const dir = t.d >= 3 ? "up" : t.d <= -3 ? "down" : "flat";
  const word = t.new ? "새로 등장 — 지난 기록에는 근거가 없던 종목입니다" : dir === "up" ? "근거가 쌓이는 중" : dir === "down" ? "근거가 줄어드는 중" : "큰 변화 없음";
  const ax = Object.entries(t.axes).map(([k, v]) => `<span class="${v > 0 ? "pos" : v < 0 ? "neg" : ""}">${SHORT[k]} ${v > 0 ? "+" : ""}${Math.round(v)}</span>`).join(" · ");
  return `<div class="trend ${dir}"><b>근거 추세</b> ${esc(tb.date)} 대비(${tb.days}일) 근거 점수 <b class="d">${t.d > 0 ? "+" : ""}${t.d.toFixed(1)}</b> · ${word}<br><small>${ax}${recent}</small></div>`;
}
function trendBadge(t) {
  if (!t) return "";
  if (t.new) return '<span class="tb up">NEW</span>';
  if (t.d >= 3) return `<span class="tb up">▲${Math.round(t.d)}</span>`;
  if (t.d <= -3) return `<span class="tb down">▼${Math.round(-t.d)}</span>`;
  return "";
}
function panel(s) {
  if (!s) return '<p class="empty">지도에서 점을 누르거나 아래 목록에서 종목을 고르세요.</p>';
  const watching = store.get().some((w) => w.id === s.id);
  const url = safeUrl(s.url);
  const w = D.weights || { quiet: 0.3, smart: 0.25, rev: 0.25, cat: 0.2 };
  const has = Q.slice(1).filter(([k]) => s.core && s.core[k] >= ((D.bar && D.bar[s.market] && D.bar[s.market][k]) ?? 25)).map(([, t]) => t);
  let h = `<div class="who"><div><h2>${esc(s.name)}</h2><small>${esc(s.ticker)} · ${esc(s.sector)} · 6개월 ${pct(s.ret_6m)}</small></div><div class="score" title="종합 점수">${Math.round(s.score)}</div></div>`;
  h += `<p class="rankline">${MK[s.market]} 후보 ${s.rank_of}개 중 ${s.rank}위 · 한국과 미국은 따로 순위를 매깁니다</p>`;
  h += `<div class="formula">종합 ${s.score.toFixed(1)} = ${Q.map(([k]) => `${SHORT[k]} ${Math.round(s.pillars[k])}×${Math.round(w[k] * 100)}%`).join(" + ")}</div>`;
  h += `<p class="rankline">근거가 겹친 축 ${s.confirm}개${has.length ? ": " + has.join(", ") : ""} · 근거 ${s.ev.filter((e) => e.pts !== 0).length}건</p>`;
  h += trendHtml(s);
  h += Q.map(([k, t], i) => `<details class="g"${i < 2 || s.pillars[k] > 0 ? " open" : ""}><summary><span class="t">${t}<span>${Math.round(s.pillars[k])}</span></span><span class="bar"><b style="width:${Math.max(0, Math.min(100, s.pillars[k]))}%"></b></span></summary>${k === "quiet" ? quietDetail(s) : axisDetail(s, k)}</details>`).join("");
  if (s.below_200 && (s.ret_1m || 0) < 0) h += '<div class="note warn">주의 · 아직 하락 추세입니다 (200일선 아래, 최근 1개월도 약세)</div>';
  h += '<div class="btns">';
  if (s.doc) h += `<button type="button" class="primary" data-doc="${esc(s.id)}">투자 메모 · 반론 보기</button>`;
  h += `<button type="button" data-watch="${esc(s.id)}" aria-pressed="${watching}">${watching ? "★ 추적 중" : "☆ 추적표에 추가"}</button>`;
  if (url) h += `<a class="btn" href="${esc(url)}" target="_blank" rel="noopener noreferrer">시세 ↗</a>`;
  h += "</div>";
  if (!s.doc) h += '<p class="rankline">투자 메모는 시장별 상위 후보에만 미리 만들어 둡니다.</p>';
  return h;
}
function rowHtml(s, i, selId) {
  const first = s.ev.find((e) => e.pts > 0) || s.ev[0];
  const down = s.below_200 && (s.ret_1m || 0) < 0;
  return `<button type="button" class="row${s.id === selId ? " on" : ""}" data-id="${esc(s.id)}"><span class="rk">${i}</span>
    <span class="nm"><b>${esc(s.name)}</b><small>${esc(s.sector)} · 6개월 ${pct(s.ret_6m)}</small><span class="pips" aria-hidden="true">${Q.map(([k]) => `<u class="${s.pillars[k] >= 40 ? "on" : ""}"></u>`).join("")}</span>${trendBadge(s.trend)}${down ? '<span class="tag">하락 추세</span>' : ""}</span>
    <span class="n">${Math.round(s.score)}</span><p>${first ? esc(first.t) : ""}</p></button>`;
}

// ---------------------------------------------------------------- 보기: 한국 / 미국
function marketStocks(m) { return D.stocks.filter((s) => s.market === m).sort((a, b) => a.rank - b.rank); }

function renderMarket() {
  const m = S.market, all = marketStocks(m);
  if (!all.length) { $("view").innerHTML = '<p class="empty">이 시장의 후보가 없습니다.</p>'; return; }
  if (!all.some((s) => s.id === S.sel[m])) S.sel[m] = all[0].id;
  const sel = all.find((s) => s.id === S.sel[m]);
  const map = stockMap(all, { selId: sel.id, labelIds: all.slice(0, narrow() ? 5 : 9).map((s) => s.id) });
  const st = D.stats, key = m.toLowerCase();
  const q = S.q.trim().toLowerCase();
  let list = q ? all.filter((s) => (s.name + " " + s.ticker + " " + s.sector).toLowerCase().includes(q)) : all;
  if (S.sort === "trend") list = [...list].sort((a, b) => ((b.trend || {}).d ?? -999) - ((a.trend || {}).d ?? -999));
  else if (S.sort !== "rank") list = [...list].sort((a, b) => b.pillars[S.sort] - a.pillars[S.sort]);
  const usNote = m === "US" ? " 미국 종목은 S&amp;P 500 대비 <b>베타를 감안한</b> 수익률로 소외도를 재고, 미국 종목끼리만 비교합니다." : "";
  $("view").innerHTML = `
    <p class="say">${MK[m]} ${st[key]}종목 중 <b>오른쪽 위</b>에 있는 종목이 "주가는 조용한데 근거가 쌓이는" 곳입니다. 오늘은 <b>${map.zone.length}종목</b>이 들어와 있습니다.${usNote}</p>
    <div class="funnel"><span><b>${st[key]}</b>관찰</span><span><b>${st[key] - st["hot_" + key]}</b>이미 오른 ${st["hot_" + key]}종목 제외</span><span><b>${all.length}</b>조용하고 근거가 있는 후보</span><span><b>${map.zone.length}</b>발굴 구역</span></div>
    <div class="main">
      <section class="card" id="mapCard">${map.svg}
        <div class="legend"><span><i style="background:var(--acc)"></i>근거 3가지 겹침</span><span><i style="background:var(--acc2)"></i>2가지</span><span><i style="background:var(--d1)"></i>1가지</span><span><i class="col"></i>기둥이 길수록 근거가 강함</span><span>점을 누르면 이유가 나옵니다</span></div></section>
      <aside class="card" id="side">${panel(sel)}</aside>
    </div>
    <div class="tools"><h2>${MK[m]} 후보 ${all.length}</h2>
      <input type="search" id="q" placeholder="종목·업종 찾기" value="${esc(S.q)}" aria-label="종목·업종 찾기">
      <select id="sort" aria-label="정렬"><option value="rank">종합 순위순</option><option value="quiet">소외도순</option><option value="smart">누가 사나 순</option><option value="rev">실적 기대순</option><option value="cat">계기순</option><option value="trend">근거 증가순</option></select></div>
    <div class="rows" id="rows">${list.slice(0, S.limit).map((s, i) => rowHtml(s, S.sort === "rank" && !q ? s.rank : i + 1, sel.id)).join("") || '<p class="empty">찾는 종목이 없습니다.</p>'}</div>
    ${list.length > S.limit ? `<button type="button" class="more" id="more">더 보기 (${list.length - S.limit}개 남음)</button>` : ""}
    <div class="tools"><h2>거시 환경</h2></div><div class="strip">${macroHtml()}</div>`;
  $("sort").value = S.sort;
}
function macroHtml() {
  return D.macro.rows.map((r) => `<div class="${r.tag ? "on" : ""}"><b>${esc(r.name)}</b><span>${esc(r.value)} ${esc(r.change)}</span><small>${esc(r.note)}</small></div>`).join("")
    || '<p class="muted">거시 자료가 없습니다.</p>';
}
function select(id, scroll) {
  const s = D.stocks.find((x) => x.id === id);
  if (!s) return;
  S.market = s.market; S.sel[s.market] = id;
  if (S.view !== s.market.toLowerCase()) { location.hash = s.market.toLowerCase(); return; }
  renderMarket();
  if (scroll && narrow()) $("side").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------------------------------------------------------------- 보기: 업종별
function renderSectors() {
  const m = S.market, stocks = marketStocks(m);
  const count = {};
  for (const s of stocks) count[s.sector_id] = (count[s.sector_id] || 0) + 1;
  const secs = D.sectors.filter((x) => x.market === m && count[x.id]).sort((a, b) => b.score - a.score);
  if (!secs.length) { $("view").innerHTML = '<p class="empty">업종 자료가 없습니다.</p>'; return; }
  if (!secs.some((x) => x.id === S.sec[m])) S.sec[m] = secs[0].id;
  const pick = secs.find((x) => x.id === S.sec[m]);
  const pts = secs.map((x) => ({ x: x.pillars.quiet, y: sig(x.pillars), name: x.name, sec: x, k: count[x.id] }));
  const sc = scale(pts, { W: 600, H: 440, x0: 30 });
  const rad = (k) => (narrow() ? 3 : 4) + Math.sqrt(k) * (narrow() ? 2.6 : 3.8);
  let g = TRAIL + axes(sc, "더 소외됨 →", "근거가 강함 →");
  for (const p of [...pts].sort((a, b) => b.k - a.k)) {
    const on = p.sec === pick;
    g += `<circle class="pt" data-sec="${esc(p.sec.id)}" cx="${sc.X(p.x)}" cy="${sc.Y(p.y)}" r="${rad(p.k)}" fill="${on ? "var(--hl)" : "var(--card)"}" fill-opacity="${on ? 1 : 0.7}" stroke="var(--ink)" stroke-width="${on ? 2.5 : 1}"><title>${esc(p.name)} · 후보 ${p.k}개 · ${Math.round(p.sec.score)}점</title></circle>`;
  }
  const lab = new Set(secs.slice(0, narrow() ? 5 : 9).map((x) => x.id)); lab.add(pick.id);
  g += labels(sc, pts.filter((p) => lab.has(p.sec.id)).map((p) => ({ ...p, pad: rad(p.k) + 3 })));
  const mine = stocks.filter((s) => s.sector_id === pick.id);
  const map2 = stockMap(stocks, { focus: new Set(mine.map((s) => s.id)), labelIds: mine.map((s) => s.id) });
  $("view").innerHTML = `
    <p class="say">업종부터 고르고, 그 안에서 종목을 고릅니다. 원이 클수록 후보가 많은 업종이고, <b>오른쪽 위</b>일수록 조용한데 근거가 쌓인 업종입니다.</p>
    <div class="tools"><div class="seg" role="group" aria-label="시장">${["KR", "US"].map((k) => `<button type="button" data-mk="${k}" aria-pressed="${k === m}">${MK[k]}</button>`).join("")}</div></div>
    <div class="two">
      <section class="card"><h2>① 업종을 고릅니다</h2><p class="sub">원을 누르면 옆 지도가 그 업종으로 바뀝니다</p>
        <svg viewBox="0 0 ${sc.W} ${sc.H}" role="img" aria-label="업종 분포">${g}</svg>
        <div class="rows" style="margin-top:12px">${secs.slice(0, 8).map((x, i) => `<button type="button" class="row${x === pick ? " on" : ""}" data-sec="${esc(x.id)}"><span class="rk">${i + 1}</span><span class="nm"><b>${esc(x.name)}</b><small>후보 ${count[x.id]}개</small>${x.trend_d != null ? trendBadge({ d: x.trend_d }) : ""}</span><span class="n">${Math.round(x.score)}</span><p>${esc(x.leaders.slice(0, 3).map((l) => l.name).join(" · "))}</p></button>`).join("")}</div></section>
      <section class="card"><h2>② ${esc(pick.name)} 안에서 종목을 고릅니다</h2><p class="sub">진한 점이 이 업종의 후보 · 종합 ${Math.round(pick.score)}점</p>
        ${map2.svg}
        <details class="g sec-ev" open><summary><span class="t">이 업종의 근거 ${pick.ev.length}건</span></summary>
          <p class="sumline muted">업종 점수 = 소외도는 구성 종목 중앙값, 나머지 축은 구성 종목 상위 절반의 평균에 아래 업종 근거의 점수를 더한 값입니다.</p>
          <ul class="evs-all">${pick.ev.map((e) => evLine(e)).join("")}</ul>
          <p class="sumline">${Q.map(([k, t]) => `${t} <b>${Math.round(pick.pillars[k])}</b>`).join(" · ")}</p></details>
        ${pick.doc ? `<div class="btns"><button type="button" class="primary" data-doc="${esc(pick.id)}">업종 메모 보기</button></div>` : ""}
        <div class="rows" style="margin-top:12px">${mine.map((s) => rowHtml(s, s.rank, null)).join("")}</div></section>
    </div>`;
}

// ---------------------------------------------------------------- 보기: 브리핑 / 추적표 / 제외됨
async function renderBriefing() {
  $("view").innerHTML = '<p class="muted">브리핑을 불러오는 중…</p>';
  try {
    const b = await (await fetch("data/briefing.json", { cache: "no-store" })).json();
    if (S.view !== "briefing") return;
    $("view").innerHTML = `<section class="card doc">${md(b.text)}</section>${trackHtml()}`;
  } catch { $("view").innerHTML = '<p class="empty">브리핑을 불러오지 못했습니다.</p>'; }
}
function trackHtml() {
  if (!D.track.length) return '<div class="tools"><h2>발굴 성적표</h2></div><p class="muted">아직 비교할 기록이 없습니다. 자료가 7일 이상 쌓이면, 과거에 후보로 뽑혔던 종목들이 이후 시장보다 잘했는지 여기에 표시됩니다.</p>';
  return `<div class="tools"><h2>발굴 성적표</h2></div><p class="muted">그날 후보였던 종목들의 이후 평균 수익률입니다.</p><div class="card tbl-wrap"><table><tr><th>뽑은 날</th><th>경과</th><th>종목 수</th><th>평균 수익률</th><th>오른 비율</th><th>시장 대비</th></tr>${
    D.track.map((t) => `<tr><td>${esc(t.date)}</td><td>${t.days}일</td><td>${t.n}</td><td class="${t.avg >= 0 ? "pos" : "neg"}">${pct(t.avg, 1)}</td><td>${Math.round(t.win * 100)}%</td><td class="${(t.excess || 0) >= 0 ? "pos" : "neg"}">${pct(t.excess, 1)}</td></tr>`).join("")}</table></div>`;
}
function renderWatch() {
  const rows = store.get();
  if (!rows.length) {
    $("view").innerHTML = '<p class="say">종목 화면에서 <b>☆ 추적표에 추가</b>를 누르면 그날의 가격이 기록됩니다. 6개월 뒤 내 발굴이 맞았는지 여기서 확인하세요.</p><p class="empty">아직 추적 중인 종목이 없습니다.</p>';
    return;
  }
  const today = new Date(D.date);
  $("view").innerHTML = `<p class="say">추가한 날의 가격과 지금 가격을 비교합니다. 이 목록은 <b>이 브라우저에만</b> 저장됩니다.</p>
    <div class="card tbl-wrap"><table><tr><th>종목</th><th>추가한 날</th><th>그때 가격</th><th>지금 가격</th><th>수익률</th><th>경과</th><th>6개월 재평가일</th><th></th></tr>${rows.map((w) => {
      const now = D.prices[w.id], r = now && w.price0 ? now / w.price0 - 1 : null;
      const d0 = new Date(w.date), days = Math.round((today - d0) / 864e5), rev = new Date(d0.getTime() + 182 * 864e5).toISOString().slice(0, 10);
      return `<tr><td><b>${esc(w.name)}</b> <span class="muted">${MK[w.market] || ""}</span></td><td>${esc(w.date)}</td><td>${Number(w.price0).toLocaleString()}</td><td>${now ? Number(now).toLocaleString() : "n/a"}</td><td class="${r == null ? "" : r >= 0 ? "pos" : "neg"}">${pct(r, 1)}</td><td>${days}일</td><td>${rev}</td><td><button type="button" data-unwatch="${esc(w.id)}" aria-label="${esc(w.name)} 빼기">빼기</button></td></tr>`;
    }).join("")}</table></div>`;
}
function renderExcluded() {
  $("view").innerHTML = `<p class="say">이미 많이 올라 "잠잠하다"는 조건에서 벗어난 종목입니다. 시장 대비 6개월 수익률이 높은 순으로 ${D.excluded.length}개를 보여 줍니다.</p>
    <div class="card tbl-wrap"><table><tr><th>종목</th><th>시장</th><th>업종</th><th>6개월 수익률</th><th>시장 대비</th></tr>${D.excluded.map((s) => `<tr><td><b>${esc(s.name)}</b></td><td>${MK[s.market]}</td><td>${esc(s.sector)}</td><td>${pct(s.ret_6m)}</td><td>${pct(s.rel_6m)}</td></tr>`).join("")}</table></div>`;
}

// ---------------------------------------------------------------- 문서 창
const docState = { docs: null, kind: "memo" };
async function openDoc(id) {
  const it = D.stocks.find((s) => s.id === id) || D.sectors.find((s) => s.id === id);
  if (!it || !it.doc) return;
  try {
    const r = await fetch(`data/items/${encodeURIComponent(it.doc)}.json`, { cache: "no-store" });
    if (!r.ok) throw new Error();
    docState.docs = (await r.json()).docs;
  } catch { return toast("문서를 불러오지 못했습니다."); }
  docState.kind = docState.docs.ai_memo ? "ai_memo" : "memo";
  $("docTitle").textContent = it.name;
  showDoc();
  $("docDlg").showModal();
}
function showDoc() {
  const names = { ai_memo: "AI 해석 메모", memo: "투자 메모", script: "콘텐츠 대본" };
  $("docTabs").innerHTML = Object.keys(names).filter((k) => docState.docs[k]).map((k) => `<button type="button" data-kind="${k}" aria-pressed="${k === docState.kind}">${names[k]}</button>`).join("");
  $("docBody").innerHTML = md(docState.docs[docState.kind] || "");
  $("docBody").scrollTop = 0;
}

// ---------------------------------------------------------------- 뼈대
function aboutHtml() {
  const src = Object.entries(D.sources).map(([k, v]) => `<li>${esc(k)} — ${v.state === "받음" && v.count != null ? `받음 ${Number(v.count).toLocaleString()}건` : esc(v.state)}${v.note ? ` (${esc(v.note)})` : ""}</li>`).join("");
  return `<ul><li><b>가로축 소외도</b>: 6개월 시장 대비 수익률, 52주 범위 내 위치, 거래량·변동성 축소로 계산. 미국 종목은 S&amp;P 500 대비 베타를 감안한 수익률을 씁니다.</li>
    <li><b>세로축 근거</b>: 누가 사고 있나(수급·내부자·기관), 실적 기대(추정치), 예정된 계기(수주·증설·실적 발표·뉴스) 세 점수의 평균.</li>
    <li><b>발굴 구역</b>: 그 시장 후보들 가운데 소외도 상위 절반이면서 근거 상위 40%인 영역. 한국과 미국은 각자 기준으로 따로 정합니다.</li>
    <li><b>근거 축 개수</b>: 한국은 축 점수 25점 이상, 미국은 자료 종류가 적어 미국 종목 중 상위 30% 점수(15~25점)를 기준으로 셉니다.</li>
    <li><b>근거 추세</b>: 5일 이상 지난 기록 중 가장 최근 것과 비교한 근거 점수(수급·실적·계기 평균, 거시 가점 제외) 변화. ▲는 3점 이상 늘어남, NEW는 그때 근거가 없던 종목. 종합 점수에는 넣지 않습니다.</li>
    <li><b>뉴스</b>: 촉매 단어가 있어도 적자·하락·소송 같은 부정적인 말이 제목에 있으면 점수에서 빼고 "참고"로만 보여 줍니다.</li>
    <li>투자 스타일 ${esc(D.style_label)} · 이미 많이 오른 종목은 제외</li></ul><p><b>오늘 받은 자료</b></p><ul>${src}</ul>`;
}
function render() {
  for (const a of $("tabs").querySelectorAll("a")) a.setAttribute("aria-current", a.dataset.v === S.view ? "page" : "false");
  if (S.view === "kr" || S.view === "us") { S.market = S.view.toUpperCase(); renderMarket(); }
  else if (S.view === "sectors") renderSectors();
  else if (S.view === "briefing") renderBriefing();
  else if (S.view === "watch") renderWatch();
  else renderExcluded();
}
function route() {
  const h = location.hash.slice(1);
  S.view = VIEWS.includes(h) ? h : "kr";
  S.limit = 15;
  if (D) render();
}

$("view").addEventListener("click", (e) => {
  const t = e.target.closest("[data-id],[data-sec],[data-doc],[data-watch],[data-unwatch],[data-mk],#more");
  if (!t) return;
  if (t.id === "more") { S.limit += 30; return renderMarket(); }
  if (t.dataset.doc) return openDoc(t.dataset.doc);
  if (t.dataset.mk) { S.market = t.dataset.mk; return renderSectors(); }
  if (t.dataset.sec) { S.sec[S.market] = t.dataset.sec; return renderSectors(); }
  if (t.dataset.unwatch) { store.set(store.get().filter((w) => w.id !== t.dataset.unwatch)); return renderWatch(); }
  if (t.dataset.watch) {
    const s = D.stocks.find((x) => x.id === t.dataset.watch), cur = store.get();
    const next = cur.some((w) => w.id === s.id) ? cur.filter((w) => w.id !== s.id)
      : [...cur, { id: s.id, name: s.name, market: s.market, date: D.date, price0: s.price }];
    if (!store.set(next)) return toast("이 브라우저에서는 저장할 수 없습니다.");
    toast(next.length > cur.length ? "추적표에 추가했습니다." : "추적표에서 뺐습니다.");
    return renderMarket();
  }
  if (t.dataset.id) select(t.dataset.id, t.classList.contains("row") || t.classList.contains("pt"));
});
$("view").addEventListener("input", (e) => {
  if (e.target.id !== "q") return;
  S.q = e.target.value; S.limit = 15;
  const pos = e.target.selectionStart;
  renderMarket();
  const q = $("q"); q.focus(); q.setSelectionRange(pos, pos);
});
$("view").addEventListener("change", (e) => { if (e.target.id === "sort") { S.sort = e.target.value; renderMarket(); } });
$("docTabs").addEventListener("click", (e) => { const k = e.target.dataset.kind; if (k) { docState.kind = k; showDoc(); } });
$("docClose").onclick = () => $("docDlg").close();
$("docCopy").onclick = async () => {
  try { await navigator.clipboard.writeText(docState.docs[docState.kind]); toast("복사했습니다."); }
  catch { toast("복사하지 못했습니다. 본문을 직접 선택해 복사하세요."); }
};
addEventListener("hashchange", route);
let resizeTimer, lastNarrow = narrow();
addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (D && narrow() !== lastNarrow) { lastNarrow = narrow(); render(); } }, 200);
});

(async function init() {
  try {
    const r = await fetch("data/dashboard.json", { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    D = await r.json();
  } catch {
    $("view").innerHTML = '<p class="empty">자료를 불러오지 못했습니다. 아직 첫 수집이 끝나지 않았을 수 있습니다.</p>';
    $("asof").textContent = "";
    return;
  }
  $("asof").textContent = `${D.generated_at.replace("T", " ").slice(0, 16)} 기준`;
  $("aboutBody").innerHTML = aboutHtml();
  route();
})();
