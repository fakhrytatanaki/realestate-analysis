# SpaceEngine landing page

A standalone SvelteKit 2 / Svelte 5 / TypeScript landing page for the upcoming SpaceEngine real estate intelligence platform.

## Development

Requires Node.js 22.12+ and npm.

```sh
cd landing
npm install
npm run dev
```

Open the local URL printed by Vite (normally http://localhost:5173).

```sh
npm run check
npm run build
npm run preview
```

## Production

The project uses `@sveltejs/adapter-node`, including a server-side early-access form.

```sh
npm run build
ORIGIN=https://your-domain.com PORT=3000 npm start
```

## Features

- Responsive ivory and forest-green design, locally hosted fonts and photography.
- Interactive product preview: six sample markets, three chart ranges, city comparisons, and three London heatmap metrics.
- Keyboard-accessible charts, collapsible FAQs, mobile navigation, and reduced-motion support.
- Python, JavaScript, and cURL API examples with clipboard copying.
- Progressively enhanced early-access form with server validation, explicit consent, a honeypot, and success/error states. Works without JavaScript.
- Search and social metadata, custom brand mark, and favicon.

## Early-access submissions

Successful form submissions append a JSON record to `.data/waitlist.jsonl`, outside the public directory. The file contains the email, selected role, consent, and submission timestamp; it is excluded from Git.

Set `WAITLIST_FILE` to an absolute file path to use another location. For deployment, mount persistent storage at that path and restrict its access. Submission collection does not send email automatically; connect the saved records to your mailing or CRM workflow when ready.

The landing page is independent of `../core` and does not modify or consume the Python backend.

## Preview content

All chart, comparison, heatmap, and ticker data is deterministic illustrative data. The map is an SVG neighborhood illustration, not a geographic dataset. Planned features are described as upcoming; no launch date, customers, pricing, or coverage is assumed.

API examples use the reserved `api.spaceengine.example` domain. Replace them with confirmed endpoints and documentation at launch.

## Assets

- DM Sans and DM Serif Display from [Google Fonts](https://fonts.google.com/), hosted locally under `static/fonts`. SIL Open Font License files are included alongside them.
- Building photograph from [Unsplash](https://images.unsplash.com/photo-1486406146926-c627a92ad1ab), hosted locally under `static/images`.
- Icons from `@lucide/svelte`.
