# SpaceEngine app

The signed-in SpaceEngine web app: accounts plus historical price trends by
region, drawn from the core API. SvelteKit 2 · Svelte 5 · TypeScript ·
LayerChart. It shares its design language with `../landing` (tokens in
`src/lib/styles/tokens.css`).

## Run it

Needs Node 22.12+ and the core API (`../core`) with its database migrated.

```bash
# terminal 1, from core/
./scripts/migrate.sh && ./scripts/dev.sh        # API on :8000

# terminal 2, from main_frontend/
npm install
cp .env.example .env                            # API_BASE_URL=http://127.0.0.1:8000
npm run dev                                     # http://localhost:5173
```

Create an account at `/signup`; `/trends` needs a session.

```bash
npm run check      # svelte-check
npm run build && ORIGIN=https://app.example.com PORT=3000 npm start
```

## How auth works

The browser never talks to the core API. Every call goes through the SvelteKit
server (`src/lib/server/api.ts`), which forwards the session token as
`Authorization: Bearer`. The token comes from an httpOnly, `SameSite=Lax`
cookie (`se_session`) set at login. As a result:

- page scripts cannot read the token;
- the API needs no CORS;
- SvelteKit's origin check covers CSRF for the form actions.

`hooks.server.ts` resolves the user once per request via `GET /auth/me`, and
`(app)/+layout.server.ts` redirects anonymous visitors to `/login?next=…`.
`next` is only honoured for same-site paths. Logout deletes the session in the
API as well as the cookie.

## The trends page

All of the page's state lives in the URL: `region` (repeatable, `City` or
`City/District`), `type`, `metric`, `interval`, `ptype`, `from` and `to`. Views
are therefore shareable and work with the back button. The server load fetches
`/markets/regions`, then makes one `/markets/trends` call per region in
parallel.

Chart behaviour:

- Each series is the median asking price per period. A single region also
  shows its interquartile band.
- Periods with fewer than 5 adverts, or with no data at all, are drawn as gaps
  rather than interpolated across.
- Hover for a tooltip showing the sample size.
- Drag across the chart to zoom: the brush selection becomes the new
  `from`/`to`.
- "View as table" lists every figure.

The four series colours re-step the landing's hues to pass colour-vision-deficiency
separation checks. Identity never relies on colour alone, because the legend,
chips and tiles also carry it.
