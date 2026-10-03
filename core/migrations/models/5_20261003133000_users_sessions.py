from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "app_user" (
    "id" UUID NOT NULL PRIMARY KEY,
    "email" VARCHAR(254) NOT NULL UNIQUE,
    "display_name" VARCHAR(128) NOT NULL,
    "password_hash" VARCHAR(255) NOT NULL,
    "is_active" BOOL NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);
COMMENT ON COLUMN "app_user"."email" IS 'Stored normalised (stripped, lower-cased), so a plain unique index suffices.';
COMMENT ON TABLE "app_user" IS 'An account. ``user`` is reserved in PostgreSQL, hence ``app_user``.';
        CREATE TABLE IF NOT EXISTS "user_session" (
    "id" UUID NOT NULL PRIMARY KEY,
    "token_hash" VARCHAR(64) NOT NULL UNIQUE,
    "expires_at" TIMESTAMPTZ NOT NULL,
    "last_used_at" TIMESTAMPTZ NOT NULL,
    "user_agent" VARCHAR(512),
    "created_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL REFERENCES "app_user" ("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "idx_user_sessio_expires_adab17" ON "user_session" ("expires_at");
COMMENT ON TABLE "user_session" IS 'A signed-in session, keyed by the sha256 of its bearer token.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "user_session";
        DROP TABLE IF EXISTS "app_user";"""


MODELS_STATE = (
    "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"
    "b/Mvv9/5v1fGQ="
)
