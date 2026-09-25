"""공시 본문(DART 뷰어)을 받아 핵심 수치를 추출한다."""
from __future__ import annotations

import re
import time

import requests
from bs4 import BeautifulSoup

from .classify import CAPEX, EARNINGS, STAKE
from .sources import TIMEOUT

MAIN_URL = "https://dart.fss.or.kr/dsaf001/main.do"
VIEWER_URL = "https://dart.fss.or.kr/report/viewer.do"
SKIP_NODE = re.compile(r"제1부|제2부|제4부|첨부|부속|별첨|신고의무")

Rows = list[list[str]]


# ---------------------------------------------------------------- fetch
def _nodes(session: requests.Session, rcp: str) -> list[dict]:
    html = session.get(MAIN_URL, params={"rcpNo": rcp}, timeout=TIMEOUT).text
    nodes = []
    for blk in re.split(r"node1\s*=\s*\{\};", html)[1:]:
        d = dict(re.findall(r"node1\['(\w+)'\]\s*=\s*\"([^\"]*)\"", blk))
        if "dcmNo" in d:
            nodes.append(d)
    if not nodes:
        m = re.search(
            r'viewDoc\("(\d+)", "(\d+)", "(\d+)", "(\d+)", "(\d+)", "([\w.]+)"', html
        )
        if m:
            keys = ["rcpNo", "dcmNo", "eleId", "offset", "length", "dtd"]
            nodes.append(dict(zip(keys, m.groups()), text=""))
    return nodes


def fetch_rows(session: requests.Session, rcp: str, max_nodes: int = 6) -> Rows:
    nodes = _nodes(session, rcp)
    if len(nodes) > 1:
        # 표지·첨부 등 수치가 없는 목차는 건너뛴다.
        nodes = [n for n in nodes if not SKIP_NODE.search(n.get("text", ""))] or nodes
    rows: Rows = []
    for n in nodes[:max_nodes]:
        params = {k: n[k] for k in ["rcpNo", "dcmNo", "eleId", "offset", "length", "dtd"]}
        resp = session.get(VIEWER_URL, params=params, timeout=TIMEOUT)
        soup = BeautifulSoup(resp.content, "lxml")
        for tr in soup.select("tr"):
            cells = [" ".join(td.get_text().split()) for td in tr.find_all(["td", "th"])]
            if any(cells):
                rows.append(cells)
        time.sleep(0.25)
    return rows


# ---------------------------------------------------------------- helpers
def num(s: str | None) -> float | None:
    if s is None:
        return None
    t = s.strip().replace(",", "").replace(" ", "")
    neg = False
    if t.startswith(("△", "▽", "Δ")):
        neg, t = True, t[1:]
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    t = t.rstrip("%")
    if not re.fullmatch(r"-?\d+(\.\d+)?", t):
        return None
    v = float(t)
    return -v if neg else v


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def pick_num(rows: Rows, label: str, which: str = "last") -> float | None:
    """label 셀 뒤에 오는 첫 번째 숫자."""
    pat = re.compile(label)
    found = None
    for cells in rows:
        for i, c in enumerate(cells):
            if pat.search(_norm(c)):
                for nxt in cells[i + 1:]:
                    v = num(nxt)
                    if v is not None:
                        found = v
                        break
                else:
                    continue
                if which == "first":
                    return found
                break
    return found


def pick_text(rows: Rows, label: str, which: str = "last", limit: int = 160) -> str:
    pat = re.compile(label)
    found = ""
    for cells in rows:
        for i, c in enumerate(cells):
            if pat.search(_norm(c)):
                for nxt in cells[i + 1:]:
                    if nxt and nxt not in ("-", "--"):
                        found = nxt[:limit]
                        break
                if found and which == "first":
                    return found
                break
    return found


def strip_amendment(rows: Rows) -> Rows:
    """정정공시는 앞부분의 '정정전/정정후' 비교표를 잘라내고 정정 후 본문만 남긴다."""
    for i, cells in enumerate(rows):
        if any("정정후" in _norm(c) for c in cells):
            for j in range(i + 1, len(rows)):
                if rows[j] and re.match(r"^1\.", _norm(rows[j][0])):
                    return rows[j:]
            break
    return rows


def row_nums(cells: list[str]) -> list[float | None]:
    return [num(c) for c in cells]


def pct_str(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:+,.1f}%"


def eok(v: float | None) -> str:
    """원 → 억원 표기."""
    if v is None:
        return "n/a"
    e = v / 1e8
    if abs(e) >= 10000:
        return f"{e / 10000:,.2f}조"
    if abs(e) >= 10:
        return f"{e:,.0f}억"
    return f"{e:,.1f}억"


# ---------------------------------------------------------------- earnings
EARN_LABELS = [("revenue", r"^-?매출액"), ("op", r"^-?영업이익"),
               ("pretax", r"^-?법인세"), ("net", r"^-?당기순이익")]


def parse_earnings(rows: Rows) -> dict:
    out: dict = {}
    unit = ""
    for cells in rows:
        joined = " ".join(cells)
        m = re.search(r"단위\s*[:：]\s*([^)\s]+)", joined)
        if m and ("손익구조" in joined or "매출액" in joined):
            unit = m.group(1)
        if not cells:
            continue
        head = _norm(cells[0])
        for key, pat in EARN_LABELS:
            if re.match(pat, head):
                vals = row_nums(cells[1:])
                if len([v for v in vals if v is not None]) >= 2:
                    cur, prev = vals[0], vals[1] if len(vals) > 1 else None
                    pct = vals[3] if len(vals) > 3 else None
                    turn = cells[5] if len(cells) > 5 and cells[5] not in ("-", "") else ""
                    if pct is None and cur is not None and prev not in (None, 0) and prev > 0:
                        pct = (cur - prev) / prev * 100
                    out[key] = {"cur": cur, "prev": prev, "pct": pct, "turn": turn}
    out["unit"] = unit
    out["fs_type"] = pick_text(rows, r"재무제표의종류|재무제표구분")
    out["reason"] = pick_text(rows, r"주요원인", limit=200)

    def improved(k: str) -> bool | None:
        d = out.get(k)
        if not d:
            return None
        if "흑자" in d["turn"]:
            return True
        if "적자" in d["turn"]:
            return False
        if d["cur"] is not None and d["prev"] is not None:
            return d["cur"] > d["prev"]
        return d["pct"] > 0 if d["pct"] is not None else None

    rev, op, net = improved("revenue"), improved("op"), improved("net")
    ups = [x for x in (rev, op, net) if x]
    if rev and op:
        direction = "up"
    elif ups:
        direction = "mixed"
    elif any(x is not None for x in (rev, op, net)):
        direction = "down"
    else:
        direction = None

    parts = []
    for key, name in (("revenue", "매출"), ("op", "영업익"), ("net", "순익")):
        d = out.get(key)
        if not d:
            continue
        parts.append(f"{name} {d['turn']}" if d["turn"] else f"{name} {pct_str(d['pct'])}")
    return {
        "metrics": out,
        "direction": direction,
        "signal": direction in ("up", "mixed"),
        "summary": " · ".join(parts),
    }


# ---------------------------------------------------------------- capex
def parse_capex(rows: Rows, subtype: str) -> dict:
    rows = strip_amendment(rows)
    if subtype == "신규시설투자":
        amount = pick_num(rows, r"투자금액", which="first")
        ratio = pick_num(rows, r"자기자본대비", which="first")
        base_label = "자기자본"
        m = {
            "kind": pick_text(rows, r"투자구분"),
            "purpose": pick_text(rows, r"투자목적"),
            "start": pick_text(rows, r"^시작일"),
            "end": pick_text(rows, r"^종료일"),
        }
    elif subtype in ("유형자산취득", "유형자산양수"):
        amount = pick_num(rows, r"취득가액|취득금액|양수금액", which="first")
        ratio = pick_num(rows, r"자산총액대비", which="first")
        base_label = "자산총액"
        m = {
            "name": pick_text(rows, r"취득물건명|자산명|취득물건구분|자산구분"),
            "purpose": pick_text(rows, r"취득목적|양수목적"),
            "counterparty": pick_text(rows, r"^3\.거래상대|회사명\(성명\)", limit=60),
        }
    else:  # 공급계약
        amount = pick_num(rows, r"계약금액", which="first")
        ratio = pick_num(rows, r"매출액대비", which="first")
        base_label = "매출액"
        m = {
            "name": pick_text(rows, r"체결계약명|판매ㆍ?공급계약내용|계약내용"),
            "counterparty": pick_text(rows, r"^3\.계약상대|계약상대방?$", limit=60),
            "start": pick_text(rows, r"^시작일"),
            "end": pick_text(rows, r"^종료일"),
        }
    m.update({"amount": amount, "ratio": ratio, "ratio_base": base_label})
    summary = f"{eok(amount)}"
    if ratio is not None:
        summary += f" ({base_label} 대비 {ratio:,.1f}%)"
    if m.get("counterparty"):
        summary += f" · {m['counterparty']}"
    return {"metrics": m, "direction": "up", "signal": True, "summary": summary}


# ---------------------------------------------------------------- stake
REASON = re.compile(r"\(\s*[+-]\s*\)\s*$")
BUY_WORDS = re.compile(r"매수|매입|취득|양수|인수|배정|청약")


def _reasons(rows: Rows) -> tuple[list[str], float, float]:
    """'장내매수(+)' 형태의 변동 사유와 매수 수량·금액 합계."""
    reasons: list[str] = []
    buy_shares = 0.0
    buy_amount = 0.0
    for cells in rows:
        for i, c in enumerate(cells):
            if REASON.search(c) and len(c) < 30:
                if c not in reasons:
                    reasons.append(c)
                if "+" in c and BUY_WORDS.search(c):
                    # '-'는 0으로 보고 자리를 유지, 날짜·주식종류 같은 텍스트 셀은 제외
                    nums = [0.0 if x.strip() == "-" else num(x) for x in cells[i + 1:]]
                    nums = [v for v in nums if v is not None]
                    # [변동전, 증감, 변동후, 단가] 순서가 일반적
                    if len(nums) >= 3:
                        buy_shares += abs(nums[1])
                        if len(nums) >= 4 and nums[3] > 0:
                            buy_amount += abs(nums[1]) * nums[3]
                break
    return reasons, buy_shares, buy_amount


def _delta_row(rows: Rows) -> tuple[float | None, float | None]:
    for cells in rows:
        if cells and _norm(cells[0]) == "증감":
            rest = cells[1:]
            while rest and num(rest[0]) is None and rest[0].strip() != "-":
                rest = rest[1:]  # '보통주식' 같은 구분 셀 건너뜀
            vals = [0.0 if c.strip() == "-" else num(c) for c in rest]
            if len(vals) >= 2:
                return vals[0], vals[1]
    return None, None


def parse_stake(rows: Rows, subtype: str, title: str) -> dict:
    m: dict = {}
    if subtype in ("자사주취득", "자사주신탁"):
        if subtype == "자사주취득":
            m["amount"] = pick_num(rows, r"취득예정금액", which="first")
            m["shares"] = pick_num(rows, r"취득예정주식", which="first")
            m["purpose"] = pick_text(rows, r"취득목적", which="first")
            m["method"] = pick_text(rows, r"취득방법", which="first")
        else:
            m["amount"] = pick_num(rows, r"^1\.계약금액|^계약금액", which="first")
            m["purpose"] = pick_text(rows, r"계약목적", which="first")
        summary = f"자사주 {eok(m.get('amount'))}"
        if m.get("purpose"):
            summary += f" · {m['purpose'][:30]}"
        return {"metrics": m, "direction": "buy", "signal": True, "summary": summary}

    reasons, buy_shares, buy_amount = _reasons(rows)
    m["reasons"] = reasons[:6]
    if buy_amount:
        m["buy_amount"] = buy_amount

    if subtype == "대량보유(5%)":
        prev = cur = None
        for cells in rows:
            h = _norm(cells[0]) if cells else ""
            if h == "직전보고서" and prev is None:
                vals = row_nums(cells[1:])
                prev = vals[1] if len(vals) > 1 else None
                prev = prev if prev is not None else 0.0
            elif h == "이번보고서" and cur is None:
                vals = row_nums(cells[1:])
                cur = vals[1] if len(vals) > 1 else None
        m["ratio_prev"], m["ratio_cur"] = prev, cur
        delta = (cur - (prev or 0)) if cur is not None else None
        m["ratio_delta"] = delta
        m["relation"] = pick_text(rows, r"발행회사와의관계", which="first")
        m["reason_text"] = pick_text(rows, r"^보고사유$", which="first")
        m["simple"] = "약식" in title
        shares_delta = None
    else:
        shares_delta, delta = _delta_row(rows)
        m["shares_delta"] = shares_delta
        m["ratio_delta"] = delta
        if subtype == "임원·주요주주":
            m["relation"] = pick_text(rows, r"직위명", which="first") or pick_text(
                rows, r"^주요주주$", which="first")

    plus = any("(+)" in r.replace(" ", "") for r in reasons)
    minus = any("(-)" in r.replace(" ", "") for r in reasons)
    change = shares_delta if shares_delta is not None else delta
    if plus and not minus:
        direction = "buy"
    elif minus and not plus:
        direction = "sell"
    elif change is None:
        direction = "buy" if buy_shares else None
    elif change > 0:
        direction = "buy"
    elif change < 0:
        direction = "sell"
    else:
        direction = "flat"
    bought = any(BUY_WORDS.search(r) and "+" in r for r in reasons)
    signal = direction == "buy" and (bought or not reasons)

    parts = []
    if reasons:
        parts.append(", ".join(r.replace(" ", "") for r in reasons[:2]))
    if shares_delta is not None:
        parts.append(f"{shares_delta:+,.0f}주")
    if delta is not None:
        parts.append(f"{delta:+.2f}%p")
    if subtype == "대량보유(5%)" and m.get("ratio_cur") is not None:
        parts.append(f"→ {m['ratio_cur']:.2f}%")
    if buy_amount:
        parts.append(f"매수 약 {eok(buy_amount)}")
    return {"metrics": m, "direction": direction, "signal": signal, "summary": " · ".join(parts)}


# ---------------------------------------------------------------- entry
def parse(session: requests.Session, item: dict) -> dict:
    rows = fetch_rows(session, item["id"])
    cat, sub = item["category"], item["subtype"]
    if cat == EARNINGS:
        return parse_earnings(rows)
    if cat == CAPEX:
        return parse_capex(rows, sub)
    if cat == STAKE:
        return parse_stake(rows, sub, item["title"])
    return {}
