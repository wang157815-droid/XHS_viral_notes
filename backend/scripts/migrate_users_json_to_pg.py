from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from backend.app.infrastructure.db import ensure_business_schema, get_business_db_session
from backend.app.services.redmuse_auth.user_store import RedMuseUserStore


def migrate_users_json_to_pg(store_file: str | None = None) -> int:
    ensure_business_schema()
    source = RedMuseUserStore(store_file=store_file) if store_file else RedMuseUserStore()
    users = source.list_users()
    if not users:
        return 0

    with get_business_db_session() as session:
        for user in users:
            session.execute(
                text(
                    """
                    INSERT INTO redmuse_users (
                        user_id,
                        username,
                        nickname,
                        password_hash,
                        role,
                        status,
                        xhs_credential_path,
                        created_at,
                        updated_at,
                        last_login_at
                    ) VALUES (
                        :user_id,
                        :username,
                        :nickname,
                        :password_hash,
                        :role,
                        :status,
                        :xhs_credential_path,
                        :created_at,
                        :updated_at,
                        :last_login_at
                    )
                    ON CONFLICT (user_id) DO UPDATE SET
                        username = EXCLUDED.username,
                        nickname = EXCLUDED.nickname,
                        password_hash = EXCLUDED.password_hash,
                        role = EXCLUDED.role,
                        status = EXCLUDED.status,
                        xhs_credential_path = EXCLUDED.xhs_credential_path,
                        created_at = EXCLUDED.created_at,
                        updated_at = EXCLUDED.updated_at,
                        last_login_at = EXCLUDED.last_login_at
                    """
                ),
                user.to_dict(),
            )
    return len(users)


if __name__ == "__main__":
    count = migrate_users_json_to_pg(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"migrated {count} users")
