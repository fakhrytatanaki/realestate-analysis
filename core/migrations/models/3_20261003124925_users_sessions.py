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
        DROP TABLE IF EXISTS "app_user";
        DROP TABLE IF EXISTS "user_session";"""


MODELS_STATE = (
    "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"
)
