import time
from datetime import datetime

from notion_client import Client

from config import NOTION_PROPS, NOTION_RATE_DELAY


class NotionTokenMissing(RuntimeError):
    pass


def _rich_text(s: str) -> list[dict]:
    return [{"type": "text", "text": {"content": s or ""}}]


def _to_int(v) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, int):
        return v
    try:
        return int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def build_page(post: dict, now_iso: str) -> dict:
    """FSS 크롤 결과 dict → Notion pages.create properties dict."""
    p = NOTION_PROPS
    props: dict = {
        p["title"]: {"title": _rich_text(post.get("title") or "")},
        p["ntt_id"]: {"rich_text": _rich_text(post.get("ntt_id") or "")},
        p["department"]: {"rich_text": _rich_text(post.get("department") or "")},
        p["link"]: {"url": post.get("link")},
        p["is_new"]: {"checkbox": True},
        p["first_seen"]: {"date": {"start": now_iso}},
    }

    no_int = _to_int(post.get("no"))
    if no_int is not None:
        props[p["no"]] = {"number": no_int}

    vc_int = _to_int(post.get("view_count"))
    if vc_int is not None:
        props[p["view_count"]] = {"number": vc_int}

    posted = (post.get("posted_date") or "").strip()
    if posted:
        # FSS 사이트는 YYYY-MM-DD 형태로 제공. 이상 값이면 Date 필드는 비운다.
        try:
            datetime.strptime(posted, "%Y-%m-%d")
            props[p["posted_date"]] = {"date": {"start": posted}}
        except ValueError:
            pass

    return props


def fetch_existing_ntt_ids(notion: Client, db_id: str) -> set[str]:
    """Notion DB를 순회해 nttId 집합을 in-memory로 로드한다."""
    ntt_prop = NOTION_PROPS["ntt_id"]
    existing: set[str] = set()
    cursor = None
    while True:
        payload = {"database_id": db_id, "page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        resp = notion.databases.query(**payload)
        for page in resp.get("results", []):
            rt = page.get("properties", {}).get(ntt_prop, {}).get("rich_text") or []
            if rt:
                value = (rt[0].get("plain_text") or "").strip()
                if value:
                    existing.add(value)
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return existing


def push_new_posts(
    notion: Client, db_id: str, posts: list[dict], now_iso: str
) -> tuple[int, int]:
    added, failed = 0, 0
    for i, post in enumerate(posts, 1):
        try:
            notion.pages.create(
                parent={"database_id": db_id},
                properties=build_page(post, now_iso),
            )
            added += 1
            print(f"  [{i}/{len(posts)}] {(post.get('title') or '')[:50]}")
        except Exception as e:
            failed += 1
            print(f"  실패 [{post.get('ntt_id')}] {(post.get('title') or '')[:40]}: {e}")
        time.sleep(NOTION_RATE_DELAY)
    return added, failed
