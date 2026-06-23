"""检查 MySQL 中 note_url 情况"""
import asyncio
import aiomysql


async def check():
    conn = await aiomysql.connect(
        host="localhost", port=3306, user="root", password="",
        db="media_crawler", charset="utf8mb4"
    )
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN note_url IS NOT NULL AND note_url <> '' THEN 1 ELSE 0 END) as has_url "
            "FROM xhs_note"
        )
        row = await cur.fetchone()
        print(f"总笔记: {row[0]}  有 note_url 的: {row[1]}")

        await cur.execute("SELECT note_url FROM xhs_note LIMIT 3")
        rows = await cur.fetchall()
        for r in rows:
            print("note_url 样例:", r[0])
    conn.close()


asyncio.run(check())
