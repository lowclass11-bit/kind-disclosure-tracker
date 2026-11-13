"""작업실: 사실 카드 · 시나리오 · 반론 검사 · 투자 메모 · 콘텐츠 대본.

기본은 규칙 기반(수집한 근거를 틀에 맞춰 정리). Claude API 키가 있으면 같은 근거만으로 서술형 해석을 쓴다.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from . import config
from .pipeline import PILLARS

KINDS = {"facts": "사실 카드", "scenario": "시나리오", "bear": "반론 검사", "memo": "투자 메모",
         "script": "콘텐츠 대본", "briefing": "주간 섹터 브리핑"}
DISCLAIMER = ("> 이 문서는 공개 자료를 기계적으로 모아 정리한 조사 메모이며 매수·매도 권유가 아닙니다. "
              "점수는 '더 조사해 볼 만한 순서'일 뿐 수익을 예측하지 않습니다. 투자 판단과 책임은 본인에게 있습니다.")


def _pct(x, sign=True) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:+.1f}%" if sign else f"{x * 100:.1f}%"


def _title(item: dict) -> str:
    if item.get("kind") == "sector":
        return f"{item['name']} ({'한국' if item['market'] == 'KR' else '미국'} 업종)"
    return f"{item['name']} ({item['ticker']} · {item['sector']})"


def _ev_line(e: dict) -> str:
    tail = f" — {e['src']}" + (f", {e['d']}" if e.get("d") else "")
    link = f" [원문]({e['url']})" if e.get("url") else ""
    return f"- {e['t']}{tail}{link}"


def _by(item: dict, pillar: str, positive: bool = True) -> list[dict]:
    return [e for e in item.get("ev", []) if e["p"] == pillar and (e["pts"] >= 0) == positive]


def _score_table(item: dict) -> str:
    p = item["pillars"]
    rows = "\n".join(f"| {PILLARS[k]} | {p[k]:.0f} |" for k in PILLARS)
    return f"| 축 | 점수(0~100) |\n|---|---|\n{rows}\n| **종합** | **{item['score']:.0f}** |"


# ---------------------------------------------------------------- 규칙 기반
def facts(item: dict, data: dict) -> str:
    out = [f"# 사실 카드 — {_title(item)}", f"기준일 {data['date']} · 종합 {item['score']:.0f}점", ""]
    for k, label in PILLARS.items():
        evs = [e for e in item.get("ev", []) if e["p"] == k]
        out.append(f"## {label} ({item['pillars'][k]:.0f}점)")
        out += [_ev_line(e) for e in evs] or ["- 수집된 근거 없음"]
        out.append("")
    if item.get("kind") == "sector" and item.get("leaders"):
        out.append("## 업종 안에서 점수가 높은 후보")
        out += [f"- {m['name']} ({m['score']:.0f}점)" for m in item["leaders"]]
        out.append("")
    return "\n".join(out)


def _checkpoints(item: dict, data: dict) -> list[str]:
    today = dt.date.fromisoformat(data["date"])
    rows = []
    if item.get("next_earnings"):
        rows.append(f"{item['next_earnings']} — 실적 발표: 추정치 방향이 유지되는지, 회사 전망(가이던스)")
    nxt = next((d for d in (dt.date(today.year, 2, 14), dt.date(today.year, 5, 15), dt.date(today.year, 8, 14),
                            dt.date(today.year, 11, 14), dt.date(today.year + 1, 2, 14)) if d > today))
    if item["market"] == "US":
        rows.append(f"{nxt.isoformat()} 전후 — 다음 13F 공개: 기관이 더 샀는지, 팔았는지")
        rows.append("수시 — SEC Form 4: 내부자 매수가 이어지는지, 매도로 바뀌는지")
    else:
        first = (today.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
        rows.append(f"{first.isoformat()} — 월간 수출입 통계(매월 1일), 11일·21일 잠정치")
        rows.append("매 분기 — 잠정실적 공시와 증권사 추정치 변화 (이 프로그램을 다시 돌리면 추정 EPS 변화가 기록됨)")
        rows.append("수시 — 임원·대주주 지분 공시, 공급계약·시설투자 공시 (KIND 공시 트래커)")
    rows.append(f"{(today + dt.timedelta(days=182)).isoformat()} — 6개월 재평가일: 아래 무효화 조건과 실제 결과 대조")
    return rows


def _invalidation(item: dict) -> list[str]:
    rows = []
    if item.get("lo52"):
        cur = "원" if item["market"] == "KR" else "달러"
        rows.append(f"주가가 52주 저점({item['lo52']:,.0f}{cur})을 거래량과 함께 이탈")
    if _by(item, "rev"):
        rows.append("실적 추정치가 두 번 연속 하향되거나, 다음 분기 실적이 추정치를 크게 밑돎")
    if _by(item, "smart"):
        rows.append("매수하던 주체(내부자·외국인·기관)가 순매도로 전환")
    if _by(item, "cat"):
        rows.append("기대하던 촉매(계약·증설·정책·제품)가 연기되거나 취소")
    rows.append("6개월이 지나도 시장 대비 수익률이 개선되지 않으면 가설을 접는다")
    return rows


def scenario(item: dict, data: dict) -> str:
    quiet, smart, rev, cat = (_by(item, k) for k in PILLARS)
    out = [f"# 시나리오 — {_title(item)}", ""]
    out.append("## 왜 지금 잠잠한가")
    out += [f"- {e['t']}" for e in quiet] or ["- 가격 자료 없음"]
    out += ["", "## 6개월 안에 달라질 수 있는 것"]
    if rev:
        out.append("**실적 기대가 먼저 움직이는 중** — 주가는 추정치 변화를 뒤늦게 따라가는 경우가 많다.")
        out += [f"- {e['t']}" for e in rev[:4]]
    if smart:
        out.append("**정보가 많은 쪽이 사고 있다** — 가격이 조용할 때의 매집은 선행 신호로 본다.")
        out += [f"- {e['t']}" for e in smart[:4]]
    if cat:
        out.append("**예정된 촉매가 있다**")
        out += [f"- {e['t']}" for e in cat[:4]]
    if not (rev or smart or cat):
        out.append("- 소외 외에는 확인된 근거가 없다. 지금은 '싸다'는 사실뿐이다.")
    out += ["", "## 세 가지 경로", ""]
    out.append("- **잘 풀리는 경우**: 위 근거가 다음 분기 실적으로 확인되고, 시장의 관심이 돌아오며 저평가가 해소된다."
               + (" 내부자·기관 매수가 이어진다." if smart else ""))
    out.append("- **기본 경우**: 근거의 일부만 확인된다. 주가는 바닥을 다지지만 시장 평균 수준에 그친다.")
    out.append("- **안 풀리는 경우**: 추정치가 하향 전환하거나 촉매가 밀린다. 싼 주식이 더 싸진다(가치 함정).")
    out += ["", "## 확인할 것 (날짜순)"]
    out += [f"- {c}" for c in _checkpoints(item, data)]
    return "\n".join(out)


def bear(item: dict, data: dict) -> str:
    p = item["pillars"]
    flags = []
    neg = [e for e in item.get("ev", []) if e["pts"] < 0]
    for e in neg:
        flags.append(f"**부정 신호가 이미 있다** — {e['t']}")
    if p["rev"] < 25:
        flags.append("**실적 근거가 약하다** — 추정치 상향이 확인되지 않았다. 싸기만 하고 계속 싼 '가치 함정'일 수 있다.")
    if p["smart"] < 15:
        flags.append("**아무도 사고 있지 않다** — 내부자·외국인·기관 매수가 확인되지 않았다. 나만 아는 호재는 대개 호재가 아니다.")
    if item.get("below_200") and (item.get("ret_1m") or 0) < 0:
        flags.append(f"**아직 떨어지는 중이다** — 200일선 아래, 최근 1개월 {_pct(item.get('ret_1m'))}. 바닥 확인 전 진입은 '떨어지는 칼날'.")
    if item.get("confirm", 3) <= 1 and item.get("kind") != "sector":
        flags.append("**근거가 한 축에만 기댄다** — 서로 다른 종류의 신호가 겹칠 때 신뢰도가 높다.")
    if (item.get("news_count") or 0) >= 60:
        flags.append(f"**생각만큼 잠잠하지 않다** — 최근 30일 관련 기사 {item['news_count']}건. 이미 알려진 이야기일 수 있다.")
    if item.get("next_earnings"):
        gap = (dt.date.fromisoformat(item["next_earnings"]) - dt.date.fromisoformat(data["date"])).days
        if gap <= 14:
            flags.append(f"**실적 발표가 {gap}일 앞이다** — 결과에 따라 가설이 한 번에 깨질 수 있다.")
    if item["market"] == "KR" and any("추정 EPS" in e["t"] and "대비" in e["t"] for e in _by(item, "rev")):
        flags.append("**'추정 EPS가 높다'는 것과 '추정치가 올라가고 있다'는 다르다** — 한국 종목은 이 프로그램이 "
                     "추정치를 여러 번 기록한 뒤에야 방향(상향·하향)을 알 수 있다.")
    if item.get("kind") != "sector" and item["market"] == "KR" and 0 < item.get("mcap", 0) < 5000:
        flags.append(f"**시가총액 {item['mcap']:,.0f}억원** — 거래가 얇아 수급 신호가 과장되기 쉽다.")
    for e in _by(item, "smart"):
        if "매수 공시" in e["t"] and "합계" in e["t"]:
            flags.append("**대주주 매수의 성격을 확인할 것** — 장내 매수인지, 계열사 간 이전·블록딜·상속 관련인지 공시 원문에서 확인.")
            break
    macro_tags = data.get("macro", {}).get("labels", [])
    if not any("거시 환경" in e["t"] for e in item.get("ev", [])):
        flags.append("**거시 환경의 뒷받침이 없다** — 현재 활성 신호: " + (", ".join(macro_tags) or "없음") + ".")
    out = [f"# 반론 검사 — {_title(item)}", "",
           "가설을 세우기 전에 반대편 주장부터 적는다. 아래는 수집된 자료에서 자동으로 걸린 항목이다.", "",
           "## 걸린 항목"]
    out += [f"{i}. {f}" for i, f in enumerate(flags, 1)] or ["- 자동 검사에서 걸린 항목 없음 (그래도 아래 질문에 답해 볼 것)"]
    out += ["", "## 스스로 답해야 할 질문",
            "- 이 종목이 싼 이유를 시장은 뭐라고 설명하는가? 그 설명이 6개월 안에 틀릴 근거가 있는가?",
            "- 같은 신호(내부자 매수, 추정치 상향)가 과거 이 회사에서 나왔을 때 주가는 어땠는가?",
            "- 내가 틀렸다는 걸 어떤 숫자로, 언제 알 수 있는가?",
            "", "## 무효화 조건 (이 중 하나면 가설 폐기)"]
    out += [f"- {r}" for r in _invalidation(item)]
    return "\n".join(out)


def memo(item: dict, data: dict) -> str:
    strong = [PILLARS[k] for k in ("smart", "rev", "cat") if item["pillars"][k] >= 25]
    one = (f"시장 대비 6개월 {_pct(item.get('rel_6m'))}로 소외된 상태에서 "
           f"{'·'.join(strong) if strong else '뚜렷한 추가 근거 없이'} "
           f"{'신호가 겹친다' if len(strong) >= 2 else '신호가 있다' if strong else '싸기만 하다'}.")
    out = [f"# 투자 메모 — {_title(item)}", f"작성 {data['date']} · 스타일 {data['style_label']} · 6개월 관점", "",
           "## 한 줄 요약", one, "", "## 점수", _score_table(item), ""]
    if item.get("kind") != "sector":
        cur = "원" if item["market"] == "KR" else "달러"
        vals = [f"현재가 {item['price']:,.0f}{cur}" if item["market"] == "KR" else f"현재가 ${item['price']:,.2f}",
                f"52주 범위의 {item['pos52'] * 100:.0f}% 위치"]
        if item.get("mcap"):
            vals.append(f"시가총액 {item['mcap']:,.0f}{'억원' if item['market'] == 'KR' else '억달러'}")
        for k, label in (("per", "PER"), ("fwd_pe", "예상 PER"), ("pbr", "PBR")):
            if item.get(k):
                vals.append(f"{label} {item[k]:.1f}배")
        out += ["## 기본 정보", " · ".join(vals), ""]
    body = scenario(item, data).split("\n", 2)[2]
    out += [body, ""]
    bear_body = bear(item, data)
    out += ["## 반론", bear_body[bear_body.index("## 걸린 항목") + len("## 걸린 항목"):].replace("\n## ", "\n### ").strip(), ""]
    out += ["## 근거 출처"]
    out += [_ev_line(e) for e in item.get("ev", []) if e.get("url")][:14]
    out += ["", DISCLAIMER]
    return "\n".join(out)


def script(item: dict, data: dict) -> str:
    quiet = _by(item, "quiet")
    proof = (_by(item, "smart") + _by(item, "rev") + _by(item, "cat"))[:3]
    name = item["name"]
    out = [f"# 콘텐츠 대본 — {_title(item)}", "", "## 60초 쇼츠 대본", "",
           f"**[0~5초 · 훅]** 요즘 아무도 얘기 안 하는 {'업종' if item.get('kind') == 'sector' else '종목'}이 하나 있습니다. {name}.",
           f"**[5~15초 · 왜 조용한가]** {quiet[0]['t'] if quiet else '주가는 한동안 시장을 따라가지 못했습니다'}."]
    for i, e in enumerate(proof):
        out.append(f"**[{15 + i * 12}~{27 + i * 12}초 · 근거 {i + 1}]** {e['t']}. (화면: {e['src']})")
    out += [f"**[{15 + len(proof) * 12}~55초 · 반론]** 다만 싼 데는 이유가 있습니다. "
            "추정치가 꺾이거나 매수 주체가 돌아서면 이 이야기는 끝입니다.",
            "**[55~60초 · 마무리]** 6개월 뒤에 결과를 다시 확인해 보겠습니다. 투자 권유가 아닌 조사 기록입니다.", "",
            "## 블로그 글 개요", "",
            f"1. 제목 후보: 「지금은 조용한 {name}, 6개월 뒤를 보는 세 가지 이유」",
            "2. 도입: 왜 '잠잠한 곳'을 보는가 — 주가는 실적 기대와 수급을 뒤늦게 따라간다",
            "3. 현재 상태: " + (quiet[0]["t"] if quiet else ""),
            "4. 근거: " + " / ".join(e["t"] for e in proof),
            "5. 반론과 무효화 조건: " + " / ".join(_invalidation(item)[:3]),
            "6. 확인 일정: " + " / ".join(_checkpoints(item, data)[:3]),
            "7. 맺음: 조사 기록이며 권유가 아니라는 점, 6개월 뒤 결과 대조 예고", "",
            "## 화면에 넣을 출처"]
    out += [_ev_line(e) for e in item.get("ev", []) if e.get("url")][:6]
    out += ["", DISCLAIMER]
    return "\n".join(out)


RULES = {"facts": facts, "scenario": scenario, "bear": bear, "memo": memo, "script": script}


# ---------------------------------------------------------------- Claude
AI_SYSTEM = """당신은 한국의 주식 리서치 보조원이다. 사용자가 수집한 자료만으로 '지금은 잠잠하지만 6개월 안에 재평가될 수 있는 업종·종목'에 대한 조사 문서를 쓴다.

지켜야 할 것:
- 제공된 자료(JSON과 초안)에 있는 사실과 숫자만 쓴다. 자료에 없는 수치, 일정, 제품명, 과거 사례를 지어내지 않는다. 일반적인 업계 상식을 쓸 때는 '일반적으로'라고 밝힌다.
- 근거가 약하면 약하다고 쓴다. 독자는 이 문서로 돈을 움직일 수 있으므로, 부풀린 확신은 실제 손실로 이어진다.
- 매수·매도를 권하지 않는다. 가설, 근거, 반론, 확인할 지표, 무효화 조건을 구분해서 쓴다.
- 한국어 마크다운으로 쓴다. 제목은 #, 소제목은 ##. 출처는 자료의 링크를 그대로 단다.
- 문서 끝에 '투자 권유가 아닌 조사 메모'라는 한 줄을 넣는다."""

AI_TASK = {
    "facts": "근거를 축별로 정리한 사실 카드. 각 근거가 왜 6개월 선행 신호가 될 수 있는지 한 줄씩 덧붙인다.",
    "scenario": "왜 지금 잠잠한지, 6개월 안에 무엇이 달라질 수 있는지, 잘 풀리는/기본/안 풀리는 세 경로와 각 경로에서 보일 신호, 날짜순 확인 목록.",
    "bear": "가장 강한 반대 논리. 이 근거들이 틀렸거나 이미 주가에 반영됐을 이유, 가치 함정일 가능성, 자료의 한계, 무효화 조건.",
    "memo": "한 장짜리 투자 메모: 한 줄 요약, 가설, 근거(축별), 반론, 확인할 지표와 일정, 무효화 조건, 출처.",
    "script": "유튜브 쇼츠 60초 대본(초 단위 구간 표시)과 블로그 글 개요. 과장 없이, 반론과 '권유 아님'을 포함.",
    "briefing": "주간 섹터 브리핑: 이번 주 달라진 점 요약, 점수가 오른 업종과 이유, 새 후보, 추적표 점검, 다음 주에 볼 것.",
}


def ai_write(kind: str, draft: str, payload: dict, note: str, cfg: dict) -> str:
    """Claude에게 같은 자료로 서술형 문서를 쓰게 한다. 키가 없거나 SDK가 없으면 예외."""
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("anthropic 패키지가 없습니다. `pip install anthropic` 후 다시 시도하세요.") from exc
    client = anthropic.Anthropic(api_key=cfg["anthropic_key"])
    user = (f"문서 종류: {KINDS[kind]}\n요청: {AI_TASK[kind]}\n"
            + (f"사용자의 추가 지시: {note}\n" if note else "")
            + f"\n<수집 자료>\n{json.dumps(payload, ensure_ascii=False)}\n</수집 자료>\n"
            + f"\n<규칙 기반 초안>\n{draft}\n</규칙 기반 초안>")
    try:
        with client.beta.messages.stream(
            model=cfg.get("ai_model") or "claude-opus-5-5",
            max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",   # 안전 분류기가 거절하면 서버가 다른 모델로 한 번 더 시도
            system=AI_SYSTEM,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise RuntimeError("Claude API 키가 올바르지 않습니다. 설정에서 다시 입력하세요.") from exc
    except anthropic.RateLimitError as exc:
        raise RuntimeError("Claude API 사용량 한도에 걸렸습니다. 잠시 뒤 다시 시도하세요.") from exc
    except anthropic.APIStatusError as exc:
        raise RuntimeError(f"Claude API 오류 (HTTP {exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise RuntimeError("Claude API에 연결하지 못했습니다. 인터넷 연결을 확인하세요.") from exc
    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude가 이 요청에 답하지 않았습니다. 규칙 기반 문서를 사용하세요.")
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    if not text:
        raise RuntimeError("Claude 응답이 비어 있습니다.")
    if msg.stop_reason == "max_tokens":
        text += "\n\n> (응답이 길이 제한에서 잘렸습니다.)"
    return text


def ai_payload(item: dict, data: dict) -> dict:
    keep = ("name", "ticker", "sector", "market", "exchange", "kind", "mcap", "price", "score", "pillars",
            "ret_1m", "ret_6m", "rel_6m", "pos52", "from_hi", "lo52", "below_200", "per", "pbr", "fwd_pe",
            "next_earnings", "news_count", "leaders", "n", "breadth", "ev")
    return {"기준일": data["date"], "투자스타일": data["style_label"], "점수축": PILLARS,
            "거시": data.get("macro", {}), "대상": {k: item[k] for k in keep if k in item}}


# ---------------------------------------------------------------- 저장·내보내기
def save(kind: str, name: str, text: str) -> str:
    safe = re.sub(r"[^\w가-힣\-]+", "_", name).strip("_")[:40] or "item"
    fname = f"{dt.date.today():%Y%m%d}_{kind}_{safe}.md"   # 같은 날 같은 문서는 덮어쓴다
    (config.OUTPUTS / fname).write_text(text, encoding="utf-8")
    return fname


def to_docx(md: str) -> bytes:
    import io

    from docx import Document

    doc = Document()
    link = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

    def add(par, text):
        text = link.sub(lambda m: f"{m.group(1)} ({m.group(2)})", text)
        for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
            if part:
                par.add_run(part).bold = bool(i % 2)

    for line in md.splitlines():
        s = line.rstrip()
        if not s or re.fullmatch(r"\|[-| :]+\|", s):
            continue
        if s.startswith("#"):
            level = len(s) - len(s.lstrip("#"))
            doc.add_heading(s.lstrip("# "), level=min(level, 3))
        elif s.startswith(("- ", "* ")):
            add(doc.add_paragraph(style="List Bullet"), s[2:])
        elif re.match(r"\d+\. ", s):
            add(doc.add_paragraph(style="List Number"), s.split(". ", 1)[1])
        elif s.startswith("|"):
            add(doc.add_paragraph(), " · ".join(c.strip() for c in s.strip("|").split("|")))
        else:
            add(doc.add_paragraph(), s.lstrip("> "))
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
