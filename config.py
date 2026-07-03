BOARD_URL = "https://www.fss.or.kr/fss/bbs/B0000188/list.do"
VIEW_URL = "https://www.fss.or.kr/fss/bbs/B0000188/view.do"
MENU_NO = "200218"

PAGES_TO_FETCH = 2

HTTP_TIMEOUT = 20
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

# Notion DB property names — 실제 DB 컬럼명과 정확히 일치해야 함.
# (DB: Crawler_FSS > FSS 보도자료 DB)
NOTION_PROPS = {
    "title": "제목",          # Title
    "no": "번호",              # Number
    "posted_date": "등록일",   # Date
    "department": "담당부서",   # Rich text
    "view_count": "조회수",    # Number
    "link": "원문 링크",        # URL
    "is_new": "신규",          # Checkbox
    "first_seen": "최초 수집",  # Date (datetime)
    "ntt_id": "nttId",        # Rich text — 중복 방지 키
}

# "신규" 배지 유지 기간
NEW_BADGE_HOURS = 24

# Notion API rate limit 회피용 sleep 간격 (초)
NOTION_RATE_DELAY = 0.35
