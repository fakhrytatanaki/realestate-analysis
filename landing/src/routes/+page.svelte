<script lang="ts">
  import { enhance } from '$app/forms';
  import {
    ArrowDown,
    ArrowRight,
    ArrowUpRight,
    Building2,
    ChartNoAxesCombined,
    Check,
    ChevronDown,
    Code2,
    Copy,
    Globe2,
    GraduationCap,
    Layers,
    Menu,
    Plus,
    ShieldCheck,
    Sparkles,
    TrendingUp,
    X
  } from '@lucide/svelte';
  import type { PageProps } from './$types';
  import Logo from '$lib/components/Logo.svelte';
  import MarketDemo from '$lib/components/MarketDemo.svelte';
  import TrendChart from '$lib/components/TrendChart.svelte';
  import Heatmap from '$lib/components/Heatmap.svelte';
  import { cities } from '$lib/market-data';

  let { form }: PageProps = $props();
  let menuOpen = $state(false);
  let previewTab = $state('trends');
  let language = $state('Python');
  let copied = $state(false);
  let copyError = $state(false);
  let submitting = $state(false);
  let openFaq = $state<number | null>(0);

  const codeExamples: Record<string, string> = {
    Python:
      'import requests\n\nresponse = requests.get(\n    "https://api.spaceengine.example/v1/markets",\n    params={"city": "london", "metric": "price"},\n    headers={"Authorization": "Bearer YOUR_API_KEY"}\n)\n\nmarket = response.json()\nprint(market["price_per_sqm"])',
    JavaScript:
      'const response = await fetch(\n  "https://api.spaceengine.example/v1/markets?city=london&metric=price",\n  {\n    headers: {\n      Authorization: "Bearer YOUR_API_KEY"\n    }\n  }\n);\n\nconst market = await response.json();\nconsole.log(market.price_per_sqm);',
    cURL: 'curl --get \\\n  "https://api.spaceengine.example/v1/markets" \\\n  --data-urlencode "city=london" \\\n  --data-urlencode "metric=price" \\\n  --header "Authorization: Bearer YOUR_API_KEY"'
  };
  const faqs = [
    {
      question: 'What is SpaceEngine?',
      answer:
        'SpaceEngine is an upcoming real estate intelligence platform built to bring market trends, city comparisons, neighborhood price maps, and data access into one workspace. It helps real estate professionals and researchers move from scattered information to a clearer view of the market.'
    },
    {
      question: 'Which markets will be available?',
      answer:
        'Our ambition is worldwide coverage, introduced market by market. Availability, historical depth, and update frequency will depend on local data sources. The cities and figures shown on this page are illustrative previews, not a confirmed launch coverage list.'
    },
    {
      question: 'How often will the data update?',
      answer:
        'We are building frequently updated market feeds with interactive charts inspired by financial market tools. Update frequency will vary by source and market, and will be displayed with the data so you can understand its freshness.'
    },
    {
      question: 'Will there be an API?',
      answer:
        'Yes, API access is part of the planned product. We aim to offer structured market data for your research notebooks, internal tools, and applications. The code on this page previews the intended experience; final endpoints and documentation will be shared before launch.'
    },
    {
      question: 'When can I get access?',
      answer:
        'SpaceEngine is currently in development. Join the early-access list to hear about launch timing, supported markets, and opportunities to try the platform. Pricing and access details will be announced closer to launch.'
    }
  ];

  async function copyCode() {
    try {
      await navigator.clipboard.writeText(codeExamples[language]);
      copied = true;
      copyError = false;
      setTimeout(() => (copied = false), 2200);
    } catch {
      copyError = true;
    }
  }
  function closeMenu() {
    menuOpen = false;
  }
</script>

<svelte:head>
  <title>SpaceEngine — A world of real estate. A clearer perspective.</title>
  <meta
    name="description"
    content="See the bigger picture of real estate with SpaceEngine. Explore global market trends, compare cities, uncover neighborhood price patterns, and bring market data into your own tools. Join early access."
  />
  <meta property="og:title" content="SpaceEngine — Your next move. Backed by data." />
  <meta
    property="og:description"
    content="An upcoming workspace for worldwide real estate intelligence. Market trends, city comparisons, price heatmaps, and API access."
  />
  <meta property="og:type" content="website" />
  <meta name="twitter:card" content="summary" />
</svelte:head>

<a class="skip-link" href="#main">Skip to content</a>
<header class="site-header" id="top">
  <div class="container header-inner">
    <a href="#top" aria-label="SpaceEngine home" onclick={closeMenu}><Logo /></a>
    <nav class:mobile-open={menuOpen} aria-label="Main navigation">
      <a href="#platform" onclick={closeMenu}>The platform</a>
      <a href="#built-for-you" onclick={closeMenu}>Who it’s for</a>
      <a href="#developers" onclick={closeMenu}>For developers <span class="nav-api">API</span></a>
      <a href="#faq" onclick={closeMenu}>FAQs</a>
      <a href="#early-access" class="mobile-cta" onclick={closeMenu}
        >Get early access <ArrowUpRight size={15} /></a
      >
    </nav>
    <a class="button button-dark header-cta" href="#early-access"
      >Get early access <ArrowUpRight size={16} /></a
    >
    <button
      class="menu-toggle"
      aria-label={menuOpen ? 'Close navigation' : 'Open navigation'}
      aria-expanded={menuOpen}
      onclick={() => (menuOpen = !menuOpen)}
      >{#if menuOpen}<X size={23} />{:else}<Menu size={23} />{/if}</button
    >
  </div>
</header>

<main id="main">
  <section class="hero">
    <div class="hero-grid-bg" aria-hidden="true"></div>
    <div class="container hero-inner">
      <div class="hero-copy">
        <a href="#early-access" class="announcement"
          ><span class="announcement-dot"></span>A new perspective is coming <ArrowUpRight
            size={13}
          /></a
        >
        <h1>Your next move.<br />Backed by <em>data.</em></h1>
        <p class="hero-description">
          Real estate moves fast. See what’s next.<br class="desktop-break" /> Connect the dots across
          global markets, uncover opportunities, and make your next decision with confidence.
        </p>
        <div class="hero-actions">
          <a class="button button-dark" href="#early-access"
            >Get early access <ArrowUpRight size={17} /></a
          ><a class="button button-outline" href="#product-preview"
            >Explore the platform <ArrowRight size={16} /></a
          >
        </div>
        <div class="hero-assurance">
          <span><Check size={13} /> Built for a global perspective</span><span
            ><Check size={13} /> Designed for your next decision</span
          >
        </div>
        <div class="hero-coordinate">
          <Globe2 size={14} /><span>LOCAL INSIGHT. GLOBAL POSSIBILITIES.</span><span
            class="coordinate-line"
          ></span>
        </div>
      </div>
      <div class="hero-visual">
        <div class="visual-orbit orbit-one" aria-hidden="true"></div>
        <div class="visual-orbit orbit-two" aria-hidden="true"></div>
        <div class="preview-caption">
          <span class="caption-cross">+</span> YOUR WORLD, IN FOCUS
          <span class="caption-line"></span><span>01 / OVERVIEW</span>
        </div>
        <MarketDemo bind:tab={previewTab} />
        <div class="insight-note">
          <span class="insight-icon"><Sparkles size={17} strokeWidth={1.5} /></span>
          <div>
            <span>Better signals. Smarter decisions.</span><small>Go beyond the asking price.</small
            >
          </div>
          <ArrowUpRight size={17} />
        </div>
        <div class="visual-caption">
          <span><span class="caption-dot"></span> Click around. Get a feel for what’s coming.</span
          ><ArrowDown size={13} />
        </div>
      </div>
    </div>
  </section>
  <div class="market-strip" aria-label="Illustrative market changes">
    <div class="container market-strip-inner">
      <div class="strip-label">
        <TrendingUp size={16} /><span
          >A WORLD IN MOTION<small>Illustrative annual price changes</small></span
        >
      </div>
      <div class="ticker-markets">
        {#each [cities[0], cities[1], cities[2], cities[4], cities[5]] as city}<span
            class="ticker-city"
            ><span>{city.name}</span><span class="ticker-growth"
              ><ArrowUpRight size={12} />{city.growth.toFixed(1)}%</span
            ><svg width="42" height="20" viewBox="0 0 42 20" aria-hidden="true"
              ><polyline
                points={city.trend
                  .map((value, i) => i * 3.8 + ',' + (19 - (value - 90) / 3))
                  .join(' ')}
                fill="none"
                stroke="#759368"
                stroke-width="1.4"
              /></svg
            ></span
          >{/each}
      </div>
    </div>
  </div>
  <section class="audience-strip container">
    <span>BUILT FOR PEOPLE<br />WHO MOVE MARKETS</span>
    <div><Building2 size={21} strokeWidth={1.4} />Real estate professionals</div>
    <div><GraduationCap size={24} strokeWidth={1.4} />Market researchers</div>
    <div><ChartNoAxesCombined size={22} strokeWidth={1.4} />Investment teams</div>
    <div><Code2 size={22} strokeWidth={1.4} />Data builders</div>
  </section>

  <section class="platform-section section-padding" id="platform">
    <div class="container">
      <div class="section-heading">
        <div>
          <span class="eyebrow"><span></span> THE SPACEENGINE PLATFORM</span>
          <h2>The bigger picture.<br /><em>The sharper edge.</em></h2>
        </div>
        <p>
          From a single neighborhood to the other side of the world. Turn a sea of property data
          into a clear direction.
        </p>
      </div>
      <div class="feature-grid">
        <article class="feature-card">
          <div class="feature-top">
            <span class="feature-number">01 / FOLLOW THE SIGNAL</span><TrendingUp
              size={19}
              strokeWidth={1.5}
            />
          </div>
          <div class="feature-visual trend-feature">
            <div class="mini-chart-header">
              <span>Market momentum</span><span>+5.8% <ArrowUpRight size={11} /></span>
            </div>
            <TrendChart mini />
            <div class="mini-chart-footer"><span>JAN</span><span>DEC</span></div>
          </div>
          <h3>Markets don’t stand still.<br />Neither should your insight.</h3>
          <p>
            Track price movement, supply, and market momentum with interactive, frequently updated
            charts. Spot the shift before your next move.
          </p>
          <a href="#product-preview" onclick={() => (previewTab = 'trends')}
            >Explore market trends <ArrowUpRight size={15} /></a
          >
        </article>
        <article class="feature-card">
          <div class="feature-top">
            <span class="feature-number">02 / FIND YOUR OPPORTUNITY</span><Building2
              size={19}
              strokeWidth={1.5}
            />
          </div>
          <div class="feature-visual comparison-feature">
            <div class="comparison-row">
              <span class="city-marker">LDN</span>
              <div><span>London</span><small>United Kingdom</small></div>
              <span class="comparison-value">$12,480<small>per m²</small></span>
            </div>
            <div class="comparison-bar"><span style:width="74%"></span></div>
            <div class="comparison-row">
              <span class="city-marker marker-dubai">DXB</span>
              <div><span>Dubai</span><small>United Arab Emirates</small></div>
              <span class="comparison-value">$5,980<small>per m²</small></span>
            </div>
            <div class="comparison-bar"><span style:width="39%" class="dubai-bar"></span></div>
            <span class="comparison-caption">Different cities. One clear perspective.</span>
          </div>
          <h3>A city you know.<br />A market you haven’t met.</h3>
          <p>
            Put cities side by side. Compare prices, rental yields, and growth to discover how
            different markets stack up against your goals.
          </p>
          <a href="#product-preview" onclick={() => (previewTab = 'compare')}
            >Compare the possibilities <ArrowUpRight size={15} /></a
          >
        </article>
        <article class="feature-card">
          <div class="feature-top">
            <span class="feature-number">03 / GET CLOSER</span><Layers
              size={19}
              strokeWidth={1.5}
            />
          </div>
          <div class="feature-visual heatmap-feature">
            <Heatmap compact /><span class="heatmap-badge"
              ><span></span> A neighborhood-level view</span
            >
          </div>
          <h3>Every neighborhood<br />has a story. See it.</h3>
          <p>
            Go beyond city averages with visual price heatmaps. Reveal local patterns, emerging
            hotspots, and the streets worth a closer look.
          </p>
          <a href="#product-preview" onclick={() => (previewTab = 'heatmap')}
            >Discover price heatmaps <ArrowUpRight size={15} /></a
          >
        </article>
      </div>
      <div class="feature-footnote">
        <ShieldCheck size={15} /><span
          >Context comes first. Source coverage and data freshness will be visible alongside your
          insights.</span
        >
      </div>
    </div>
  </section>

  <section class="people-section section-padding" id="built-for-you">
    <div class="container people-grid">
      <div class="city-photo">
        <img
          src="/images/architecture.jpg"
          alt="Modern glass towers seen from below against an open sky"
          width="900"
          height="1100"
          loading="lazy"
        />
        <div class="photo-overlay">
          <span class="photo-eyebrow"><Globe2 size={15} /> A NEW POINT OF VIEW</span>
          <p>Every address.<br />A bigger story.</p>
          <span class="photo-coordinate">LOOK BEYOND THE LISTING <ArrowUpRight size={16} /></span>
        </div>
      </div>
      <div class="people-copy">
        <span class="eyebrow"><span></span> BUILT AROUND YOUR AMBITION</span>
        <h2>Your expertise.<br /><em>A wider lens.</em></h2>
        <p class="section-description">
          You know your market. SpaceEngine helps you see how it fits into the world.
        </p>
        <div class="persona">
          <span class="persona-icon"><Building2 size={22} strokeWidth={1.5} /></span>
          <div>
            <h3>For real estate professionals</h3>
            <p>
              Bring more than intuition to the conversation. Give clients market context, benchmark
              properties, and find the next area of opportunity.
            </p>
          </div>
        </div>
        <div class="persona">
          <span class="persona-icon"><GraduationCap size={25} strokeWidth={1.5} /></span>
          <div>
            <h3>For researchers & analysts</h3>
            <p>
              Follow trends across borders, explore historical patterns, and build a more complete
              picture with structured data for your research.
            </p>
          </div>
        </div>
        <a href="#early-access" class="text-link"
          >A better view starts here <ArrowUpRight size={17} /></a
        >
      </div>
    </div>
  </section>

  <section class="developer-section" id="developers">
    <div class="container developer-grid">
      <div class="developer-copy">
        <span class="eyebrow light-eyebrow"><span></span> FOR THE BUILDERS & THE CURIOUS</span>
        <h2>Our intelligence.<br /><em>Your next big idea.</em></h2>
        <p>
          Take the data beyond the dashboard. Bring real estate intelligence into your models,
          notebooks, and products with planned API access.
        </p>
        <ul class="api-benefits">
          <li><Check size={14} />Structured, machine-readable market data</li>
          <li><Check size={14} />Built for your research and workflows</li>
          <li><Check size={14} />One connection. A world to explore.</li>
        </ul>
        <a href="#early-access" class="button button-lime"
          >Get API launch updates <ArrowUpRight size={17} /></a
        ><span class="api-status">API access is coming with SpaceEngine.</span>
      </div>
      <div class="code-window">
        <div class="code-window-header">
          <span class="code-dots"><i></i><i></i><i></i></span><span
            >spaceengine / a world of data</span
          ><Code2 size={15} />
        </div>
        <div class="code-tabs">
          <div>
            {#each ['Python', 'JavaScript', 'cURL'] as item}<button
                class:code-active={language === item}
                aria-pressed={language === item}
                onclick={() => {
                  language = item;
                  copied = false;
                  copyError = false;
                }}>{item}</button
              >{/each}
          </div>
          <button
            class="copy-code"
            onclick={copyCode}
            aria-label={copied ? 'Code copied' : 'Copy code'}
            >{#if copied}<Check size={14} />{:else}<Copy size={14} />{/if}<span
              >{copied ? 'Copied' : 'Copy'}</span
            ></button
          >
        </div>
        <pre><code>{codeExamples[language]}</code></pre>
        <div class="code-response">
          <span><span></span> EXAMPLE RESPONSE</span>
          <pre><code
              >{'{ "city": "london", "price_per_sqm": 12480,\n  "currency": "USD", "annual_change": 5.8 }'}</code
            ></pre>
        </div>
        <div class="code-footnote">
          <Code2 size={12} /><span>API design preview · Illustrative endpoint and data</span>
        </div>
        {#if copyError}<p class="copy-error" role="status">
            Select the example above to copy it manually.
          </p>{/if}
      </div>
    </div>
  </section>

  <section class="faq-section section-padding" id="faq">
    <div class="container faq-grid">
      <div>
        <span class="eyebrow"><span></span> A LITTLE MORE CONTEXT</span>
        <h2>Good questions.<br /><em>Clear answers.</em></h2>
        <p class="section-description">Getting to know SpaceEngine.</p>
        <span class="faq-deco" aria-hidden="true"
          ><Globe2 size={95} strokeWidth={0.7} /><Plus size={18} /></span
        >
      </div>
      <div class="faq-list">
        {#each faqs as faq, index}<div class="faq-item" class:faq-open={openFaq === index}>
            <h3>
              <button
                onclick={() => (openFaq = openFaq === index ? null : index)}
                aria-expanded={openFaq === index}
                aria-controls={'faq-answer-' + index}
                ><span>{faq.question}</span><Plus size={19} /></button
              >
            </h3>
            <div id={'faq-answer-' + index} hidden={openFaq !== index}><p>{faq.answer}</p></div>
          </div>{/each}
      </div>
    </div>
  </section>

  <section class="early-access-section" id="early-access">
    <div class="container early-access-grid">
      <div class="early-access-copy">
        <span class="eyebrow"><span></span> A CLEARER PERSPECTIVE IS COMING</span>
        <h2>Be ahead.<br /><em>Be in the know.</em></h2>
        <p>
          Get a first look at SpaceEngine. Join our early-access list for product news and a chance
          to help shape what comes next.
        </p>
        <div class="early-access-note">
          <span class="announcement-dot"></span>In development. Built with the future in mind.
        </div>
      </div>
      <div class="signup-card">
        {#if form?.success}<div class="signup-success" role="status">
            <span><Check size={27} /></span>
            <h3>You’re on the list.</h3>
            <p>
              Thanks for joining SpaceEngine’s early-access list. We’ll be in touch with product and
              launch updates.
            </p>
            <a href="#platform" class="text-link">Keep exploring <ArrowUpRight size={16} /></a>
          </div>{:else}<h3>Your next perspective starts here.</h3>
          <p>Leave your email. We’ll keep you in the loop.</p>
          <form
            method="POST"
            action="?/join"
            use:enhance={() => {
              submitting = true;
              return async ({ update }) => {
                try {
                  await update();
                } finally {
                  submitting = false;
                }
              };
            }}
          >
            <label for="email">Email address</label><input
              id="email"
              name="email"
              type="email"
              placeholder="you@company.com"
              autocomplete="email"
              required
              maxlength="254"
              value={form?.email ?? ''}
              aria-invalid={form?.error ? 'true' : undefined}
              aria-describedby={form?.error ? 'signup-error' : undefined}
            /><label for="role">What brings you here?</label>
            <div class="signup-select">
              <select id="role" name="role" required
                ><option value="" disabled selected>Select your role</option><option value="agent"
                  >Real estate professional</option
                ><option value="researcher">Researcher or analyst</option><option value="investor"
                  >Investor or investment team</option
                ><option value="developer">Developer or data builder</option><option value="other"
                  >Exploring the possibilities</option
                ></select
              ><ChevronDown size={16} />
            </div>
            <div class="honeypot" aria-hidden="true">
              <label for="website">Leave this field empty</label><input
                id="website"
                name="website"
                tabindex="-1"
                autocomplete="off"
              />
            </div>
            <label class="consent"
              ><input type="checkbox" name="consent" value="yes" required /><span
                >I’d like to receive SpaceEngine product and launch updates.</span
              ></label
            >{#if form?.error}<p class="signup-error" id="signup-error" role="alert">
                {form.error}
              </p>{/if}<button
              class="button button-dark signup-button"
              type="submit"
              disabled={submitting}
              >{submitting ? 'Joining the list…' : 'Get early access'}{#if !submitting}<ArrowUpRight
                  size={17}
                />{/if}</button
            ><span class="signup-privacy"
              ><ShieldCheck size={12} />Your email is only used for SpaceEngine updates.</span
            >
          </form>{/if}
      </div>
    </div>
    <div class="access-orbit" aria-hidden="true"></div>
  </section>
</main>
<footer class="site-footer">
  <div class="container">
    <div class="footer-top">
      <div>
        <a href="#top" aria-label="SpaceEngine home"><Logo /></a>
        <p>A world of real estate.<br />A clearer perspective.</p>
      </div>
      <div class="footer-links">
        <a href="#platform">The platform</a><a href="#built-for-you">Who it’s for</a><a
          href="#developers">API access</a
        ><a href="#early-access">Stay in the loop <ArrowUpRight size={13} /></a>
      </div>
      <span class="footer-signoff"
        ><Globe2 size={20} strokeWidth={1.3} />MADE FOR A WORLD<br />OF POSSIBILITIES.</span
      >
    </div>
    <div class="footer-bottom">
      <span>© {new Date().getFullYear()} SpaceEngine. All rights reserved.</span><span
        >An upcoming product. Preview data is illustrative.</span
      ><a href="#top">Back to top <ArrowUpRight size={13} /></a>
    </div>
  </div>
</footer>
