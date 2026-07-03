import re
from urllib.parse import urljoin, parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from config import BOARD_URL, MENU_NO, HTTP_HEADERS, HTTP_TIMEOUT, PAGES_TO_FETCH


def _parse_int(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"[\d,]+", text)
    return int(m.group().replace(",", "")) if m else None


def _find_list_table(soup: BeautifulSoup):
    for table in soup.find_all("table"):
        caption = table.find("caption")
        if caption and "보도자료" in caption.get_text():
            return table
    return soup.find("table")


def _parse_row(tr) -> dict | None:
    tds = tr.find_all("td")
    if len(tds) < 4:
        return None
    a = tds[1].find("a")
    if not a or not a.get("href"):
        return None
    href = a["href"]
    ntt_id = None
    m = re.search(r"nttId=(\d+)", href)
    if m:
        ntt_id = m.group(1)
    else:
        qs = parse_qs(urlparse(href).query)
        ntt_id = (qs.get("nttId") or [None])[0]
    if not ntt_id:
        return None
    title = a.get_text(strip=True)
    view_count = _parse_int(tds[-1].get_text(strip=True)) if len(tds) >= 5 else None
    return {
        "ntt_id": ntt_id,
        "no": tds[0].get_text(strip=True),
        "title": title,
        "department": tds[2].get_text(strip=True) if len(tds) > 2 else "",
        "posted_date": tds[3].get_text(strip=True) if len(tds) > 3 else "",
        "link": urljoin(BOARD_URL, href),
        "view_count": view_count,
    }


def fetch_page(page_index: int = 1) -> list[dict]:
    params = {"menuNo": MENU_NO, "pageIndex": page_index}
    resp = requests.get(BOARD_URL, params=params, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    table = _find_list_table(soup)
    if not table:
        return []
    tbody = table.find("tbody") or table
    rows = []
    for tr in tbody.find_all("tr"):
        row = _parse_row(tr)
        if row:
            rows.append(row)
    return rows


def fetch_all(pages: int = PAGES_TO_FETCH) -> list[dict]:
    seen = {}
    for p in range(1, pages + 1):
        for row in fetch_page(p):
            seen.setdefault(row["ntt_id"], row)
    return list(seen.values())


if __name__ == "__main__":
    posts = fetch_all()
    print(f"Fetched {len(posts)} posts")
    for p in posts[:5]:
        print(f"  [{p['no']}] {p['posted_date']} | {p['department']} | {p['title'][:50]}")
