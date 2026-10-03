from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "archive_rate_gate" (
    "name" VARCHAR(64) NOT NULL PRIMARY KEY,
    "next_at" TIMESTAMPTZ NOT NULL
);
COMMENT ON TABLE "archive_rate_gate" IS 'The next moment any worker may send a request to one archive host.';
        CREATE TABLE IF NOT EXISTS "crawl_link_request" (
    "id" BIGSERIAL NOT NULL PRIMARY KEY,
    "source_key" VARCHAR(64) NOT NULL,
    "url_key" TEXT NOT NULL,
    "url_key_hash" VARCHAR(40) NOT NULL,
    "url" TEXT NOT NULL,
    "rel" VARCHAR(16) NOT NULL,
    "parent_timestamp" VARCHAR(14),
    "status" VARCHAR(16) NOT NULL,
    "captures_found" INT NOT NULL,
    "attempts" SMALLINT NOT NULL,
    "error" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    CONSTRAINT "uid_crawl_link__source__8bb89f" UNIQUE ("source_key", "url_key_hash")
);
CREATE INDEX IF NOT EXISTS "idx_crawl_link__source__6afe3e" ON "crawl_link_request" ("source_key", "status");
COMMENT ON COLUMN "crawl_link_request"."rel" IS 'DETAIL: DETAIL\nLIST: LIST\nPAGINATION: PAGINATION';
COMMENT ON COLUMN "crawl_link_request"."parent_timestamp" IS 'Capture time of the first page seen linking here (lookup window centre).';
COMMENT ON COLUMN "crawl_link_request"."status" IS 'PENDING: PENDING\nFOUND: FOUND\nNONE: NONE\nFAILED: FAILED';
COMMENT ON TABLE "crawl_link_request" IS 'A link from a recognised page to a URL no enumerated capture matched.';
        ALTER TABLE "llm_decision" ADD "source_key" VARCHAR(64);
        COMMENT ON COLUMN llm_decision."source_key" IS 'Which source''s induction asked (NULL for decisions recorded before this column).';
        CREATE INDEX IF NOT EXISTS "idx_llm_decisio_source__7103ed" ON "llm_decision" ("source_key");
        -- Attribute earlier decisions that became rules; rejected ones stay unknown.
        UPDATE "llm_decision" AS d SET "source_key" = g."source_key"
            FROM "rule_node" AS n JOIN "rule_graph" AS g ON g."id" = n."graph_id"
            WHERE n."llm_decision_id" = d."id" AND d."source_key" IS NULL;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_llm_decisio_source__7103ed";
        ALTER TABLE "llm_decision" DROP COLUMN "source_key";
        DROP TABLE IF EXISTS "archive_rate_gate";
        DROP TABLE IF EXISTS "crawl_link_request";"""


MODELS_STATE = (
    "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"
    "WUaiiZBsX6Z5GlHyKV7863+Z/fp/3mrNLg=="
)
