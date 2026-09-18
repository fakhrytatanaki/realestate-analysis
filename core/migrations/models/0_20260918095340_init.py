from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "scrape_run" (
    "id" UUID NOT NULL PRIMARY KEY,
    "source_key" VARCHAR(64) NOT NULL,
    "trigger" VARCHAR(16) NOT NULL,
    "status" VARCHAR(16) NOT NULL,
    "started_at" TIMESTAMPTZ NOT NULL,
    "finished_at" TIMESTAMPTZ,
    "documents_fetched" INT NOT NULL,
    "listings_created" INT NOT NULL,
    "listings_updated" INT NOT NULL,
    "errors" INT NOT NULL,
    "error_message" TEXT
);
CREATE INDEX IF NOT EXISTS "idx_scrape_run_source__079698" ON "scrape_run" ("source_key");
CREATE INDEX IF NOT EXISTS "idx_scrape_run_status_9c01c8" ON "scrape_run" ("status");
COMMENT ON COLUMN "scrape_run"."trigger" IS 'SCHEDULED: SCHEDULED\nMANUAL: MANUAL\nBACKFILL: BACKFILL';
COMMENT ON COLUMN "scrape_run"."status" IS 'RUNNING: RUNNING\nSUCCESS: SUCCESS\nPARTIAL: PARTIAL\nFAILED: FAILED';
COMMENT ON TABLE "scrape_run" IS 'One execution of the ingestion pipeline for a single source.';
CREATE TABLE IF NOT EXISTS "raw_document" (
    "id" UUID NOT NULL PRIMARY KEY,
    "source_key" VARCHAR(64) NOT NULL,
    "external_id" VARCHAR(255),
    "kind" VARCHAR(16) NOT NULL,
    "status" VARCHAR(16) NOT NULL,
    "blob_key" VARCHAR(512) NOT NULL,
    "blob_uri" VARCHAR(1024) NOT NULL,
    "content_type" VARCHAR(128) NOT NULL,
    "size_bytes" INT NOT NULL,
    "sha256" VARCHAR(64) NOT NULL,
    "source_url" VARCHAR(2048),
    "meta" JSONB NOT NULL,
    "fetched_at" TIMESTAMPTZ NOT NULL,
    "parsed_at" TIMESTAMPTZ,
    "parse_error" TEXT,
    "attempts" INT NOT NULL,
    "scrape_run_id" UUID REFERENCES "scrape_run" ("id") ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS "idx_raw_documen_source__a8e585" ON "raw_document" ("source_key");
CREATE INDEX IF NOT EXISTS "idx_raw_documen_status_4585ba" ON "raw_document" ("status");
CREATE INDEX IF NOT EXISTS "idx_raw_documen_sha256_feb0e0" ON "raw_document" ("sha256");
CREATE INDEX IF NOT EXISTS "idx_raw_documen_source__89a053" ON "raw_document" ("source_key", "status");
COMMENT ON COLUMN "raw_document"."kind" IS 'JSON: JSON\nHTML: HTML\nXML: XML\nSCREENSHOT: SCREENSHOT\nOTHER: OTHER';
COMMENT ON COLUMN "raw_document"."status" IS 'PENDING: PENDING\nPARSED: PARSED\nFAILED: FAILED\nSKIPPED: SKIPPED';
COMMENT ON COLUMN "raw_document"."sha256" IS 'Indexed so an identical re-fetch can be recognised without re-parsing.';
COMMENT ON TABLE "raw_document" IS 'Metadata for one archived payload; the bytes live in the blob store.';
CREATE TABLE IF NOT EXISTS "listing" (
    "id" UUID NOT NULL PRIMARY KEY,
    "source_key" VARCHAR(64) NOT NULL,
    "external_id" VARCHAR(255) NOT NULL,
    "url" VARCHAR(2048),
    "title" VARCHAR(512) NOT NULL,
    "description" TEXT,
    "listing_type" VARCHAR(16) NOT NULL,
    "property_type" VARCHAR(32) NOT NULL,
    "price" DECIMAL(14,2),
    "currency" VARCHAR(3) NOT NULL,
    "price_type" VARCHAR(16) NOT NULL,
    "installment_plan" JSONB,
    "area_sqm" DECIMAL(12,2),
    "bedrooms" SMALLINT,
    "bathrooms" SMALLINT,
    "location_name" VARCHAR(512) NOT NULL,
    "country_code" VARCHAR(2),
    "city" VARCHAR(128),
    "district" VARCHAR(128),
    "latitude" DECIMAL(9,6),
    "longitude" DECIMAL(9,6),
    "attributes" JSONB NOT NULL,
    "content_hash" VARCHAR(64) NOT NULL,
    "is_active" BOOL NOT NULL,
    "listed_at" TIMESTAMPTZ,
    "first_seen_at" TIMESTAMPTZ NOT NULL,
    "last_seen_at" TIMESTAMPTZ NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "raw_document_id" UUID REFERENCES "raw_document" ("id") ON DELETE SET NULL,
    CONSTRAINT "uid_listing_source__29ebcc" UNIQUE ("source_key", "external_id")
);
CREATE INDEX IF NOT EXISTS "idx_listing_source__b65e50" ON "listing" ("source_key");
CREATE INDEX IF NOT EXISTS "idx_listing_listing_d325e3" ON "listing" ("listing_type");
CREATE INDEX IF NOT EXISTS "idx_listing_propert_fdebc0" ON "listing" ("property_type");
CREATE INDEX IF NOT EXISTS "idx_listing_price_f5deac" ON "listing" ("price");
CREATE INDEX IF NOT EXISTS "idx_listing_country_eddf04" ON "listing" ("country_code");
CREATE INDEX IF NOT EXISTS "idx_listing_city_836bae" ON "listing" ("city");
CREATE INDEX IF NOT EXISTS "idx_listing_is_acti_ce14e1" ON "listing" ("is_active");
CREATE INDEX IF NOT EXISTS "idx_listing_listed__c34f69" ON "listing" ("listed_at");
CREATE INDEX IF NOT EXISTS "idx_listing_latitud_b89acc" ON "listing" ("latitude", "longitude");
CREATE INDEX IF NOT EXISTS "idx_listing_listing_2e01b8" ON "listing" ("listing_type", "property_type");
CREATE INDEX IF NOT EXISTS "idx_listing_country_16e848" ON "listing" ("country_code", "city");
COMMENT ON COLUMN "listing"."source_key" IS '``(source_key, external_id)`` is the idempotency key for upserts.';
COMMENT ON COLUMN "listing"."listing_type" IS 'RENT: RENT\nSALE: SALE';
COMMENT ON COLUMN "listing"."property_type" IS 'APARTMENT: APARTMENT\nVILLA: VILLA\nTOWNHOUSE: TOWNHOUSE\nHOUSE: HOUSE\nSTUDIO: STUDIO\nLAND: LAND\nOFFICE: OFFICE\nSHOP: SHOP\nWAREHOUSE: WAREHOUSE\nOTHER: OTHER';
COMMENT ON COLUMN "listing"."price_type" IS 'TOTAL: TOTAL\nPER_MONTH: PER_MONTH\nPER_WEEK: PER_WEEK\nPER_NIGHT: PER_NIGHT\nPER_YEAR: PER_YEAR\nPER_SQM: PER_SQM\nINSTALLMENT: INSTALLMENT\nON_REQUEST: ON_REQUEST';
COMMENT ON COLUMN "listing"."attributes" IS 'Source-specific extras that do not warrant a column.';
COMMENT ON COLUMN "listing"."content_hash" IS 'sha256 over the business fields; see ``realestate.domain.hashing``.';
COMMENT ON TABLE "listing" IS 'An aggregated classified ad.';
CREATE TABLE IF NOT EXISTS "aerich" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "version" VARCHAR(255) NOT NULL,
    "app" VARCHAR(100) NOT NULL,
    "content" JSONB NOT NULL
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """


MODELS_STATE = (
    "eJztXG1z4jgS/isqf5qtyqQS8jJz3qurIomz4YaXDDg7c7tsOYrdgCpGYiR5Mtze/PcryQ"
    "b8SjBhWIflCxYtPcI8LUut7pb/NMbMA18cNomQhA5b6pthoj8NisdgmCi3/gAZeDJZ1CqB"
    "xA++BvhhS93oQUiOXWmYaIB9AQfI8EC4nEwkYVQ1rlOEh0MOQyzBQ66PhSADAh7C3qHqwW"
    "OukFx190zjPu3TJnOx6hgRgTygjI+xTwR4iFHJkBwB4uzJRPAV+BQJFnAX0AhTT6BAIIwG"
    "HOCthG+yT338AD7C1EOCjUGSMagGE0aoPNDigY+lBEroEBGJHgEmQv/AiEkkAHN3hCZYjp"
    "BkfYqRIHToA9IU6b8VUPIlAEeyIcgRcMNEv/9uhHfkPMJUNYFvEjjFvkM8448/DpBBqAff"
    "QIRtfSyJDDxQLX1Gh+GXPw7Q7zP+HTmd6OoJZxPgchoKdBOXBVTyqeOysAeXyGn4I5NHZ0"
    "DA9xJDgHiqkZaHnZjIuLtrXF3rlkpLD47L/GBMF60nUzlidN48CIh3qDCqbggUuNJhbFzQ"
    "wPejETQThSQZJpI8gDkB3kLgwQAHvhpdxj8HAXW17vUvqY/Tf0W3FmvmOO2O7fQs23GMzG"
    "BUt5AacpHIZVQNZEKlov/P72G/C0K01FA/cHlT7745Of9JU8CEHHJdqekyvmsgljiEalUu"
    "WE6qP8n25QjzfLaTqBTrQvJ1+J4JlhA+42p1do37+zeLmz1AsfH90/29emTV80M8GE+YBO"
    "pO0SNM0YBxFEwEcCmy88FGekzp1hjjb44PdChHhonOT5fo+td6V6v7/FSrm3HshjNgO6qp"
    "6Sql9YWW4491CTWnYD9Kz4tZen1Fr/wYJamunZ2twHXt7KyQbF2XZDvgfhmWo+ZrsRs9Ih"
    "Ul9+j0/SrsHp2+L6ZXVyb5lUT6UIbhOWAHR/DZcW0Fjs+Oa4UU67okw/E7y/BswzeZz3MK"
    "tgsjegm3tvXZVj2Phfjixyl906p/1myPp1FNs9P+ZdY8poLLZucixXzajsoOcYsGY01/gw"
    "qJqQsZNWRssQqvz12rbZtIffZpr960TKQ+04vuKg/C8fkKz8HxeeFjoKqSukjasGsqI9NJ"
    "lbVRv6137ZZWybzYp782ms26ifSlT+3Op/ZN565nmWhe7NNIEn3r2XdXjY6JwmufNuvtKx"
    "Opzz7tXF83Li0Thdc+7d10bk2kPvv0U71rRT3Ni33asW+sron0ZZ2RcbLKDHlSPEGeZObH"
    "CSduzoi4ApeMsZ8/Oc4xqQHghaDDCLyRKXIDY2ETM+SVddlo1Ztvjk8PQnbFF59IiNN+ep"
    "Tm1g04V3ZzmQU+jtnBNf5klfFbPHxzR+8LJ7V4D9tj3LA7dr1ZcocdgtRsZdebfXprdZ1W"
    "p23fmGheDKWfLOtDKFSlUNZu/HJjh0JdDKX/serdUKhKoaz3sRWKeh9bfdpo9+x6sxnOpb"
    "EvfdppO13r453Vs020KFdjxSNK4b4/BiqdiY9zjL9/9zrtAg9MDjY91RFXov8hZZ/slCWo"
    "SFluCaaNvpSjRnWQtgQxB+yIL+OSy0wctoWVpioqmC81tRJLzQN4nLGxyFLcG2Pfb9CCfU"
    "4cl+KY0Fc4tI2huom3J7V35++Vaxe00fTu/N0SwnuterPZaNtpSrEcrcdpHLgnNbkpjEIM"
    "jhaUsIsywB00jn6IAyQdq1jZEE3hfpALZJv2fcqnt4pDr9ibl+FZxYDK8Bu13zlej2uruE"
    "qPa8WeUl2XcuMRdQ+uLMNwHLMLDrwt0BwPi5aw1OKwv6Gl9o+D85UNtUWwuSTFcdye46Uc"
    "Yyk5eQgk5Jhuxbu+JOpF+731PAKLGPxDQHxJqDhUP1s2DG/0dEz3rZiASwbEVXFdjlUwF0"
    "vkMUSZRE+Yc0wlwij8/9nY8Lqd/MVbTZdRqbbtIyxG5aydJK7K1qUhRrh2do7YV+A6RP8Q"
    "CEJBCBSmRfyMBAC6v+eAfRASSzj02BgTeqj+HaHD+/usvjfU55azAYhwsCvJ15zZ9IIxHz"
    "At8PDEcSldPzC2yhS6ThxjJtjuFHrR6TQTD9pFIx2+u2tdWN03x6kZNmf7RoQEz8E5dtgV"
    "ljrRq2D5igPTc2uEPJwVXp0BvCy02mhZPbveuk1o4KpuW6qmZsRjqzPpm7Sjc94J+tSwb5"
    "D6in7rtK30jDhvZ/9mqHvCgWQOZU8O9uI8zsQzUULFA8KFdAQAXUPNGfAGVF25PTsH7HWo"
    "P40G2StRfvQ8LNW9j9dXfRq71/xr0rzLAa83syeRe62/Jq0HE29NrSeRe61XRes5S3x09w"
    "utc/zkeMwNdHyzXG56DvQFieqvxwvxTFq6yvwfPOZmpccZyzJ9zTiQIf0A00yWQorX6BhJ"
    "Fz9dRb3Nj5K8SpYX0sUtKq5mpyfyhhqjjgc+hJuTnmWj9l2zaWj2H7D7+IS55yTUoGpYja"
    "Uk87bZqnFtnJZgioeaJfV31M0XaSLn0E+etooP/qSHyvOnf1ogsToPoZPyGQWkDs6Qr+Ch"
    "CZ76DHs/h3v5qQSBfPIVEKGhxGcPSEjGIesJ2FSnOWd0MsdwkqcvlF8hEPuDNPuDNJXY1e"
    "/wUZaKrQlbOMnySGgBy8/nCM6wlXYKK2+4idRnn97YraaJ1GefflbFz6rUu+xaVrt307FN"
    "tCi/PCl588l70UKwproW6K3NQsat1b5qtH8pG6qJYCrbUhf69Lbe7VlXJgqvfXpdbzTV9/"
    "Dap70PjdtbJYgK1VCYWvrLLhhxTJWfrCol82jOAk5K8xxhdpDn46PaKiuzalY8oHVlfiSx"
    "OLl8eSRx+ynlrzv1RJD/gqM3FVm2C1Mtk6C1ci0rR3WYbFk7Pn13+v7k/HSecTmXLEu7zI"
    "bswsBuKUt+jqiwFW80wt6QYAhTdUqdSuJiH3F4OwDpjpCLKXoAxMFlQ6pfY/FE5IgFUjWZ"
    "YK5eKpHd/W6u2y3vJqL9V8kT20nUDu4lftDB7TFInOW5OLlo1v71phVV9nSJfizXCmIkkf"
    "sgRlWCGKuErtRMu5bSE8CtZKJUJYhRJRWvlIqiVeUA54xntVz87oYUbBcWtW2/u0G9nGs8"
    "kWVM8Thke4b40Wuxwl2OJ+DwgJaMuGaA+3jr0njrgq8NRFt7urNuQHc81poZZKUjrZnXvu"
    "RMHRcR8vpDF3xc8Gqdgtcl7gbv339kQDo1WHPC0dnhXByMTj5Hz4eiOxQQfAM30DsNNgjf"
    "HUeHILRgQibgEwo6qDx/oWO4+8xuwl/a2fOh532IeR9i/huEmCUnwyHkWM+rRdJi8Cq7sY"
    "3e5Y11dafjZPNin7bq7Tv1tpTw2qcX9csP141m00SzUjWCZ68u2rmGhrp37bYOdEaFPu3d"
    "XV5avZ6JooIOfdoNpbCokA5+VkZbfL2c2SRy726qii9iFXfTgFAi1vQyJqF7l1OVXU6z/E"
    "vhRM7hEh6QXOzeFZJ3hlBtEJ3o5EgJhvOge4ILCY4OaaxDcAy6JzhNsHYtl/GNLgB7MnPJ"
    "dMYgBB7mJNoUu/kzwL2jfzVHf8Z7uopbb764vcyvt7vnV36ob68OnLijPJ9eVLPUl4cXbZ"
    "7z4xXTsGGfWuFcmetSy5kno2HxMl9a5fO4il1oX4GL3JfaF/vPYpAqu3OqdGxAPVQlGI6a"
    "7yC7x0dHK2XZHi1Jss2+IDvMlc0yXJxIFYNsP5fqr1n4N5Y1VWLh3/xi9v3/9NYJTg=="
)
