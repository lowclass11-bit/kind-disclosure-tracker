# KIND 공시 트래커

코스피·코스닥 상장사의 **실적 개선 · 시설투자 · 대주주 지분 매입** 공시를 매 영업일 장 마감 후 자동으로 수집해서
GitHub Pages 대시보드로 보여줍니다.

## 추적 대상

| 카테고리 | 공시 | 시그널(상승·매수) 판정 |
|---|---|---|
| 실적 변화 | 매출액또는손익구조30%(대규모법인 15%)이상변경 | 매출·영업이익·순이익 중 개선이 있으면 시그널. 매출과 영업이익이 모두 늘면 ▲상승, 일부만 늘면 ◆혼조 |
| 시설투자·공급계약 | 신규시설투자등, 유형자산취득결정, 유형자산양수결정, 단일판매ㆍ공급계약체결 | 전부 시그널. 금액과 자기자본·자산·매출 대비 비율을 추출 |
| 지분 매입 | 최대주주등소유주식변동신고서, 임원ㆍ주요주주특정증권등소유상황보고서, 주식등의대량보유상황보고서, 자기주식취득결정, 자기주식취득신탁계약체결결정 | 지분이 늘었고 사유가 매수·취득일 때 ▲매수. 증여 받음·신규선임 등은 제외. 자사주 취득은 전부 시그널 |

- 해지·철회·결과보고서·양도·처분 공시는 제외합니다.
- 정정공시는 `[기재정정]` 배지로 표시하며, 화면에서 숨길 수 있습니다.
- 자회사·종속회사 공시는 `자회사` 배지로 표시합니다.

## 동작 방식

```
GitHub Actions (평일 18:50 KST)
  └─ scraper/collect.py
       1) 공시 목록: KIND 오늘의 공시 → 실패(해외 IP 차단 등) 시 DART 최근공시로 대체
       2) 제목 기준으로 분류 (scraper/classify.py)
       3) DART 뷰어에서 본문 표를 파싱해 수치 추출 (scraper/details.py)
       4) docs/data/days/YYYY-MM-DD.json, docs/data/all.json, docs/data/index.json 에 저장 후 커밋
GitHub Pages (docs/)
  └─ index.html + app.js : 오늘의 공시 / 중복 시그널 / 히스토리 검색 / 관심종목
```

KIND 접수번호와 DART 접수번호는 거래소 공시에서 같은 값이라, 어느 소스에서 수집해도 같은 id로 저장되고
카드마다 KIND 원문과 DART 원문 링크를 둘 다 제공합니다.

> ⚠️ KIND(kind.krx.co.kr)는 해외 IP를 차단하는 경우가 많습니다. GitHub Actions 러너가 해외에 있어서
> 실제로는 대부분 DART로 대체 수집됩니다. 공시 내용은 같습니다.

## 알림

- 매일 18:50 KST에 수집이 시작되고, GitHub 스케줄 지연까지 감안하면 **19시 전후**에 데이터가 반영됩니다.
- 대시보드를 열면 마지막 확인 이후 새로 들어온 공시 건수가 배너로 표시됩니다(관심종목은 따로 표시).
- **🔔 알림 켜기**를 누르면 탭이 열려 있는 동안 10분마다 새 데이터를 확인하고, 새 데이터가 있으면 브라우저 알림을 보냅니다.
- **텔레그램**: 수집이 끝나면 상승·매수 시그널 공시 요약을 텔레그램으로 보냅니다. 이미 보낸 공시는 다시 보내지 않고,
  직전 거래일 장 마감 후에 늦게 올라온 공시도 포함합니다. 휴장일에는 보내지 않습니다.

### 텔레그램 설정

1. 텔레그램에서 **@BotFather** → `/newbot` → 봇 이름을 정하고 **토큰**을 받습니다.
2. 만든 봇과 대화를 열어 아무 메시지나 보낸 뒤, 브라우저에서
   `https://api.telegram.org/bot<토큰>/getUpdates` 를 열어 `"chat":{"id": ...}` 숫자를 확인합니다. 이게 **Chat ID**입니다.
3. 레포 **Settings → Secrets and variables → Actions**
   - **Secrets** 탭: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 추가
   - (선택) **Variables** 탭: `WATCHLIST` = `삼성전자,현대건설` 처럼 쉼표로 구분 → 메시지 맨 위에 ⭐ 관심종목으로 따로 표시
4. **Actions → daily-collect → Run workflow**에서 `텔레그램 연결 테스트`를 체크하고 실행하면 테스트 메시지가 옵니다.

## 설치 (최초 1회)

1. 이 레포를 **Public**으로 둡니다(무료 계정의 GitHub Pages 조건).
2. **Settings → Pages → Build and deployment**에서 Source는 `Deploy from a branch`, Branch는 `main` / `/docs`로 지정합니다.
3. **Settings → Actions → General → Workflow permissions**에서 `Read and write permissions`를 선택합니다.
4. **Actions → daily-collect → Run workflow**를 실행합니다. 처음에는 `backfill`에 `20`을 넣으면 최근 20일치를 채웁니다.
5. `https://<계정명>.github.io/kind-disclosure-tracker/` 로 접속합니다.

## 로컬 실행

```bash
pip install -r requirements.txt
python -m scraper.collect                  # 오늘 + 직전 수집일 재확인
python -m scraper.collect --date 2026-09-22
python -m scraper.collect --backfill 10    # 최근 10일 (주말 제외)
python -m http.server -d docs 8000         # http://localhost:8000
pip install pytest && python -m pytest -q  # 파서 단위 테스트
```

## 한계

- 제목과 본문 표 구조를 기준으로 파싱합니다. 거래소 양식이 바뀌면 `scraper/details.py`를 고쳐야 합니다.
  파싱에 실패한 건은 "본문 파싱 실패 - 원문 확인"으로 표시되고, 원문 링크는 그대로 남습니다.
- 관심종목은 브라우저 localStorage에 저장되므로 기기마다 따로 관리됩니다.
