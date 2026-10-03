from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "crawl_frontier" ADD "next_retry_at" TIMESTAMPTZ;
        ALTER TABLE "crawl_frontier" ADD "claimed_at" TIMESTAMPTZ;
        COMMENT ON COLUMN "crawl_frontier"."status" IS 'DISCOVERED: DISCOVERED\nUNROUTED: UNROUTED\nQUEUED: QUEUED\nFETCHING: FETCHING\nDEFERRED: DEFERRED\nSKIPPED: SKIPPED\nFETCHED: FETCHED\nFAILED: FAILED';
        ALTER TABLE "listing_observation" ADD "graph_version" INT;
        ALTER TABLE "listing_observation" ADD "snapshot" JSONB;
        ALTER TABLE "listing_observation" ADD "template_key" VARCHAR(128);
        ALTER TABLE "raw_document" ADD "graph_version" INT;
        ALTER TABLE "raw_document" ADD "parse_report" JSONB;
        ALTER TABLE "scrape_run" ADD "stats" JSONB NOT NULL DEFAULT '{}'::jsonb;
        COMMENT ON COLUMN "scrape_run"."trigger" IS 'SCHEDULED: SCHEDULED\nMANUAL: MANUAL\nBACKFILL: BACKFILL\nREPLAY: REPLAY';
        COMMENT ON COLUMN crawl_frontier."next_retry_at" IS 'A retryable failure waits in QUEUED until then.';
COMMENT ON COLUMN crawl_frontier."claimed_at" IS 'When a fetch claimed the row (status FETCHING); stale claims are released.';
COMMENT ON COLUMN listing_observation."snapshot" IS 'The complete normalised advert as this capture showed it, so history is';
COMMENT ON COLUMN raw_document."graph_version" IS 'Extraction graph version that last parsed this document (archive';
COMMENT ON COLUMN raw_document."parse_report" IS 'What that parse saw: template, page kind, items, problems.';
COMMENT ON COLUMN scrape_run."stats" IS 'Quality counters beyond success/failure: documents recognised, OTHER,';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "scrape_run" DROP COLUMN "stats";
        COMMENT ON COLUMN "scrape_run"."trigger" IS 'SCHEDULED: SCHEDULED\nMANUAL: MANUAL\nBACKFILL: BACKFILL';
        ALTER TABLE "raw_document" DROP COLUMN "graph_version";
        ALTER TABLE "raw_document" DROP COLUMN "parse_report";
        ALTER TABLE "crawl_frontier" DROP COLUMN "next_retry_at";
        ALTER TABLE "crawl_frontier" DROP COLUMN "claimed_at";
        COMMENT ON COLUMN "crawl_frontier"."status" IS 'DISCOVERED: DISCOVERED\nUNROUTED: UNROUTED\nQUEUED: QUEUED\nDEFERRED: DEFERRED\nSKIPPED: SKIPPED\nFETCHED: FETCHED\nFAILED: FAILED';
        ALTER TABLE "listing_observation" DROP COLUMN "graph_version";
        ALTER TABLE "listing_observation" DROP COLUMN "snapshot";
        ALTER TABLE "listing_observation" DROP COLUMN "template_key";"""


MODELS_STATE = (
    "eJztXetz2jy6/1c0fNm+O2lObr0sPXNmaOI0nBJIgbTdXb/jKrYCOjWSK8lJs7v93888sg"
    "FfiU2AGOovWMh6hPnp4ueufzcm3CGu3D8V+N499YXk4hJqGk307wbDE9Jootw2e6iBPW/e"
    "AioUvnE1kQ2tLVs31y1vpBLYVo0musWuJHuo4RBpC+opyhlQ9In0JwR5nDKF+C3ijCDC/A"
    "kRGJogaXOP7CGyP9pH377Zzs/m0cHh8bdv+9C9w22pBGWjJ/fkM/rDJ5biI6LGRDSa6J//"
    "bEjuC5tY38kDNNH0jT//3EMNyhzyk0hoBV+979YtJa4Tw486QKTrLfXg6bo2U+e6ITz7jW"
    "Vz15+weWPvQY05m7WmTEHtiDD4CwS6V8IHDJnvuiHkU1iDPzBvEjxihMYht9h3YSSAOniA"
    "eV3Dsrq9oTUwhpbVSI3SlCICd1hlcwYjTJmS+t+P4BFeHh2evDl5e/z65O0eaujHnNW8+R"
    "X89ByYgFDD0x02fun7WOGghcZ4Dmp8ROLgno6xyEY3TpVAWSqRRHmK6SKYpxVznOcTfBNA"
    "T/BPyyVspMaNJnp9sgDVz63+6UWr/+L1yR/wg1xgO1is3fDOkb4FwEeA1pO9DMZTghreAv"
    "AKvVdlz+Mh+ZmzS8SplgI63AyqgvMCXIfG1yH0PJHyhxvF88Vl66uGevIQ3un0uh+mzSP4"
    "n3Z67xO4O5xlzOr3nLsEs2zQpyQJuG84d9c1sWc1m0X8fa/XiSH+vp2E9PryvdF/cajhlz"
    "9cqkh0357j7HsOYGJhlUb7DCui6IRkwx2nTIDuhKT708I2bi2CYKfH3IdwNS5aAu1LYzBs"
    "XV7FRuWsNTTgzlFsDUxrX7xObEOzTtCX9vACwVf0j17X0PByqUZC/+K83fAfDXgm7CtuMX"
    "5vYSeK0bR6+vTA/9x+j7ysoeIG29/vsXCs1B1+xPPapm9NjibJGszwSI8ZgAuPGWVTzwVn"
    "ipJHmNl4qwLs7G1IUIyh7TGCsLDH9I44yMae8gVBmDnofowVUmOCdK9EIIfY1CEOwjfcV4"
    "iqNEv7xL4KMLW+cKFojbEc679OJ0QqPPFSjG6KHVZY+RouT1AuqHpo/LmHFvb/Zylm+T0d"
    "/S788t+Ojo6P3xwdHL9+++rkzZtXbw9mjHP61iIO+n37A2zGsQ2g5qorw/aFq6EMzxchqT"
    "LEjdOzry+lenAJsjHjjNrYRd/JwzvkcjZCLwizOexPLYFvqI2k64/kH3tIchQ+B7qjGMEu"
    "kd4FV9v1M3OgyQ236GpL0lV5Miy53k4OCqy3k4Pc9Qa34mDP32YlkI4R7SDMh0W2tcP8be"
    "0wta1xQUeUYdfyhVtmb0vS7QTYGxdp6YjIDDErf4LPKXYC8LWrwmaMbhpgg/kTDXKbSYWZ"
    "TdJc0pxN3hjYjbP24LT32egbZ42Sr/E5ZRPNyya77vZ710OonZZM9unauIaa4Gqyc2N4et"
    "HufmiiaclkZ8a50Q96C0smG3xsX11BVVgIKaEmLJjsvNXu6Ap9TbIEhba510W2uaS0HNnm"
    "XicnwkzOSU2FwQS7bq6wEqV7XGRZ1SQ4WK/Icnz05vVMSIEvi8SSwWWr00lriTw8ItZ3yj"
    "KEv2KLK9ZBhfWhjU57MGwi+IQlMWy1O7Ag4Gqy3vDC6DeRvlRjogvuK2Ix7pSyAMSpKjwa"
    "y/JNR2+LAH30Nh9puBeHmtxRh4RTOw70/w563WygozRJBSm1FfoPcqlc277S+O9bn9naon"
    "rjU1dRJvfhZ/+n5KtmFQwVYLSYoUryTgmlJ3SQZKiwUmTiqYw3/uJtPkpXb/PROS5E4A1Q"
    "VC6YEezCJrJpecB2MZ0sZXqJU67A9LKhl+uXMWEIo1ui7DEK/4RWjgt+j14EDPiMJf3jHZ"
    "IKgy4JGkqEBUGCuARL4qRVT6vtusTMqLblR1fFZh0jP5UliBIPS0y8FPH2zL0W0s8NP4pu"
    "MXXBQHOPqZKIslAwQj5T1IVZw9ITbAn6HZ5FtiBLmo3jlLXZuCrDHi7DhaNeOwvs2qhX3F"
    "mgQ6WibJTrJhC7v9BBwA1aNgp5BrQYwqORICOYf8AkSElvKRjtMziPRY1NZrIOtwO3ViqR"
    "QxgXE+xSSRzEmeJTFqWJyB0RDygw1aIxZo5EvgSORhDyUpGfymQuviGu9iqQfKIXCjTQbr"
    "R7uvrWBdGGUTZCVKHvhHhS/8CYKyQJ+CcgD6sxUtxkGEnKRi5BGqKiXgjkpyICDAHUyfA7"
    "cLGiyg/UCmDyC75oZ4MQ/2DNa1cE7hGhHoIK3cTmPgPOwg4VE7b2VCjliXB93T4r4Yfg+9"
    "TZB5plNpzH3REiArj+Jfg4eQ7pW0sqx8EWEt0c9F/fXo+DFfh/NL59ezF/2D0Umd9/fPsG"
    "SxbWD3XIxOOKMPsB7Nnolgvke5IIJdP7wUp63LDlJLqsSwxzgmwHjVRHr14VwPro1atcsP"
    "W9lGdBGZSfYnOtmIolAe7BSRFFLTTLh1ffTDoTKLeUQnxGsIMz+NXhUQGMXx0e5UKs7yVM"
    "2ZEnK6EuTJDtwozetNIwyUctY4xL8WIVfj/3je6wieDTZINWx2gi+KyG9S3Owy5rGU12Uu"
    "XRaF21+sNLPSSzosk+tzudVhPpi8mGvS/di971wGiiWdFkYU34bTC8Pmv3mii4mqzT6p41"
    "EXyarHd+3j41mii4mmxw0btqIvg02ZdW3wh7mhWfbpc9LrJDHudvkMep/dETNMtSeEZsOs"
    "FurvNBtqUwINoPiVeyRW7Sc3oBtmfGafuy1XlxeLJ3lIhUiXoRJswmvhDAN5d5wUdpdvAd"
    "f1xk/uZP38zZ+8RNLdrDBv2phr1hq1NSwg6IYLcatjomuzL61mWvO7xoolkxqP1iGB+DSi"
    "gFdd32h4thUKmLQe3fjVY/qIRSUDf4dBlUDT5dmqzdHQxbnU6wl0a+mKzXtfrGp2sD/FDm"
    "ZXDs+tjtfemCX5cuVOMdSGEKuO6EMGV5Ls5gB/PdI7Jon+QmsT284Xr8IQTBlvwxKfniiZ"
    "Jt4N1TlSGYvXyOSrx8bogjOJ+UdjmJ0i3lclIxXFfodHKD1Xg5TKOENahxMTE0Oli6ogSn"
    "lCLcQXZpLSqRpPWiMGuaoFuTUmSjsZJxLV8RFV++fi+Fc6Zf9wJ8s/25tx/XtXi5OhSewS"
    "4ZpTKn2QWV3gZgjhpKS3BqUbLfkFP7297rwoza3PxcEuIoXY3xQoyxUoLe+IpksG75Ul+c"
    "anvd4hsDbeV9KT1i01tqg6VXYDDvYoUcjhhX6B4LgZlCGAX/P20tXraTZxY1bc4UiO1lg6"
    "OTdFXmLhtyjI9evUb8jghttL/xJWVEShQ4SrxDkhD07Zsg2IU4ZEX2HT7BlO3Dv6NslJX7"
    "bkV9btg/gEoL24relU3JFKNbW16mFA82rdjarEyw/y3lZxkj3IhzdkVMCRV3qkz70t5SIZ"
    "UlCWFLDHOKuPaorcrgF/GjdvHyQ5+krUd+m0Y+WLj8RhJxt9QGn9nB9kTh6DAt4Huwc0cE"
    "8LYSTf8MejFNnAbPrF0iw5RqoVu0/GMfDcckxVOtolOTCX7/F5nkxxB27/GDRILcusQOMr"
    "n91cWKSPXX8De0unRPJ3oDH26TceEQEb0ZBJhRBsk8iIPozkcH6T3qCZM8i75mZio84HU4"
    "2O/4MqvDwXZt1PPCwWI5R/C95XDb124b5YJwMkifEJGzPcrVR+JvUsF22WCnkT7ngtAR+0"
    "geUu5YCVzDeLk+vj8Le5vFzG0lyvPa+SMCVtMwsaypxpnlEJcEOpeBMUTd606n8Ss/sjGS"
    "Ny/CzGWowELq84994uIc7/Z4xGJv3mHRgajaFpc3Er82EBCagi8/NjQL6UfDRK3IgDcKJ5"
    "OWdDQGajjTBKOwK0gPAAecaGVuUwsQ2jsSjalUXDxk55JevqvMIM5I9GuUrX7SESl1rOWq"
    "Yy2fIDGtXljaTETBbyktVT06oCpsUx0eUIcHPLcNuI4NWD424HdwElhySa4h+THDnhzzDM"
    "4h3xUnSrMFgReNIZyjwiceCHEokkkl1PtrvxkqZ+evyDG/B3270mcMhEwyojLFca+oX5Mx"
    "sAAgs6G4gx/+MjMi/B+nDNLRcYQZ4q4TsO1mowqePCOBvbF1R4TMDObO9b5P0e2QB/4TT0"
    "WMZCIgEw+sRWXzpyTparfaQm61tRli1xTShbwpQp1NOSVFnGqVyorqClSP6iZqLf+zaPkj"
    "6rknKviTCfG2c+4+qt2PL96YYv+0NThtnRmN3Nlcm1E2bEZZqz3AnYDmSy40BCTbLLYAuB"
    "PLCZsXV/3rjhBm8p6IJrKxPSZ7CPsOVQjwDbI22lwq5BJnRES21n+pXjIU/qnEjArL7w1d"
    "6fnKutUHZk2mWHiCTzw1Y+XrjIvPawWYjlVhWSFsX2tJigQyRBZAUYCjNDXIBUCe7SxFEZ"
    "5vRbsH71oE3cSWXQLqNOUOYr76hF6C/PAzD+/LV65GSLY3yHEV78K1qC0FkR5nstRxTFGa"
    "LVB3Vxd7fG9BRvI09vmpP6M0G8wH9gzTfS1pP++wm3ny+qLgyxnN2gIv04DParY29LI+A2"
    "uT81rx74RJi5axPMVodub8ttXZnAJ4uJ+xPz+GaUhUg5oKyMY6V7+VlaUsF9Q4UQ1qEtTa"
    "XPe7mOsqcpxQSm+foS3O0u3na4uThoXHtcWXRGHQ/elQVPDmDsNRHeThB5dj512QF+RBEY"
    "lciFOlQXDrjctvELiakLTyeFWdFtElx892CU9Nr5XGz6s03vFjeiqj2nzGg3IqJils4Jyc"
    "pxw4/6Sz5jflXAzKlSaCT5NdDC87TQSfJvsKxa9QGpz2DaM7uOgNm2heruJR9OGLYMnhml"
    "NvbBdqXBnds3b3Q8mNfkoG/tq6YLKrVn9gnDVRcDXZeavdge/B1WSDj+2rK6gIC+Cv3TdO"
    "ex+6bU0X/VaNwQS2oOzLJEpT5VW35A63lqTBGjNf0NI4hzQ7iPPhwVGRtzY0y5/Q+mZ2ME"
    "J+3MriYIRtiFapkolS0n8RSwscJTQmcaLNaUy2yKU8SCBZisufUVSYw2+0g94gogIzOB+T"
    "KWpjFwny8pYoe4xszNANQYLYfMR0eMY9VWPuK2jiYQHH2aYl49V1u+l4nkA2K3lWZJxqB+"
    "WMNR0ZOSEKp3HONyVP29eW/cUmoSWsy3pZLpcbL0ZZa4WrrhWOeTRhIZca9Bjh1iRCXIlB"
    "tkJDPMXk8TG2ShvYE2S78FLbtJkdK4goVGVY8ShJbbpMcuG/Q9Rsw/ipbVnAaujnRuFzB6"
    "cTQJJOFOy+QXj01A6GXoTWpxQz/tQOTTbN2aojrzESvkuQ740Edgj6zvi9RPdjao+n3Ux7"
    "CDKwwmmDJJOXX/HsCHYsQTwuSrmMJum2wEmx8QWGTo+ffnok8X0TTeOX95CHRwSBMn4PUU"
    "Umcg95gt+4ZCLTstrTunpmtlXaAnvEEj4rGS6ZIqyDJRcGS87xSqNcOpJvoDvr+4Wz8FUT"
    "4Ufj+FKT7AnJEMPgy9UkQtwx3GNbQhWyRm4Lbmt1+/FdYjgjku/zE2uw2OHHd4kFwZeNQt"
    "4+LTTysXCAoxGYSap5H53TUbMuMS4o/UIsTf24/07tp/Osfjq3gk/KGlajNLX1qZj1SfHS"
    "KXd4jXHJbDucOVRlyr/5Ik6MqNaer1wM8QTlIvOI3FydRJRkc5qew4Nt0fV4XOZM83xIIy"
    "S18ixbeVaO4YjS1MmaFkvIGqsVCMfAFn+AvnY9nVB0cmUnE3qmeAUYAOwtlFtm9x8XW0bY"
    "Kyq12K4vFZxidYt0+guJGA+EjjFmjkucLGGlGFFmgvq4J3pw5Gkwg9mICE/A2NXZ6islyl"
    "Q55OAZWe3VewJFV0N5z+o5dZXBbnRbn9sfWsM2OMTPyyYzvg77rdOgfl5Obj7P4yUd3ZzK"
    "yPNxsioPS5V8pbcvwKB3ZXRL7umapong02R9Y9DrfIYIgWkpGVtQjXXA7fAAhVK+vgmqWk"
    "xKefpiyEieAemChO5zkmroVuD3dka3UnvRrDyrglTl/cHiVLU7WHl3MEEkd+G0qPIOTFmk"
    "FfZherapXee2+B29mOsTcXdt1DMcm2f+ClXIaBJX0ebpCGNK3Me0hFPtcbHM13Qy8XUHM2"
    "fOhINCdqrrYmSldIWxBNa1nrDWE1ZtB6z1hL+nnrA8l/1U5rpyU3/VqpFt0wUuMdPP+q3z"
    "YRPpi8lgPn82mii4gm5w2O4HqkFdqMZM97Aof15QjKh2fk/sHdzGN2VUgDOCaigAd8q5iv"
    "HMdBb5qqoZQa2lKq+lqlUouyZMryA9aCQVnzPKWoxlwipSfv9bNg0KRaEw7qwCpy53dg+n"
    "datm5qDlaGZiqD6imIGBLKaXaUGoqyJlAk0eJ8jUxcyURSCo14qXSileSmpcdlfVspboh5"
    "1PTtrvQdJR+DTZ+36re3rRRMHVZP3e9dCAm9dDw2RD4/Kq04KKaaka0miQaqCM8DSnqKWn"
    "lUtPXNDR8vrJOfUGjw8aGKFmpcS6AZomgk+TdTqXTdTpXJrs4vqy1W0ifanG6ogedFr27O"
    "w0aa23qYOG6qChqs/QOmho94OGEilNMgTPdNKTfMkznm2lmEsA+Ulsf5qrAE4ZAYd3qSs8"
    "6hGXMqIPKsEIkqu6BAXG3mxHgad0VqdDqMCes62+Abt0bIkSdDQiGV62xXjvCHmlRdbB6Y"
    "Vxdq3jI2ZFk122utetThMFV5O9b51+PG93Ok00LYE59arT+jtYU+FaDQb9dzBy96+7XX2Y"
    "Rlgw2eD69NQYDJooLOjjNYZtGMCwUM0gGKmwWM5aFaesrVVVt1YlQgCpXDJbdZy0Tl1cHQ"
    "/f9DjPEolaYZLxEnJ9Jm0t4KeCgcK0glZovS+BcBZpDXAuwGGswTIAR0hrgDOPDi8THTgn"
    "qMHMBNOaECnxKOPApkfOY48S1r5X5X2vQHooF3k8Jdhew1njk49dqh6QzX2miJDohjxw5i"
    "Dp2zaR8r9uMXV9QZqRtOLzY4P2gsMY91JKrJX0ajKfzWvfBQmwEfmJJ5RBqzvsUmcPOYJ7"
    "HtwPzjaaRh8/V37spfy6ZjCkp18pn6WMc5y3cs1v1mupRQS1x1la4/DOQm0xnrd5TFOcD8"
    "OKtba5b99MpW3GmzecFk/T1lY+uCJfSZsbvpKvoc2PX6mUwnBJ/exaDjuGRVUC4bD5DqJ7"
    "eHBQ6PzPgwXHfx7knP5ZhqOJkGyep3keVvI5Xvyrf5n9+n+8MgVe"
)
