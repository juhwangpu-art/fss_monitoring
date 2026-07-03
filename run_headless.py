# -*- coding: utf-8 -*-
"""헤드리스 FSS 크롤러 + Notion push. GitHub Actions cron에서 실행.

로컬 SQLite/캐시를 쓰지 않는다. dedup은 매 실행마다 Notion DB를 조회해
기존 nttId 집합을 in-memory로 로드한 뒤, 신규만 push한다.

실행:
  # 로컬 테스트 (PowerShell)
  $env:NOTION_TOKEN = "..."; $env:NOTION_DB_ID = "..."; python run_headless.py

  # GitHub Actions (workflow가 자동 실행)
"""
import os
import sys
from datetime import datetime, timezone, timedelta

from notion_client import Client

import crawler
from sync_notion import (
    NotionTokenMissing,
    fetch_existing_ntt_ids,
    push_new_posts,
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
    now_iso = datetime.now(KST).isoformat(timespec="seconds")

    print("→ Notion DB 기존 nttId 로드")
    existing = fetch_existing_ntt_ids(notion, db_id)
    print(f"  기존 {len(existing)}건")

    print("→ FSS 보도자료 크롤")
    posts = crawler.fetch_all()
    print(f"  크롤 {len(posts)}건")

    to_push = [p for p in posts if p["ntt_id"] not in existing]
    print(f"→ 신규 push 대상 {len(to_push)}건")

    if not to_push:
        print("신규 없음")
        return 0

    added, failed = push_new_posts(notion, db_id, to_push, now_iso)
    print(f"완료 — 추가 {added}건 · 실패 {failed}건 · Notion 누적 {len(existing) + added}건")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except NotionTokenMissing as e:
        print(str(e))
        sys.exit(1)
