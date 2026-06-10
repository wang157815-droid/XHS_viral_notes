"""通过 xhs_comment_note 回填 xhs_comment_data 中的 note_url 和 note_title。"""
import asyncio, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from xhs_utils.common_util import load_env
load_env()

async def run():
    from backend.app.infrastructure.storage.db_engine import get_db_session
    from sqlalchemy import text

    async with get_db_session() as session:
        result = await session.execute(text("""
            UPDATE xhs_comment_data AS c
            SET
                note_url   = n.note_url,
                note_title = n.title
            FROM xhs_comment_note AS n
            WHERE c.note_id = n.note_id
              AND (
                  c.note_url   IS NULL OR c.note_url   = ''
               OR c.note_title IS NULL OR c.note_title = ''
              )
        """))
        print(f"更新行数: {result.rowcount}")

asyncio.run(run())
