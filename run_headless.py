# -*- coding: utf-8 -*-
"""헤드리스 FSS 크롤러 + Notion push/patch. GitHub Actions cron에서 실행.

동작:
  1. Notion DB의 모든 페이지 스냅샷 로드 (nttId 비면 원문 링크에서 backfill)
  2. FSS 게시판 상위 몇 페이지 크롤
  3. 3-way 분기:
       - 신규 (nttId 없음)             → pages.create + 신규=True + 최초수집=now
       - 기존 & 변경 있음               → pages.update (변경 필드만; view_count/title/…, 신규 24h 창구)
       - 이번 크롤에 없는 기존 페이지    → 신규=True이고 first_seen+24h 지났으면 신규=False
  4. NOTION_SUMMARY_PAGE_ID 설정 시: Crawler_FSS 페이지의 sentinel 블록 사이를
     동기화 시각/총 게시글/신규 건수/담당부서별 통계로 교체.

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
    compute_summary_stats,
    fetch_existing_pages,
    get_data_source_id,
    project_pages_map,
    push_new_posts,
    unmark_stale_new,
    update_summary_page,
)

KST = timezone(timedelta(hours=9))


def _clean_env(name: str) -> str | None:
    """env 값의 앞뒤 공백/개행 제거. 빈 문자열이면 None."""
    v = os.environ.get(name)
    if v is None:
        return None
    v = v.strip()
    return v or None


def main() -> int:
    token = _clean_env("NOTION_TOKEN")
    db_id = _clean_env("NOTION_DB_ID")
    summary_page_id = _clean_env("NOTION_SUMMARY_PAGE_ID")
    if not token:
        raise NotionTokenMissing("NOTION_TOKEN 환경변수 미설정")
    if not db_id:
        raise NotionTokenMissing("NOTION_DB_ID 환경변수 미설정")

    notion = Client(auth=token)
    now_dt = datetime.now(KST)
    now_iso = now_dt.isoformat(timespec="seconds")

    print("→ Notion DB 스냅샷 로드")
    ds_id = get_data_source_id(notion, db_id)
    pages_map = fetch_existing_pages(notion, ds_id)
    print(f"  기존 {len(pages_map)}건 (data_source={ds_id[:8]}…)")

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

    added, add_fail = push_new_posts(notion, ds_id, new_posts, now_iso)
    updated, upd_fail = apply_updates(notion, updates)
    unmarked, un_fail, unmarked_ntt_ids = unmark_stale_new(
        notion, pages_map, now_dt, crawled_ntt_ids
    )

    total_fail = add_fail + upd_fail + un_fail
    changed = added + updated + unmarked
    print(
        f"완료 — 추가 {added} · 업데이트 {updated} · 신규해제 {unmarked} "
        f"· 실패 {total_fail} · Notion 누적 {len(pages_map) + added}건"
    )

    # ------- Summary page 갱신 -------
    if summary_page_id:
        if changed == 0:
            print("→ 변경 없음, summary 페이지 skip")
        else:
            print("→ Crawler_FSS 요약 페이지 갱신")
            projected = project_pages_map(
                pages_map, new_posts, updates, unmarked_ntt_ids, now_dt, now_iso
            )
            stats = compute_summary_stats(projected, now_dt)
            try:
                r = update_summary_page(notion, summary_page_id, stats)
                print(
                    f"  mode={r.get('mode')} deleted={r.get('deleted', 0)} "
                    f"added={r.get('added', 0)} protected={r.get('protected', 0)}"
                )
            except Exception as e:
                print(f"  summary 갱신 실패: {e}")
                total_fail += 1
    else:
        print("→ NOTION_SUMMARY_PAGE_ID 미설정, summary 갱신 skip")

    return 0 if total_fail == 0 else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except NotionTokenMissing as e:
        print(str(e))
        sys.exit(1)
