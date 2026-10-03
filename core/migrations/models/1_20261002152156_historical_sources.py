from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "crawl_cursor" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "source_key" VARCHAR(64) NOT NULL,
    "scope" VARCHAR(64) NOT NULL,
    "resume_key" TEXT,
    "done" BOOL NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    CONSTRAINT "uid_crawl_curso_source__a918ac" UNIQUE ("source_key", "scope")
);
COMMENT ON TABLE "crawl_cursor" IS 'Resume point of one enumeration scope, e.g. ``cdx:2013``.';
        CREATE TABLE IF NOT EXISTS "crawl_frontier" (
    "id" BIGSERIAL NOT NULL PRIMARY KEY,
    "source_key" VARCHAR(64) NOT NULL,
    "url_key" TEXT NOT NULL,
    "url_key_hash" VARCHAR(40) NOT NULL,
    "timestamp" VARCHAR(14) NOT NULL,
    "original_url" TEXT NOT NULL,
    "digest" VARCHAR(64) NOT NULL,
    "status" VARCHAR(16) NOT NULL,
    "priority" SMALLINT NOT NULL,
    "page_kind" VARCHAR(16),
    "route_node" VARCHAR(128),
    "evidence" JSONB NOT NULL,
    "attempts" SMALLINT NOT NULL,
    "error" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    CONSTRAINT "uid_crawl_front_source__f5a480" UNIQUE ("source_key", "url_key_hash", "timestamp")
);
CREATE INDEX IF NOT EXISTS "idx_crawl_front_source__8a9d52" ON "crawl_frontier" ("source_key", "status", "priority");
CREATE INDEX IF NOT EXISTS "idx_crawl_front_source__aba7e2" ON "crawl_frontier" ("source_key", "url_key_hash");
COMMENT ON COLUMN "crawl_frontier"."url_key" IS 'CDX-style canonical key; long (encoded Arabic slugs), so indexed via hash.';
COMMENT ON COLUMN "crawl_frontier"."status" IS 'DISCOVERED: DISCOVERED\nUNROUTED: UNROUTED\nQUEUED: QUEUED\nDEFERRED: DEFERRED\nSKIPPED: SKIPPED\nFETCHED: FETCHED\nFAILED: FAILED';
COMMENT ON COLUMN "crawl_frontier"."page_kind" IS 'LIST: LIST\nDETAIL: DETAIL\nOTHER: OTHER';
COMMENT ON TABLE "crawl_frontier" IS 'One archived capture and what the crawler decided about it.';
        CREATE TABLE IF NOT EXISTS "llm_decision" (
    "id" UUID NOT NULL PRIMARY KEY,
    "task" VARCHAR(64) NOT NULL,
    "input_fp" VARCHAR(64) NOT NULL,
    "model" VARCHAR(128) NOT NULL,
    "prompt_version" VARCHAR(32) NOT NULL,
    "request" JSONB NOT NULL,
    "response" JSONB,
    "raw_text" TEXT NOT NULL,
    "valid" BOOL NOT NULL,
    "error" TEXT,
    "tokens_in" INT NOT NULL,
    "tokens_out" INT NOT NULL,
    "latency_ms" INT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_llm_decisio_task_df6cb8" ON "llm_decision" ("task", "input_fp", "model", "prompt_version");
COMMENT ON TABLE "llm_decision" IS 'One model answer: cache, audit trail and cost ledger.';
        CREATE TABLE IF NOT EXISTS "rule_gap" (
    "id" UUID NOT NULL PRIMARY KEY,
    "source_key" VARCHAR(64) NOT NULL,
    "domain" VARCHAR(16) NOT NULL,
    "fingerprint" VARCHAR(512) NOT NULL,
    "status" VARCHAR(16) NOT NULL,
    "occurrences" INT NOT NULL,
    "samples" JSONB NOT NULL,
    "attempts" INT NOT NULL,
    "last_error" TEXT,
    "resolved_version" INT,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    CONSTRAINT "uid_rule_gap_source__2c0404" UNIQUE ("source_key", "domain", "fingerprint")
);
CREATE INDEX IF NOT EXISTS "idx_rule_gap_status_cb4ef2" ON "rule_gap" ("status");
COMMENT ON COLUMN "rule_gap"."domain" IS 'NAVIGATION: NAVIGATION\nEXTRACTION: EXTRACTION';
COMMENT ON COLUMN "rule_gap"."status" IS 'OPEN: OPEN\nRESOLVED: RESOLVED\nFAILED: FAILED';
COMMENT ON TABLE "rule_gap" IS 'A cluster of inputs no rule handled.';
        CREATE TABLE IF NOT EXISTS "rule_graph" (
    "id" UUID NOT NULL PRIMARY KEY,
    "source_key" VARCHAR(64) NOT NULL,
    "domain" VARCHAR(16) NOT NULL,
    "version" INT NOT NULL,
    "status" VARCHAR(16) NOT NULL,
    "parent_id" UUID,
    "vocab" JSONB NOT NULL,
    "notes" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL,
    CONSTRAINT "uid_rule_graph_source__b37b80" UNIQUE ("source_key", "domain", "version")
);
CREATE INDEX IF NOT EXISTS "idx_rule_graph_status_312af1" ON "rule_graph" ("status");
COMMENT ON COLUMN "rule_graph"."domain" IS 'NAVIGATION: NAVIGATION\nEXTRACTION: EXTRACTION';
COMMENT ON COLUMN "rule_graph"."status" IS 'DRAFT: DRAFT\nACTIVE: ACTIVE\nRETIRED: RETIRED';
COMMENT ON TABLE "rule_graph" IS 'One immutable version of a rule graph.';
        CREATE TABLE IF NOT EXISTS "listing_observation" (
    "id" UUID NOT NULL PRIMARY KEY,
    "observed_at" TIMESTAMPTZ NOT NULL,
    "price" DECIMAL(14,2),
    "currency" VARCHAR(3) NOT NULL,
    "price_type" VARCHAR(16) NOT NULL,
    "content_hash" VARCHAR(64) NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "listing_id" UUID NOT NULL REFERENCES "listing" ("id") ON DELETE CASCADE,
    "raw_document_id" UUID REFERENCES "raw_document" ("id") ON DELETE SET NULL,
    CONSTRAINT "uid_listing_obs_listing_296f06" UNIQUE ("listing_id", "observed_at", "content_hash")
);
CREATE INDEX IF NOT EXISTS "idx_listing_obs_observe_028178" ON "listing_observation" ("observed_at");
COMMENT ON COLUMN "listing_observation"."price_type" IS 'TOTAL: TOTAL\nPER_MONTH: PER_MONTH\nPER_WEEK: PER_WEEK\nPER_NIGHT: PER_NIGHT\nPER_YEAR: PER_YEAR\nPER_SQM: PER_SQM\nINSTALLMENT: INSTALLMENT\nON_REQUEST: ON_REQUEST\nUNKNOWN: UNKNOWN';
COMMENT ON TABLE "listing_observation" IS 'One sighting of a listing in one state: the price history.';
        CREATE TABLE IF NOT EXISTS "rule_edge" (
    "id" UUID NOT NULL PRIMARY KEY,
    "from_key" VARCHAR(128) NOT NULL,
    "to_key" VARCHAR(128) NOT NULL,
    "condition" JSONB NOT NULL,
    "priority" INT NOT NULL,
    "position" INT NOT NULL,
    "graph_id" UUID NOT NULL REFERENCES "rule_graph" ("id") ON DELETE CASCADE
);
COMMENT ON TABLE "rule_edge" IS 'A guarded transition of a rule graph version.';
        CREATE TABLE IF NOT EXISTS "rule_node" (
    "id" UUID NOT NULL PRIMARY KEY,
    "key" VARCHAR(128) NOT NULL,
    "kind" VARCHAR(16) NOT NULL,
    "action" JSONB NOT NULL,
    "origin" VARCHAR(16) NOT NULL,
    "llm_decision_id" UUID,
    "position" INT NOT NULL,
    "graph_id" UUID NOT NULL REFERENCES "rule_graph" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_rule_node_graph_i_e7c5d4" UNIQUE ("graph_id", "key")
);
COMMENT ON COLUMN "rule_node"."kind" IS 'ROOT: ROOT\nBRANCH: BRANCH\nROUTE: ROUTE\nTEMPLATE: TEMPLATE';
COMMENT ON COLUMN "rule_node"."origin" IS 'SEED: SEED\nLLM: LLM\nHUMAN: HUMAN';
COMMENT ON TABLE "rule_node" IS 'A state of a rule graph version.';
        ALTER TABLE "listing" ADD "last_observed_at" TIMESTAMPTZ;
        ALTER TABLE "listing" ADD "first_observed_at" TIMESTAMPTZ;
        COMMENT ON COLUMN "listing"."price_type" IS 'TOTAL: TOTAL\nPER_MONTH: PER_MONTH\nPER_WEEK: PER_WEEK\nPER_NIGHT: PER_NIGHT\nPER_YEAR: PER_YEAR\nPER_SQM: PER_SQM\nINSTALLMENT: INSTALLMENT\nON_REQUEST: ON_REQUEST\nUNKNOWN: UNKNOWN';
        COMMENT ON COLUMN "raw_document"."status" IS 'PENDING: PENDING\nPARSED: PARSED\nFAILED: FAILED\nSKIPPED: SKIPPED\nUNRECOGNISED: UNRECOGNISED';
        COMMENT ON COLUMN listing."first_observed_at" IS 'When the advert was observed (capture time for archive sources). The';
        CREATE INDEX IF NOT EXISTS "idx_listing_last_ob_cd687b" ON "listing" ("last_observed_at");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_listing_last_ob_cd687b";
        ALTER TABLE "listing" DROP COLUMN "last_observed_at";
        ALTER TABLE "listing" DROP COLUMN "first_observed_at";
        COMMENT ON COLUMN "listing"."price_type" IS 'TOTAL: TOTAL\nPER_MONTH: PER_MONTH\nPER_WEEK: PER_WEEK\nPER_NIGHT: PER_NIGHT\nPER_YEAR: PER_YEAR\nPER_SQM: PER_SQM\nINSTALLMENT: INSTALLMENT\nON_REQUEST: ON_REQUEST';
        COMMENT ON COLUMN "raw_document"."status" IS 'PENDING: PENDING\nPARSED: PARSED\nFAILED: FAILED\nSKIPPED: SKIPPED';
        DROP TABLE IF EXISTS "rule_gap";
        DROP TABLE IF EXISTS "llm_decision";
        DROP TABLE IF EXISTS "rule_edge";
        DROP TABLE IF EXISTS "rule_graph";
        DROP TABLE IF EXISTS "listing_observation";
        DROP TABLE IF EXISTS "crawl_frontier";
        DROP TABLE IF EXISTS "crawl_cursor";
        DROP TABLE IF EXISTS "rule_node";"""


MODELS_STATE = (
    "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"
    "/z4l+bU8arxq7+/C9NYrqV"
)
