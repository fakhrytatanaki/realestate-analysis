<script lang="ts">
  import { LineChart, Area, Spline, Tooltip } from 'layerchart';
  import { scaleUtc } from 'd3-scale';
  import { formatMoney, formatPeriod, type ChartRow, type ChartSeries } from '$lib/trends';
  import type { TrendInterval } from '$lib/types';

  let {
    rows,
    keys,
    interval,
    currency,
    metricLabel,
    onrange
  }: {
    rows: ChartRow[];
    keys: ChartSeries[];
    interval: TrendInterval;
    currency: string;
    metricLabel: string;
    /** Committed brush selection, as inclusive YYYY-MM-DD days. */
    onrange: (range: { from: string; to: string }) => void;
  } = $props();

  const num = (value: unknown) => (typeof value === 'number' ? value : null);

  // Baseline at zero keeps rises and falls proportionate; the band can exceed the
  // medians, so it has to be part of the domain or it would be clipped.
  const yMax = $derived(
    Math.max(
      1,
      ...rows.flatMap((row) =>
        keys.flatMap(({ key }) => [
          num(row[key]) ?? 0,
          keys.length === 1 ? (num(row[`${key}_p75`]) ?? 0) : 0
        ])
      )
    )
  );

  const day = (date: Date) => date.toISOString().slice(0, 10);

  function onBrushEnd({ brush }: { brush: { x: unknown } }) {
    const [start, end] = (brush.x ?? []) as [Date | null, Date | null];
    if (!(start instanceof Date) || !(end instanceof Date) || end <= start) return;
    onrange({ from: day(start), to: day(end) });
  }

  // Year on January ticks, short month otherwise: zoomed-in ranges stay legible
  // without repeating the year on every tick.
  const tickFormat = (value: Date) =>
    value.getUTCMonth() === 0 && value.getUTCDate() === 1
      ? String(value.getUTCFullYear())
      : value.toLocaleDateString('en', { month: 'short', timeZone: 'UTC' });
</script>

<div
  class="chart"
  role="img"
  aria-label={`${metricLabel} over time for ${keys.map((k) => k.label).join(', ')}`}
>
  <LineChart
    data={rows}
    x="date"
    xScale={scaleUtc()}
    series={keys.map((item) => ({
      key: item.key,
      label: item.label,
      color: item.color,
      value: item.key
    }))}
    seriesLayout="overlap"
    yDomain={[0, yMax * 1.05]}
    yNice
    padding={{ top: 12, right: 12, bottom: 28, left: 62 }}
    legend={false}
    points={{ r: 4, class: 'trend-point' }}
    brush={{ zoomOnBrush: false, onBrushEnd }}
    props={{
      yAxis: { format: (value: number) => formatMoney(value, currency), ticks: 5 },
      xAxis: { format: tickFormat, ticks: 6 },
      grid: { class: 'trend-grid' }
    }}
  >
    {#snippet marks()}
      {#if keys.length === 1}
        <!-- Interquartile band for a single region: where the middle half of adverts sat. -->
        <Area
          y0={(row: ChartRow) => num(row[`${keys[0].key}_p25`])}
          y1={(row: ChartRow) => num(row[`${keys[0].key}_p75`])}
          fill={keys[0].color}
          opacity={0.13}
          line={false}
        />
      {/if}
      {#each keys as item (item.key)}
        <Spline seriesKey={item.key} y={item.key} stroke={item.color} class="trend-line" />
      {/each}
    {/snippet}

    {#snippet tooltip()}
      <Tooltip.Root>
        {#snippet children({ data })}
          <Tooltip.Header value={formatPeriod(data.date, interval)} />
          <Tooltip.List>
            {#each keys as item (item.key)}
              <Tooltip.Item
                label={item.label}
                color={item.color}
                value={num(data[item.key]) === null
                  ? `low data (n=${data[`${item.key}_n`] ?? 0})`
                  : `${formatMoney(num(data[item.key]), currency, true)} · n=${data[`${item.key}_n`]}`}
              />
            {/each}
            {#if keys.length === 1 && num(data[`${keys[0].key}_p25`]) !== null}
              <Tooltip.Item
                label="Middle 50%"
                value={`${formatMoney(num(data[`${keys[0].key}_p25`]), currency)} – ${formatMoney(num(data[`${keys[0].key}_p75`]), currency)}`}
              />
            {/if}
          </Tooltip.List>
        {/snippet}
      </Tooltip.Root>
    {/snippet}
  </LineChart>
</div>

<style>
  .chart {
    height: 380px;
    width: 100%;
    font-size: 11px;
    color: var(--muted);
  }
  .chart :global(.trend-grid line) {
    stroke: #e9ece5;
    stroke-dasharray: 3 4;
  }
  .chart :global(.trend-line) {
    stroke-width: 2px;
    stroke-linejoin: round;
    stroke-linecap: round;
  }
  /* Markers keep isolated periods visible; the ring separates overlapping ones. */
  .chart :global(.trend-point) {
    stroke: var(--card);
    stroke-width: 2px;
  }
  .chart :global(.lc-tooltip-root),
  .chart :global([class*='tooltip']) {
    font-family: var(--font-sans);
  }
  @media (max-width: 700px) {
    .chart {
      height: 300px;
    }
  }
</style>
