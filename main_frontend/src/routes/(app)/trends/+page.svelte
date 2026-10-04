<script lang="ts">
  import { goto } from '$app/navigation';
  import { navigating } from '$app/state';
  import { CircleAlert, Info } from '@lucide/svelte';
  import PriceTrendChart from '$lib/components/trends/PriceTrendChart.svelte';
  import RegionPicker from '$lib/components/trends/RegionPicker.svelte';
  import DateRange from '$lib/components/trends/DateRange.svelte';
  import Segmented from '$lib/components/trends/Segmented.svelte';
  import SummaryTiles from '$lib/components/trends/SummaryTiles.svelte';
  import DataTable from '$lib/components/trends/DataTable.svelte';
  import {
    METRIC_LABEL,
    PROPERTY_TYPES,
    SERIES_COLORS,
    formatDay,
    mergeSeries,
    regionLabel,
    stateToSearch,
    summarise,
    type Line,
    type TrendState
  } from '$lib/trends';
  import type { PageProps } from './$types';

  let { data }: PageProps = $props();

  const state = $derived(data.state);
  // Labels and colours follow the line's slot, so a failed request leaves no colour shift.
  const lines = $derived(
    data.series.flatMap((series, index): Line[] =>
      series
        ? [{ series, label: regionLabel(state.regions[index]), color: SERIES_COLORS[index] }]
        : []
    )
  );
  const chart = $derived(mergeSeries(lines, state.interval));
  const currency = $derived(lines[0]?.series.currency ?? 'EGP');
  const summaries = $derived(lines.map(summarise));
  const hasValues = $derived(
    chart.rows.some((row) => chart.keys.some(({ key }) => row[key] !== null))
  );
  const hiddenPeriods = $derived(summaries.reduce((sum, item) => sum + item.hidden, 0));
  const busy = $derived(navigating.to?.url.pathname === '/trends');

  /**
   * Every control writes the URL; the server load turns it into data. The date
   * range is kept across changes, and the server clamps it to the new data span.
   */
  function update(patch: Partial<TrendState>) {
    const next: TrendState = { ...state, ...patch };
    goto(`/trends${stateToSearch(next)}`, { keepFocus: true, noScroll: true, replaceState: false });
  }
</script>

<svelte:head><title>Price trends — SpaceEngine</title></svelte:head>

<div class="container page">
  <header class="heading">
    <div>
      <p class="eyebrow"><span></span>Historical prices · Egypt</p>
      <h1>Price trends, <em>by region.</em></h1>
    </div>
    <p class="lede">
      Median asking prices from archived and live classifieds, for the whole country or any mix of
      cities and districts. Each advert counts once per period; thin periods are left as gaps rather
      than guessed.
    </p>
  </header>

  <section class="card controls" aria-label="Chart filters">
    <RegionPicker
      cities={data.cities}
      selected={state.regions}
      onchange={(regions) => update({ regions })}
    />
    <div class="filter-row">
      <Segmented
        label="Listing type"
        options={[
          { value: 'SALE', label: 'For sale' },
          { value: 'RENT', label: 'For rent' }
        ]}
        value={state.type}
        onchange={(type) => update({ type })}
      />
      <Segmented
        label="Metric"
        options={[
          { value: 'median_price', label: 'Price' },
          { value: 'median_price_per_sqm', label: 'Price / m²' }
        ]}
        value={state.metric}
        onchange={(metric) => update({ metric })}
      />
      <Segmented
        label="Interval"
        options={[
          { value: 'week', label: 'Week' },
          { value: 'month', label: 'Month' },
          { value: 'quarter', label: 'Quarter' }
        ]}
        value={state.interval}
        onchange={(interval) => update({ interval })}
      />
      <label class="visually-hidden" for="ptype">Property type</label>
      <select
        id="ptype"
        class="select ptype"
        value={state.ptype}
        onchange={(event) => update({ ptype: event.currentTarget.value })}
      >
        {#each PROPERTY_TYPES as [value, label] (value)}<option {value}>{label}</option>{/each}
      </select>
      {#if data.span && state.from && state.to}
        <div class="range-slot">
          <DateRange
            from={state.from}
            to={state.to}
            span={data.span}
            onchange={({ from, to }) => update({ from, to })}
          />
        </div>
      {/if}
    </div>
  </section>

  {#if data.errors.length}
    <p class="notice error" role="alert">
      <CircleAlert size={16} />
      {data.errors.join(' · ')}
    </p>
  {/if}

  <section class="card chart-card" aria-busy={busy} class:busy>
    <div class="chart-head">
      <div>
        <h2>{METRIC_LABEL[state.metric]}</h2>
        <p class="sub">
          {state.type === 'SALE' ? 'Sale prices, total' : 'Monthly rents'} · {currency}
          {#if state.from && state.to}· {formatDay(state.from)} – {formatDay(state.to)}{/if}
        </p>
      </div>
      <div class="head-side">
        {#if chart.keys.length > 1}
          <ul class="legend" aria-label="Legend">
            {#each chart.keys as item (item.key)}
              <li><span style:background={item.color} aria-hidden="true"></span>{item.label}</li>
            {/each}
          </ul>
        {/if}
        <p class="hint">Drag across the chart to zoom into a period.</p>
      </div>
    </div>

    {#if !data.cities.length}
      <div class="empty">
        <p>No priced history yet.</p>
        <span>Run a crawl in <code>core/</code> to populate observations.</span>
      </div>
    {:else if !hasValues}
      <div class="empty">
        <p>Not enough adverts in this range.</p>
        <span>Try a wider date range, a coarser interval, or combine nearby places.</span>
      </div>
    {:else}
      <PriceTrendChart
        rows={chart.rows}
        keys={chart.keys}
        interval={state.interval}
        {currency}
        metricLabel={METRIC_LABEL[state.metric]}
        onrange={({ from, to }) => update({ from, to })}
      />
    {/if}

    {#if hiddenPeriods > 0}
      <p class="notice">
        <Info size={14} />
        {hiddenPeriods} period{hiddenPeriods === 1 ? '' : 's'} had fewer than 5 adverts and are shown
        as gaps.
      </p>
    {/if}
    {#if hasValues}<DataTable
        rows={chart.rows}
        keys={chart.keys}
        interval={state.interval}
        {currency}
      />{/if}
  </section>

  {#if summaries.length}
    <SummaryTiles {summaries} {currency} interval={state.interval} />
  {/if}

  <p class="footnote">
    Asking prices, not transactions. Sale figures use totals and rents use monthly amounts; price
    per m² uses the advert's stated area. No currency conversion is applied.
  </p>
</div>

<style>
  .page {
    display: grid;
    gap: 20px;
    padding-block: 36px 64px;
  }
  .heading {
    display: grid;
    grid-template-columns: 1fr minmax(0, 420px);
    align-items: end;
    gap: 24px;
  }
  .heading h1 {
    font-size: clamp(34px, 3.6vw, 48px);
    line-height: 1.1;
    margin-top: 10px;
  }
  .lede {
    color: var(--muted);
    font-size: 13px;
    line-height: 1.8;
  }
  .controls {
    display: grid;
    gap: 16px;
    padding: 18px;
  }
  .filter-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
  }
  .ptype {
    width: auto;
    min-width: 170px;
    height: 38px;
    font-size: 12px;
  }
  .range-slot {
    margin-left: auto;
  }
  .chart-card {
    display: grid;
    gap: 16px;
    padding: 22px 22px 18px;
    transition: opacity 0.2s;
  }
  .chart-card.busy {
    opacity: 0.55;
  }
  .chart-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
  }
  .chart-head h2 {
    font-size: 24px;
    letter-spacing: -0.5px;
  }
  .sub {
    margin-top: 4px;
    font-size: 12px;
    color: var(--muted);
  }
  .head-side {
    display: grid;
    justify-items: end;
    gap: 8px;
  }
  .legend {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 6px 16px;
    margin: 0;
    padding: 0;
    list-style: none;
    font-size: 12px;
    color: var(--ink);
  }
  .legend li {
    display: inline-flex;
    align-items: center;
    gap: 7px;
  }
  .legend span {
    width: 14px;
    height: 3px;
    border-radius: 2px;
  }
  .hint {
    font-size: 11px;
    color: #98a58a;
  }
  .empty {
    display: grid;
    place-content: center;
    gap: 6px;
    height: 300px;
    text-align: center;
    border: 1px dashed var(--border-strong);
    border-radius: 6px;
  }
  .empty p {
    font-family: var(--font-serif);
    font-size: 22px;
  }
  .empty span {
    font-size: 12px;
    color: var(--muted);
  }
  .notice {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    color: var(--muted);
  }
  .notice.error {
    color: var(--danger);
    padding: 12px 14px;
    border: 1px solid #ecc9bd;
    background: #fbf1ed;
    border-radius: 6px;
  }
  .footnote {
    font-size: 11px;
    line-height: 1.7;
    color: #98a58a;
    max-width: 760px;
  }
  @media (max-width: 900px) {
    .heading {
      grid-template-columns: 1fr;
    }
    .range-slot {
      margin-left: 0;
    }
    .chart-head {
      flex-direction: column;
      align-items: flex-start;
    }
    .head-side {
      justify-items: start;
    }
    .hint {
      display: none;
    }
  }
</style>
