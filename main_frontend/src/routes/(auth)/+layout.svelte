<script lang="ts">
  import Logo from '$lib/components/Logo.svelte';
  let { children } = $props();
</script>

<div class="auth-shell">
  <div class="grid-bg" aria-hidden="true"></div>
  <div class="orbit" aria-hidden="true"></div>
  <section class="intro">
    <a href="/" aria-label="SpaceEngine home"><Logo /></a>
    <div class="intro-copy">
      <p class="eyebrow"><span></span>Market intelligence</p>
      <h1>The bigger picture.<br /><em>The sharper edge.</em></h1>
      <p class="lede">
        Historical asking prices by region, built from years of archived classifieds. See where a
        market has been before you decide where it is going.
      </p>
    </div>
    <p class="coords">30.0444° N · 31.2357° E — CAIRO</p>
  </section>
  <main class="panel">
    {@render children()}
  </main>
</div>

<style>
  .auth-shell {
    position: relative;
    overflow: hidden;
    min-height: 100vh;
    display: grid;
    grid-template-columns: 1.1fr 1fr;
    align-items: center;
    gap: 48px;
    padding: 48px max(32px, calc((100vw - 1220px) / 2));
  }
  .grid-bg {
    position: absolute;
    inset: 0 0 0 40%;
    background-image:
      linear-gradient(#d7dec434 1px, transparent 1px),
      linear-gradient(90deg, #d7dec434 1px, transparent 1px);
    background-size: 64px 64px;
    mask-image: linear-gradient(90deg, transparent, #000 45%, transparent);
    pointer-events: none;
  }
  .orbit {
    position: absolute;
    width: 640px;
    height: 640px;
    border-radius: 50%;
    border: 1px solid #d6e1c7;
    left: -260px;
    bottom: -280px;
    pointer-events: none;
  }
  .orbit::before {
    content: '';
    position: absolute;
    inset: -55px;
    border: 1px dashed #d7e2c9;
    border-radius: 50%;
  }
  .intro {
    position: relative;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 56px;
    min-height: min(640px, calc(100vh - 96px));
  }
  .intro-copy h1 {
    font-size: clamp(40px, 4.4vw, 60px);
    line-height: 1.08;
    margin-top: 18px;
  }
  .lede {
    margin-top: 22px;
    max-width: 420px;
    color: var(--muted);
    font-size: 14px;
    line-height: 1.85;
  }
  .coords {
    color: #9aa58e;
    font-size: 10px;
    letter-spacing: 1.6px;
  }
  .panel {
    position: relative;
    width: min(420px, 100%);
    justify-self: center;
  }
  @media (max-width: 900px) {
    .auth-shell {
      grid-template-columns: 1fr;
      padding: 28px 16px;
      gap: 28px;
    }
    .intro {
      min-height: 0;
      gap: 24px;
    }
    .lede,
    .coords {
      display: none;
    }
  }
</style>
