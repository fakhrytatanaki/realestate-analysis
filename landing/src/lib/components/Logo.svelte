<script lang="ts">
  let { small = false, light = false } = $props<{ small?: boolean; light?: boolean }>();
</script>

<span class:small class:light class="brand">
  <svg viewBox="0 0 34 38" fill="none" aria-hidden="true">
    <path
      d="M17 2 32 10.7v16.6L17 36 2 27.3V10.7L17 2Z"
      stroke="currentColor"
      stroke-width="2.1"
      stroke-linejoin="round"
    />
    <path
      d="m2 10.7 15 8.6 15-8.6M17 19.3V36m-7.5-21V23l7.5 4.3 7.5-4.3v-8"
      stroke="currentColor"
      stroke-width="2.1"
      stroke-linejoin="round"
    />
  </svg>
  <span>space<span class="brand-second">engine</span><span class="brand-dot">.</span></span>
</span>

<style>
  .brand {
    display: inline-flex;
    align-items: center;
    gap: 11px;
    font-size: 25px;
    font-weight: 650;
    letter-spacing: -1.2px;
    color: var(--ink);
    white-space: nowrap;
  }
  svg {
    width: 30px;
    height: 34px;
    color: var(--green);
  }
  .brand-second {
    font-weight: 400;
  }
  .brand-dot {
    color: var(--green);
  }
  .small {
    font-size: 17px;
    gap: 8px;
    letter-spacing: -0.7px;
  }
  .small svg {
    width: 22px;
    height: 25px;
  }
  .light,
  .light svg,
  .light .brand-dot {
    color: var(--lime);
  }
</style>
