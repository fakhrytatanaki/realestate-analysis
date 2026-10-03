<script lang="ts">
  import { page } from '$app/state';
  import { LogOut, ChartLine } from '@lucide/svelte';
  import Logo from '$lib/components/Logo.svelte';
  import type { LayoutProps } from './$types';

  let { data, children }: LayoutProps = $props();
  const initials = $derived(
    data.user.display_name
      .split(/\s+/)
      .map((part) => part[0] ?? '')
      .join('')
      .slice(0, 2)
      .toUpperCase() || '·'
  );
</script>

<a class="skip-link" href="#main">Skip to content</a>
<header class="app-nav">
  <div class="container nav-inner">
    <a href="/trends" aria-label="SpaceEngine home"><Logo small /></a>
    <nav aria-label="Primary">
      <a href="/trends" aria-current={page.url.pathname === '/trends' ? 'page' : undefined}>
        <ChartLine size={15} strokeWidth={1.5} /> Price trends
      </a>
    </nav>
    <div class="account">
      <span class="avatar" aria-hidden="true">{initials}</span>
      <span class="who">
        <strong>{data.user.display_name}</strong>
        <small>{data.user.email}</small>
      </span>
      <form method="POST" action="/logout">
        <button class="icon-button" aria-label="Sign out" title="Sign out">
          <LogOut size={16} strokeWidth={1.5} />
        </button>
      </form>
    </div>
  </div>
</header>

<main id="main">
  {@render children()}
</main>

<style>
  .app-nav {
    position: sticky;
    top: 0;
    z-index: 20;
    background: #f7f8f2e8;
    backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--border);
  }
  .nav-inner {
    display: flex;
    align-items: center;
    gap: 36px;
    height: 64px;
  }
  nav {
    display: flex;
    gap: 6px;
    flex: 1;
  }
  nav a {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    padding: 8px 12px;
    border-radius: 5px;
    color: var(--muted);
  }
  nav a:hover {
    color: var(--ink);
  }
  nav a[aria-current='page'] {
    color: var(--ink);
    background: #edf1e4;
  }
  .account {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .avatar {
    display: grid;
    place-items: center;
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: var(--ink-strong);
    color: var(--lime);
    font-size: 11px;
    font-weight: 600;
  }
  .who {
    display: grid;
    line-height: 1.25;
  }
  .who strong {
    font-size: 12px;
    font-weight: 600;
  }
  .who small {
    font-size: 11px;
    color: var(--muted);
  }
  .icon-button {
    display: grid;
    place-items: center;
    width: 34px;
    height: 34px;
    border: 1px solid var(--border);
    border-radius: 5px;
    background: transparent;
    color: var(--muted);
  }
  .icon-button:hover {
    color: var(--ink);
    background: #edf1e4;
  }
  @media (max-width: 700px) {
    .nav-inner {
      gap: 12px;
    }
    .who {
      display: none;
    }
  }
</style>
