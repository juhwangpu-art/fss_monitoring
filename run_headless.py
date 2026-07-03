# -*- coding: utf-8 -*-
"""헤드리스 FSS 크롤러 + Notion push/patch. GitHub Actions cron에서 실행.

동작:
  1. Notion DB의 모든 페이지 스냅샷 로드 (nttId 비면 원문 링크에서 backfill)
  2. FSS 게시판 상위 몇 페이지 크롤
  3. 3-way 분기:
       - 신규 (nttId 없음)             → pages.create + 신규=True + 최초수집=now
       - 기존 & 변경 있음               → pages.update (변경 필드만; view_count/title/…, 신규 24h 창구)
       - 이번 크롤에 없는 기존 페이지    → 신규=True이고 first_seen+24h 지났으면 신규=False

로컬 SQLite 캐시 없음. dedup은 매 실행마다 Notion을 조회.
"""
import os
import sys
from datetime import datetime, timezone, timedelta

from notion_client import Client

import crawler
from sync_notion import (
    NotionTokenMissing,
    apply_updates,
    build_update_props,
    fetch_existing_pages,
    push_new_posts,
    unmark_stale_new,
)

KST = timezone(timedelta(hours=9))


def main() -> int:
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_DB_ID")
    if not token:
        raise NotionTokenMissing("NOTION_TOKEN 환경변수 미설정")
    if not db_id:
        raise NotionTokenMissing("NOTION_DB_ID 환경변수 미설정")

    notion = Client(auth=token)
    now_dt = datetime.now(KST)
    now_iso = now_dt.isoformat(timespec="seconds")

    print("→ Notion DB 스냅샷 로드")
    pages_map = fetch_existing_pages(notion, db_id)
    print(f"  기존 {len(pages_map)}건")

    print("→ FSS 보도자료 크롤")
    posts = crawler.fetch_all()
    print(f"  크롤 {len(posts)}건")

    new_posts: list[dict] = []
    updates: list[tuple[str, dict, str, str]] = []
    crawled_ntt_ids: set[str] = set()

    for post in posts:
        ntt_id = post["ntt_id"]
        crawled_ntt_ids.add(ntt_id)
        snap = pages_map.get(ntt_id)
        if snap is None:
            new_posts.append(post)
            continue
        patch = build_update_props(post, snap, now_dt)
        if patch:
            updates.append((snap["page_id"], patch, ntt_id, post.get("title") or ""))

    print(f"→ 신규 {len(new_posts)}건 · 업데이트 {len(updates)}건")

    added, add_fail = push_new_posts(notion, db_id, new_posts, now_iso)
    updated, upd_fail = apply_updates(notion, updates)
    unmarked, un_fail = unmark_stale_new(notion, pages_map, now_dt, crawled_ntt_ids)

    total_fail = add_fail + upd_fail + un_fail
    print(
        f"완료 — 추가 {added} · 업데이트 {updated} · 신규해제 {unmarked} "
        f"· 실패 {total_fail} · Notion 누적 {len(pages_map) + added}건"
    )
    return 0 if total_fail == 0 else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except NotionTokenMissing as e:
        print(str(e))
        sys.exit(1)
