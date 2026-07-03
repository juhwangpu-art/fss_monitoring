# FSS 보도자료 → Notion 동기화

금융감독원 보도자료 게시판(`B0000188`)을 크롤링해서 Notion `FSS 보도자료 DB`에 신규 글을 push하고, 기존 글의 변경(조회수 등)을 patch한다.
로컬 Streamlit 대시보드는 [`../fss_monitor_local/`](../fss_monitor_local/)로 분리했다.

- 저장소: [github.com/juhwangpu-art/fss_monitoring](https://github.com/juhwangpu-art/fss_monitoring)
- 참고 저장소: [juhwangpu-art/crawler_news](https://github.com/juhwangpu-art/crawler_news)
- Notion 대상: `Crawler_FSS > FSS 보도자료 DB`

## 실행 방식
- **GitHub Actions cron** — 매 4시간마다 자동 실행 (KST 07:30 / 11:30 / 15:30 / 19:30 / 23:30 / 03:30) — [`.github/workflows/crawl.yml`](.github/workflows/crawl.yml)
- **수동 실행** — Actions 탭에서 `Run workflow` (workflow_dispatch)
- **로컬 테스트** — 환경변수 세팅 후 `python run_headless.py`

## 파일 구성
| 파일 | 역할 |
|---|---|
| `config.py` | 게시판 URL, HTTP 헤더, Notion property 매핑, NEW 배지 기간 |
| `crawler.py` | 목록 페이지 파싱 (`requests` + `BeautifulSoup`) |
| `sync_notion.py` | Notion API wrapper — 스냅샷 로드, diff, create/update |
| `run_headless.py` | 진입점 — 3-way 분기 (신규 create / 변경 update / 오래된 신규 해제) |
| `.github/workflows/crawl.yml` | Actions 워크플로 (4h cron + workflow_dispatch) |

## 동작 요약

매 실행마다:

1. **스냅샷 로드** — `fetch_existing_pages` 가 Notion DB 전 페이지의 nttId/title/조회수/등록일/신규/최초수집 을 in-memory로 읽음
   - **nttId backfill** — 페이지의 nttId 컬럼이 비어있으면 `원문 링크`에서 `nttId=…` 를 파싱해 즉시 채움 (기존 수동 페이지 호환)
2. **크롤** — FSS 상위 `PAGES_TO_FETCH`(=2) 페이지 파싱
3. **3-way 분기**:
   | 케이스 | 판정 | 조치 |
   |---|---|---|
   | 신규 | 크롤에서 나온 nttId가 스냅샷에 없음 | `pages.create` + `신규=true` + `최초수집=now` |
   | 업데이트 | 스냅샷에 있으나 조회수/제목/부서/등록일/신규창구 중 하나라도 변경 | `pages.update` 로 변경 필드만 patch |
   | 오래된 신규 해제 | 이번 크롤에 안 잡힌 페이지 중 `신규=true`이고 `first_seen + 24h < now` | `신규=false` |
4. 로그에 `추가 N · 업데이트 M · 신규해제 K · 실패 F · Notion 누적 T건` 요약

## Notion DB 스키마

`Crawler_FSS > FSS 보도자료 DB`

| 컬럼 | 타입 | 내용 |
|---|---|---|
| 제목 | Title | 게시글 제목 |
| 번호 | Number | 게시판 번호 |
| 등록일 | Date | FSS 게시 등록일 |
| 담당부서 | Text | 담당부서 |
| 조회수 | Number | 조회수 (매 sync 시 갱신) |
| 원문 링크 | URL | FSS 원문 URL |
| 신규 | Checkbox | 최초 수집 이후 24시간 이내면 true |
| 최초 수집 | Date | 크롤러가 처음 발견한 시각 (KST) |
| nttId | Rich text | 중복 방지 키 (FSS 게시글 고유 ID) |

## GitHub Secrets

저장소 `Settings → Secrets and variables → Actions` 에서 등록:

| Name | Value |
|---|---|
| `NOTION_TOKEN` | Notion Integration secret (`ntn_...`) — crawler_news 저장소와 동일 값 재사용 가능 |
| `NOTION_DB_ID` | `FSS 보도자료 DB`의 database ID |

> Integration이 해당 DB에 연결되어 있어야 한다: Notion `FSS 보도자료 DB` 페이지 → `···` → `Connections` → Integration 추가.

## 로컬 테스트
```powershell
cd fss_monitor
pip install -r requirements.txt
$env:NOTION_TOKEN = "ntn_..."
$env:NOTION_DB_ID = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
python run_headless.py
```

## 조정 파라미터 (`config.py`)
- `PAGES_TO_FETCH` — 크롤할 목록 페이지 수 (기본 2)
- `NEW_BADGE_HOURS` — 신규 체크 유지 시간 (기본 24)
- `NOTION_RATE_DELAY` — Notion API 호출 간격 초 (기본 0.35)
- `NOTION_PROPS` — 실제 DB 컬럼명과의 매핑
