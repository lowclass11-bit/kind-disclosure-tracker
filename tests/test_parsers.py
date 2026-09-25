"""네트워크 없이 분류·파싱 규칙을 검증 (실제 공시 본문에서 발췌한 표 행)."""
from scraper.classify import classify
from scraper.details import parse_capex, parse_earnings, parse_stake


def test_classify():
    assert classify("매출액또는손익구조30%(대규모법인은15%)이상변경")["category"] == "earnings"
    c = classify("[기재정정]단일판매ㆍ공급계약체결(자율공시)")
    assert c["category"] == "capex" and c["amended"] and c["amend_tag"] == "기재정정"
    assert classify("유형자산취득결정(종속회사의주요경영사항)")["subsidiary"]
    assert classify("주요사항보고서(자기주식취득결정)")["subtype"] == "자사주취득"
    assert classify("주요사항보고서(자기주식취득신탁계약해지결정)") is None
    assert classify("자기주식취득결과보고서") is None
    assert classify("주요사항보고서(유형자산양도결정)") is None
    assert classify("임원ㆍ주요주주특정증권등거래계획보고서") is None
    assert classify("주주총회소집공고") is None


def test_earnings_up():
    rows = [
        ["1. 재무제표의 종류", "개별"],
        ["3. 매출액 또는 손익구조 변동내용(단위:천원)", "당해사업연도", "직전사업연도", "증감금액", "증감비율(%)", "흑자적자전환여부"],
        ["- 매출액", "312,988,435", "9,039,522", "303,948,913", "3,362.4", "-"],
        ["- 영업이익", "269,836,904", "6,653,160", "263,183,744", "3,955.8", "-"],
        ["- 당기순이익", "-1,000", "-5,000", "4,000", "-", "-"],
    ]
    r = parse_earnings(rows)
    assert r["direction"] == "up" and r["signal"]
    assert r["metrics"]["unit"] == "천원"
    assert r["metrics"]["fs_type"] == "개별"


def test_earnings_turnaround_and_down():
    rows = [
        ["- 매출액", "900", "1,000", "-100", "-10.0", "-"],
        ["- 영업이익", "50", "-20", "70", "-", "흑자전환"],
    ]
    r = parse_earnings(rows)
    assert r["direction"] == "mixed" and "흑자전환" in r["summary"]
    rows = [["- 매출액", "900", "1,000", "-100", "-10.0", "-"], ["- 영업이익", "5", "20", "-15", "-75.0", "-"]]
    assert parse_earnings(rows)["signal"] is False


def test_capex_amended_uses_body():
    rows = [
        ["정정항목", "정정전", "정정후"],
        ["5. 계약기간 종료일", "2026-09-30", "2026-11-06"],
        ["1. 판매ㆍ공급계약 구분", "기타 판매ㆍ공급계약"],
        ["2. 계약내역", "계약금액(원)", "3,957,000,000"],
        ["최근매출액(원)", "50,777,866,585"],
        ["매출액대비(%)", "7.79"],
        ["3. 계약상대", "SK하이닉스"],
        ["5. 계약기간", "시작일", "2026-07-30"],
        ["종료일", "2026-11-06"],
    ]
    r = parse_capex(rows, "공급계약")
    m = r["metrics"]
    assert m["amount"] == 3_957_000_000 and m["ratio"] == 7.79
    assert m["counterparty"] == "SK하이닉스" and m["end"] == "2026-11-06"


def test_insider_buy():
    rows = [
        ["증 감", "200", "0.00", "200", "0.00"],
        ["장내매수(+)", "2026.09.21", "보통주", "-", "200", "200", "255,000", "-", "-"],
    ]
    r = parse_stake(rows, "임원·주요주주", "임원ㆍ주요주주특정증권등소유상황보고서")
    assert r["direction"] == "buy" and r["signal"]
    assert r["metrics"]["buy_amount"] == 200 * 255_000


def test_major_holder_sell_and_gift():
    rows = [["증감", "보통주식", "-3,000", "-0.01"], ["증여(-)", "보통주식", "10,000", "-3,000", "7,000"]]
    r = parse_stake(rows, "최대주주변동", "최대주주등소유주식변동신고서")
    assert r["direction"] == "sell" and not r["signal"]


def test_block_holding():
    rows = [
        ["직전 보고서", "3,095,062", "66.74"],
        ["이번 보고서", "3,099,257", "66.83"],
        ["민선영", "611113", "2026.03.04", "장내매수(+)", "의결권있는 주식", "15,887", "900", "16,787", "88,722"],
    ]
    r = parse_stake(rows, "대량보유(5%)", "주식등의대량보유상황보고서(일반)")
    assert r["direction"] == "buy" and r["signal"]
    assert abs(r["metrics"]["ratio_delta"] - 0.09) < 1e-9
