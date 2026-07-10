import re
import time
from datetime import datetime, timedelta

from notion_client import Client

from config import NEW_BADGE_HOURS, NOTION_PROPS, NOTION_RATE_DELAY

SUMMARY_SENTINEL_START = "📊 자동 갱신 통계"
SUMMARY_SENTINEL_END = "🔒 자동 갱신 영역 끝"
PROTECTED_BLOCK_TYPES = {"child_database", "child_page", "link_to_page"}


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


def get_data_source_id(notion: Client, database_id: str) -> str:
    """Notion API 2025-09-03: DB → data source ID 로 조회.

    최신 notion-client(2.4+)에서 databases.query 가 제거되고 data_sources.query
    로 이관됐다. 이 함수는 DB의 첫 번째 data source id 를 반환한다.
    """
    db = notion.databases.retrieve(database_id=database_id)
    data_sources = db.get("data_sources") or []
    if not data_sources:
        raise RuntimeError(
            f"database {database_id}에 data source 없음. "
            "Notion API 2025-09-03 migration 확인 필요."
        )
    return data_sources[0]["id"]


def fetch_existing_pages(notion: Client, ds_id: str) -> dict[str, dict]:
    """Data source의 모든 페이지를 nttId 키로 스냅샷 로드.

    nttId가 비어있는 페이지는 원문 링크에서 nttId를 파싱해 즉시 backfill한다
    (기존에 수동으로 넣어둔 페이지 호환용).
    """
    p = NOTION_PROPS
    pages_map: dict[str, dict] = {}
    cursor = None

    while True:
        payload = {"data_source_id": ds_id, "page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        resp = notion.data_sources.query(**payload)

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


def _post_new_page_mention(
    notion: Client, page_id: str, user_ids: list[str], title: str, department: str
) -> None:
    """새로 push된 페이지에 mention 코멘트 작성.

    형식: `@user1 @user2 … {title} | {department} | 새글 업데이트 확인 필요`
    복수 대상이 있으면 하나의 코멘트에 모두 mention.
    """
    rich_text: list[dict] = []
    for uid in user_ids:
        rich_text.append(
            {"type": "mention", "mention": {"type": "user", "user": {"id": uid}}}
        )
        rich_text.append({"type": "text", "text": {"content": " "}})
    body = f"{title} | {department} | 새글 업데이트 확인 필요"
    rich_text.append({"type": "text", "text": {"content": body}})
    notion.comments.create(parent={"page_id": page_id}, rich_text=rich_text)


def push_new_posts(
    notion: Client,
    ds_id: str,
    posts: list[dict],
    now_iso: str,
    mention_user_ids: list[str] | None = None,
) -> tuple[int, int, int, int]:
    """반환: (added, add_fail, mention_ok, mention_fail)"""
    added, failed = 0, 0
    mention_ok, mention_fail = 0, 0
    for i, post in enumerate(posts, 1):
        try:
            new_page = notion.pages.create(
                parent={"data_source_id": ds_id},
                properties=build_page(post, now_iso),
            )
            added += 1
            title = post.get("title") or ""
            print(f"  [new {i}/{len(posts)}] {title[:50]}")

            if mention_user_ids:
                try:
                    _post_new_page_mention(
                        notion,
                        new_page["id"],
                        mention_user_ids,
                        title,
                        post.get("department") or "",
                    )
                    mention_ok += 1
                except Exception as e:
                    mention_fail += 1
                    print(f"    mention 실패: {e}")
                time.sleep(NOTION_RATE_DELAY)
        except Exception as e:
            failed += 1
            print(f"  실패 [{post.get('ntt_id')}] {(post.get('title') or '')[:40]}: {e}")
        time.sleep(NOTION_RATE_DELAY)
    return added, failed, mention_ok, mention_fail


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
) -> tuple[int, int, set[str]]:
    """이번 크롤에 안 잡힌 페이지 중 신규=True인데 24h 지난 것은 신규=False.

    반환: (unmarked_count, failed_count, unmarked_ntt_ids)
    """
    p = NOTION_PROPS
    unmarked, failed = 0, 0
    unmarked_ntt_ids: set[str] = set()
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
            unmarked_ntt_ids.add(ntt_id)
            print(f"  [-new] {(snap.get('title') or '')[:40]}")
        except Exception as e:
            failed += 1
            print(f"  실패 [{ntt_id}]: {e}")
        time.sleep(NOTION_RATE_DELAY)
    return unmarked, failed, unmarked_ntt_ids


# ---------------------------------------------------------------------------
# Summary page (Crawler_FSS) — sentinel 기반 자동 갱신
# ---------------------------------------------------------------------------


def _extract_heading_text(block: dict) -> str | None:
    t = block.get("type")
    if t not in ("heading_1", "heading_2", "heading_3"):
        return None
    rt = block.get(t, {}).get("rich_text") or []
    return "".join(x.get("plain_text") or "" for x in rt).strip()


def _list_page_top_blocks(notion: Client, page_id: str) -> list[dict]:
    blocks: list[dict] = []
    cursor = None
    while True:
        payload = {"block_id": page_id, "page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        resp = notion.blocks.children.list(**payload)
        blocks.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return blocks


def project_pages_map(
    pages_map: dict[str, dict],
    new_posts: list[dict],
    updates: list[tuple[str, dict, str, str]],
    unmarked_ntt_ids: set[str],
    now_dt: datetime,
    now_iso: str,
) -> dict[str, dict]:
    """이번 sync 이후의 pages_map 상태를 시뮬레이트. 통계 계산용."""
    p = NOTION_PROPS
    projected: dict[str, dict] = {k: dict(v) for k, v in pages_map.items()}

    for post in new_posts:
        projected[post["ntt_id"]] = {
            "title": post.get("title") or "",
            "department": post.get("department") or "",
            "view_count": _to_int(post.get("view_count")),
            "posted_date": post.get("posted_date") or "",
            "is_new": True,
            "first_seen": now_iso,
        }

    for _pid, patch, ntt_id, _title in updates:
        snap = projected.get(ntt_id)
        if not snap:
            continue
        if p["view_count"] in patch:
            snap["view_count"] = patch[p["view_count"]]["number"]
        if p["is_new"] in patch:
            snap["is_new"] = patch[p["is_new"]]["checkbox"]
        if p["title"] in patch:
            snap["title"] = "".join(
                t["text"]["content"] for t in patch[p["title"]]["title"]
            )
        if p["department"] in patch:
            snap["department"] = "".join(
                t["text"]["content"] for t in patch[p["department"]]["rich_text"]
            )
        if p["posted_date"] in patch:
            snap["posted_date"] = patch[p["posted_date"]]["date"]["start"]

    for ntt_id in unmarked_ntt_ids:
        if ntt_id in projected:
            projected[ntt_id]["is_new"] = False

    # 안전장치: 24h 창구로 is_new 최종 재판정 (create/update가 놓친 경우 대비)
    for snap in projected.values():
        if snap.get("first_seen"):
            snap["is_new"] = _is_within_new_window(snap["first_seen"], now_dt)

    return projected


def compute_summary_stats(pages_map: dict[str, dict], now_dt: datetime) -> dict:
    total = len(pages_map)
    new_count = 0
    latest_posted = ""
    dept_counter: dict[str, int] = {}
    for snap in pages_map.values():
        if snap.get("is_new"):
            new_count += 1
        pd = snap.get("posted_date") or ""
        if pd > latest_posted:
            latest_posted = pd
        dept = (snap.get("department") or "").strip()
        if dept:
            dept_counter[dept] = dept_counter.get(dept, 0) + 1
    return {
        "sync_time": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "total": total,
        "new_count": new_count,
        "latest_posted": latest_posted or "—",
        "dept_count": len(dept_counter),
        "dept_breakdown": sorted(dept_counter.items(), key=lambda x: (-x[1], x[0])),
    }


def _text_block(kind: str, text: str) -> dict:
    return {
        "object": "block",
        "type": kind,
        kind: {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def _build_summary_blocks(stats: dict) -> list[dict]:
    blocks = [
        _text_block("paragraph", f"⏱ 동기화 시각: {stats['sync_time']} KST"),
        _text_block(
            "paragraph",
            f"📄 총 게시글: {stats['total']}건   ·   🆕 최근 24h 신규: {stats['new_count']}건",
        ),
        _text_block(
            "paragraph",
            f"📅 최근 등록일: {stats['latest_posted']}   ·   🏢 담당부서 수: {stats['dept_count']}곳",
        ),
        _text_block("heading_3", "담당부서별 건수"),
    ]
    for dept, cnt in stats["dept_breakdown"]:
        blocks.append(_text_block("bulleted_list_item", f"{cnt}건 — {dept}"))
    return blocks


def update_summary_page(notion: Client, page_id: str, stats: dict) -> dict:
    """Sentinel(📊/🔒) 사이 블록을 새 통계로 교체.

    - 두 sentinel이 존재 → 사이 블록 삭제 (child_database 등은 보호) 후 fresh 삽입
    - 없으면 페이지 끝에 sentinel + 통계 부착 (init 모드)
    """
    blocks = _list_page_top_blocks(notion, page_id)

    start_idx: int | None = None
    end_idx: int | None = None
    for i, b in enumerate(blocks):
        heading = _extract_heading_text(b)
        if heading == SUMMARY_SENTINEL_START:
            start_idx = i
        elif heading == SUMMARY_SENTINEL_END and start_idx is not None:
            end_idx = i
            break

    fresh_content = _build_summary_blocks(stats)

    if start_idx is not None and end_idx is not None and end_idx > start_idx:
        deleted = 0
        skipped_protected = 0
        for b in blocks[start_idx + 1 : end_idx]:
            if b.get("type") in PROTECTED_BLOCK_TYPES:
                skipped_protected += 1
                continue
            try:
                notion.blocks.delete(block_id=b["id"])
                deleted += 1
            except Exception as e:
                print(f"  summary 블록 삭제 실패: {e}")
            time.sleep(NOTION_RATE_DELAY)

        try:
            notion.blocks.children.append(
                block_id=page_id,
                children=fresh_content,
                after=blocks[start_idx]["id"],
            )
            added = len(fresh_content)
        except Exception as e:
            print(f"  summary content 추가 실패: {e}")
            added = 0
        return {
            "mode": "refresh",
            "deleted": deleted,
            "added": added,
            "protected": skipped_protected,
        }

    # init: sentinel + 통계 부착
    init_children = (
        [_text_block("heading_2", SUMMARY_SENTINEL_START)]
        + fresh_content
        + [_text_block("heading_2", SUMMARY_SENTINEL_END)]
    )
    try:
        notion.blocks.children.append(block_id=page_id, children=init_children)
    except Exception as e:
        print(f"  summary init 실패: {e}")
        return {"mode": "init", "added": 0, "error": str(e)}
    return {"mode": "init", "added": len(init_children)}
