# FSS 보도자료 → Notion 동기화

금융감독원 보도자료 게시판(`B0000188`)을 크롤링해서 Notion `FSS 보도자료 DB`에 신규 글을 자동으로 push한다.
로컬 Streamlit 대시보드는 [`../fss_monitor_local/`](../fss_monitor_local/)로 분리했다.

- 저장소: [github.com/juhwangpu-art/fss_monitoring](https://github.com/juhwangpu-art/fss_monitoring)
- 참고 저장소: [juhwangpu-art/crawler_news](https://github.com/juhwangpu-art/crawler_news)
- Notion 대상: `Crawler_FSS > FSS 보도자료 DB`

## 실행 방식
- **GitHub Actions cron** — 매일 07:30 / 19:30 KST 자동 실행 ([`.github/workflows/crawl.yml`](.github/workflows/crawl.yml))
- **수동 실행** — Actions 탭에서 `Run workflow` (workflow_dispatch)
- **로컬 테스트** — 환경변수 세팅 후 `python run_headless.py`

## 파일 구성
| 파일 | 역할 |
|---|---|
| `config.py` | 게시판 URL, HTTP 헤더, Notion property 매핑 |
| `crawler.py` | 목록 페이지 파싱(`requests` + `BeautifulSoup`) |
| `sync_notion.py` | Notion API wrapper — `nttId` 기준 dedup, page 생성 |
| `run_headless.py` | 진입점 — 기존 nttId 로드 → 크롤 → 신규만 push |
| `.github/workflows/crawl.yml` | Actions 워크플로 (cron + workflow_dispatch) |

로컬 SQLite 캐시를 쓰지 않는다. 매 실행마다 Notion DB를 순회해 기존 `nttId` 집합을 in-memory로 로드하고 신규만 push한다.

## Notion DB 스키마

`Crawler_FSS > FSS 보도자료 DB` (기존 DB에 `nttId` 컬럼 추가함)

| 컬럼 | 타입 | 내용 |
|---|---|---|
| 제목 | Title | 게시글 제목 |
| 번호 | Number | 게시판 번호 |
| 등록일 | Date | FSS 게시 등록일 |
| 담당부서 | Text | 담당부서 |
| 조회수 | Number | 조회수 |
| 원문 링크 | URL | FSS 원문 URL |
| 신규 | Checkbox | 이번 sync에서 새로 발견된 글이면 체크 |
| 최초 수집 | Date | 크롤러가 처음 발견한 시각(KST) |
| **nttId** | **Rich text** | **중복 방지 키 (FSS 게시글 고유 ID)** |

## GitHub Secrets

저장소 `Settings → Secrets and variables → Actions`에서 두 개 등록:

| Name | Value |
|---|---|
| `NOTION_TOKEN` | Notion Integration secret (`ntn_...`) — `crawler_news` 저장소와 동일 값 재사용 가능 |
| `NOTION_DB_ID` | `FSS 보도자료 DB`의 database ID (Notion DB 페이지 URL에서 32자 hex 부분) |

> Integration이 해당 DB에 연결되어 있어야 한다: Notion `FSS 보도자료 DB` 페이지 → `···` → `Connections` → Integration 추가.

## 로컬 테스트
```powershell
cd fss_monitor
pip install -r requirements.txt
$env:NOTION_TOKEN = "ntn_..."
$env:NOTION_DB_ID = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
python run_headless.py
```

## 동작 요약
1. `fetch_existing_ntt_ids` — Notion DB pagination으로 기존 nttId set 로드
2. `crawler.fetch_all` — FSS 게시판 상위 `PAGES_TO_FETCH` 페이지 파싱
3. dedup — nttId가 기존 set에 없는 것만 필터
4. `push_new_posts` — 실제 DB 스키마(Number/Date/URL/Checkbox 등)에 맞춰 `pages.create` (`NOTION_RATE_DELAY` 간격)
5. `신규` 체크박스는 이번 실행에서 push된 글만 True. 이후 실행에서는 그 글을 다시 건드리지 않음
