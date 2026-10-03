<script lang="ts">
  import { TrendingDown, TrendingUp, Minus } from '@lucide/svelte';
  import { formatMoney, formatPeriod, type SeriesSummary } from '$lib/trends';
  import type { TrendInterval } from '$lib/types';

  let {
    summaries,
    currency,
    interval
  }: { summaries: SeriesSummary[]; currency: string; interval: TrendInterval } = $props();

  const pct = new Intl.NumberFormat('en', {
    style: 'percent',
    maximumFractionDigits: 0,
    signDisplay: 'exceptZero'
  });
</script>

<ul class="tiles">
  {#each summaries as item (item.label)}
    <li class="card tile">
      <p class="label">
        <span class="swatch" style:background={item.color} aria-hidden="true"></span>{item.label}
      </p>
      <p class="value">{formatMoney(item.latest, currency)}</p>
      <p class="meta">
        {#if item.latestDate}Latest · {formatPeriod(item.latestDate, interval)}{:else}No reliable
          figure in range{/if}
      </p>
      <div class="foot">
        {#if item.change !== null}
          <span class="change">
            {#if item.change > 0}<TrendingUp size={14} />{:else if item.change < 0}<TrendingDown
                size={14}
              />{:else}<Minus size={14} />{/if}
            {pct.format(item.change)} over range
          </span>
        {:else}
          <span class="change muted">Change n/a</span>
        {/if}
        <span class="samples">{item.samples.toLocaleString('en')} adverts</span>
      </div>
    </li>
  {/each}
</ul>

<style>
  .tiles {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
    gap: 12px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .tile {
    padding: 16px 18px;
  }
  .label {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    font-weight: 500;
    color: var(--muted);
  }
  .swatch {
    width: 10px;
    height: 10px;
    border-radius: 50%;
  }
  .value {
    margin-top: 10px;
    font-family: var(--font-serif);
    font-size: 32px;
    letter-spacing: -0.8px;
  }
  .meta {
    margin-top: 2px;
    font-size: 11px;
    color: #98a58a;
  }
  .foot {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    margin-top: 14px;
    padding-top: 12px;
    border-top: 1px solid var(--border);
    font-size: 11px;
  }
  .change {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    color: var(--ink);
    font-weight: 500;
  }
  .muted,
  .samples {
    color: var(--muted);
    font-weight: 400;
  }
</style>
