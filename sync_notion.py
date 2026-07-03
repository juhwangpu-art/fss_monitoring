import re
import time
from datetime import datetime, timedelta

from notion_client import Client

from config import NEW_BADGE_HOURS, NOTION_PROPS, NOTION_RATE_DELAY


class NotionTokenMissing(RuntimeError):
    pass


def _rich_text(s: str) -> list[dict]:
    return [{"type": "text", "text": {"content": s or ""}}]


def _read_title(prop: dict) -> str:
    ts = prop.get("title") or []
    return "".join(t.get("plain_text") or "" for t in ts).strip()


def _read_rich_text(prop: dict) -> str:
    rt = prop.get("rich_text") or []
    return "".join(t.get("plain_text") or "" for t in rt).strip()


def _to_int(v) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, int):
        return v
    try:
        return int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _parse_dt(s: str, tz):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt
    except ValueError:
        return None


def _is_within_new_window(first_seen_str: str, now_dt: datetime) -> bool:
    fs = _parse_dt(first_seen_str, now_dt.tzinfo)
    if not fs:
        return False
    return (now_dt - fs) < timedelta(hours=NEW_BADGE_HOURS)


def build_page(post: dict, now_iso: str) -> dict:
    """FSS 크롤 결과 → Notion pages.create properties."""
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
        try:
            datetime.strptime(posted, "%Y-%m-%d")
            props[p["posted_date"]] = {"date": {"start": posted}}
        except ValueError:
            pass

    return props


def fetch_existing_pages(notion: Client, db_id: str) -> dict[str, dict]:
    """Notion DB의 모든 페이지를 nttId 키로 스냅샷 로드.

    nttId가 비어있는 페이지는 원문 링크에서 nttId를 파싱해 즉시 backfill한다
    (기존에 수동으로 넣어둔 페이지 호환용).
    """
    p = NOTION_PROPS
    pages_map: dict[str, dict] = {}
    cursor = None

    while True:
        payload = {"database_id": db_id, "page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        resp = notion.databases.query(**payload)

        for page in resp.get("results", []):
            props = page.get("properties", {})
            ntt_id = _read_rich_text(props.get(p["ntt_id"], {}))
            link = props.get(p["link"], {}).get("url") or ""

            # Backfill from URL if nttId 비어있음
            if not ntt_id and link:
                m = re.search(r"nttId=(\d+)", link)
                if m:
                    ntt_id = m.group(1)
                    try:
                        notion.pages.update(
                            page_id=page["id"],
                            properties={p["ntt_id"]: {"rich_text": _rich_text(ntt_id)}},
                        )
                        print(f"  backfill nttId={ntt_id} → {(_read_title(props.get(p['title'], {})) or '')[:40]}")
                    except Exception as e:
                        print(f"  backfill 실패 {ntt_id}: {e}")
                    time.sleep(NOTION_RATE_DELAY)

            if not ntt_id:
                continue

            posted_prop = props.get(p["posted_date"], {}).get("date") or {}
            first_seen_prop = props.get(p["first_seen"], {}).get("date") or {}

            pages_map[ntt_id] = {
                "page_id": page["id"],
                "title": _read_title(props.get(p["title"], {})),
                "department": _read_rich_text(props.get(p["department"], {})),
                "view_count": props.get(p["view_count"], {}).get("number"),
                "posted_date": posted_prop.get("start") or "",
                "is_new": bool(props.get(p["is_new"], {}).get("checkbox")),
                "first_seen": first_seen_prop.get("start") or "",
            }

        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")

    return pages_map


def build_update_props(post: dict, snap: dict, now_dt: datetime) -> dict:
    """크롤 결과와 Notion 스냅샷을 비교, 변경 필드만 patch dict으로 반환."""
    p = NOTION_PROPS
    patch: dict = {}

    should_be_new = _is_within_new_window(snap.get("first_seen") or "", now_dt)
    if bool(snap.get("is_new")) != should_be_new:
        patch[p["is_new"]] = {"checkbox": should_be_new}

    new_vc = _to_int(post.get("view_count"))
    if new_vc is not None and new_vc != snap.get("view_count"):
        patch[p["view_count"]] = {"number": new_vc}

    new_title = (post.get("title") or "").strip()
    if new_title and new_title != (snap.get("title") or "").strip():
        patch[p["title"]] = {"title": _rich_text(new_title)}

    new_dept = (post.get("department") or "").strip()
    if new_dept and new_dept != (snap.get("department") or "").strip():
        patch[p["department"]] = {"rich_text": _rich_text(new_dept)}

    posted = (post.get("posted_date") or "").strip()
    if posted:
        try:
            datetime.strptime(posted, "%Y-%m-%d")
            if posted != (snap.get("posted_date") or ""):
                patch[p["posted_date"]] = {"date": {"start": posted}}
        except ValueError:
            pass

    return patch


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
            print(f"  [new {i}/{len(posts)}] {(post.get('title') or '')[:50]}")
        except Exception as e:
            failed += 1
            print(f"  실패 [{post.get('ntt_id')}] {(post.get('title') or '')[:40]}: {e}")
        time.sleep(NOTION_RATE_DELAY)
    return added, failed


def apply_updates(
    notion: Client, updates: list[tuple[str, dict, str, str]]
) -> tuple[int, int]:
    """updates: [(page_id, patch_props, ntt_id, title), ...]"""
    updated, failed = 0, 0
    for i, (page_id, patch, ntt_id, title) in enumerate(updates, 1):
        try:
            notion.pages.update(page_id=page_id, properties=patch)
            updated += 1
            fields = ",".join(patch.keys())
            print(f"  [upd {i}/{len(updates)}] {title[:40]}  ({fields})")
        except Exception as e:
            failed += 1
            print(f"  실패 [{ntt_id}] {title[:40]}: {e}")
        time.sleep(NOTION_RATE_DELAY)
    return updated, failed


def unmark_stale_new(
    notion: Client, pages_map: dict[str, dict], now_dt: datetime, exclude_ntt_ids: set[str]
) -> tuple[int, int]:
    """이번 크롤에 안 잡힌 페이지 중 신규=True인데 24h 지난 것은 신규=False."""
    p = NOTION_PROPS
    unmarked, failed = 0, 0
    for ntt_id, snap in pages_map.items():
        if ntt_id in exclude_ntt_ids:
            continue
        if not snap.get("is_new"):
            continue
        if _is_within_new_window(snap.get("first_seen") or "", now_dt):
            continue
        try:
            notion.pages.update(
                page_id=snap["page_id"],
                properties={p["is_new"]: {"checkbox": False}},
            )
            unmarked += 1
            print(f"  [-new] {(snap.get('title') or '')[:40]}")
        except Exception as e:
            failed += 1
            print(f"  실패 [{ntt_id}]: {e}")
        time.sleep(NOTION_RATE_DELAY)
    return unmarked, failed
