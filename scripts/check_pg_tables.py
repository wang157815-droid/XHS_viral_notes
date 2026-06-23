"""检查 PostgreSQL 迁移后两张表的数据情况"""
import asyncio
import os
import sys

sys.path.insert(0, ".")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://redmuse:redmuse123@localhost:5432/redmuse")


async def check():
    from backend.app.infrastructure.storage.db_engine import get_db_engine
    from sqlalchemy import text

    engine = await get_db_engine()
    async with engine.connect() as conn:
        r = await conn.execute(text("SELECT COUNT(*) FROM xhs_comment_note"))
        print("xhs_comment_note 总行数:", r.scalar())

        r = await conn.execute(text("SELECT COUNT(*) FROM xhs_comment_data"))
        print("xhs_comment_data 总行数:", r.scalar())

        r = await conn.execute(
            text("SELECT COUNT(*) FROM xhs_comment_note WHERE note_url IS NOT NULL AND note_url <> ''")
        )
        print("xhs_comment_note 有 note_url 的笔记:", r.scalar())

        r = await conn.execute(
            text("SELECT COUNT(*) FROM xhs_comment_data WHERE note_url IS NOT NULL AND note_url <> ''")
        )
        print("xhs_comment_data 有 note_url 的评论:", r.scalar())

        print("\n--- 笔记样例（前3条）---")
        r = await conn.execute(text("SELECT note_id, title, note_url FROM xhs_comment_note LIMIT 3"))
        for row in r.fetchall():
            print(f"  note_id={row[0]}, title={row[1]}, note_url={row[2]}")

        print("\n--- 评论样例（前3条）---")
        r = await conn.execute(
            text("SELECT comment_id, note_id, note_url, note_title, content FROM xhs_comment_data LIMIT 3")
        )
        for row in r.fetchall():
            print(f"  comment_id={row[0]}, note_id={row[1]}, note_url={row[2]}, note_title={row[3]}, content={row[4][:20]}")

    await engine.dispose()


asyncio.run(check())
