<script lang="ts">
  import { cities, months, type City, type Period } from '$lib/market-data';
  let {
    city = cities[0],
    comparison = undefined,
    period = '1Y',
    mini = false
  } = $props<{ city?: City; comparison?: City; period?: Period; mini?: boolean }>();
  let hovered = $state<number | null>(null);
  const start = $derived(period === '3M' ? 9 : period === '6M' ? 6 : 0);
  const values: number[] = $derived(city.trend.slice(start));
  const secondary: number[] = $derived(
    (comparison?.trend ?? [98, 99, 98, 100, 99, 101, 102, 101, 104, 103, 105, 107]).slice(start)
  );
  const minimum = $derived(Math.min(...values, ...secondary) - 4);
  const maximum = $derived(Math.max(...values, ...secondary) + 5);
  const x = (index: number) => 38 + index * (448 / (values.length - 1));
  const y = (value: number) => 176 - ((value - minimum) / (maximum - minimum)) * 148;
  const points = $derived(values.map((value, index) => `${x(index)},${y(value)}`).join(' '));
  const secondaryPoints = $derived(
    secondary.map((value, index) => `${x(index)},${y(value)}`).join(' ')
  );
  const area = $derived(
    `M ${x(0)} 176 L ${values.map((value, index) => `${x(index)} ${y(value)}`).join(' L ')} L ${x(values.length - 1)} 176 Z`
  );
  function inspect(event: PointerEvent) {
    const rect =
      event.currentTarget instanceof Element ? event.currentTarget.getBoundingClientRect() : null;
    if (rect)
      hovered = Math.max(
        0,
        Math.min(
          values.length - 1,
          Math.round(
            ((((event.clientX - rect.left) / rect.width) * 520 - 38) / 448) * (values.length - 1)
          )
        )
      );
  }
  function inspectKey(event: KeyboardEvent) {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      hovered = Math.max(
        0,
        Math.min(values.length - 1, (hovered ?? 0) + (event.key === 'ArrowRight' ? 1 : -1))
      );
    }
  }
</script>

<div
  class:mini
  class="trend-chart"
  role="slider"
  aria-hidden={mini}
  aria-valuemin={mini ? undefined : 0}
  aria-valuemax={mini ? undefined : values.length - 1}
  aria-valuenow={mini ? undefined : Math.min(hovered ?? 0, values.length - 1)}
  aria-valuetext={mini
    ? undefined
    : months[start + Math.min(hovered ?? 0, values.length - 1)] +
      ' index ' +
      values[Math.min(hovered ?? 0, values.length - 1)]}
  aria-label={mini
    ? 'Sample market trend'
    : 'Price index chart. Use left and right arrow keys to inspect monthly values.'}
  tabindex={mini ? undefined : 0}
  onpointermove={inspect}
  onpointerleave={() => (hovered = null)}
  onkeydown={inspectKey}
  onfocus={() => (hovered = 0)}
  onblur={() => (hovered = null)}
>
  <svg
    viewBox="0 0 520 210"
    role="img"
    aria-label={`${city.name} illustrative property price index over ${period}. ${comparison ? `Compared with ${comparison.name}.` : ''}`}
  >
    <defs>
      <linearGradient id={mini ? 'chart-fill-mini' : 'chart-fill'} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color={city.color} stop-opacity=".19" />
        <stop offset="100%" stop-color={city.color} stop-opacity=".01" />
      </linearGradient>
    </defs>
    {#each [0, 1, 2, 3] as tick}
      <line
        x1="38"
        y1={28 + tick * 49.3}
        x2="486"
        y2={28 + tick * 49.3}
        stroke="#e9ece5"
        stroke-dasharray="3 4"
      />
      {#if !mini}<text x="0" y={32 + tick * 49.3} fill="#8c928b" font-size="9"
          >{Math.round(maximum - (tick * (maximum - minimum)) / 3)}</text
        >{/if}
    {/each}
    <path d={area} fill={`url(#${mini ? 'chart-fill-mini' : 'chart-fill'})`} />
    <polyline
      points={secondaryPoints}
      fill="none"
      stroke={comparison?.color ?? '#a4afa3'}
      stroke-width="2"
      stroke-dasharray={comparison ? undefined : '5 5'}
      stroke-linejoin="round"
    />
    <polyline
      {points}
      fill="none"
      stroke={city.color}
      stroke-width="2.7"
      stroke-linejoin="round"
      stroke-linecap="round"
    />
    <circle
      cx={x(values.length - 1)}
      cy={y(values[values.length - 1])}
      r="4"
      fill={city.color}
      stroke="white"
      stroke-width="2"
    />
    {#if !mini}
      {#each values as value, index}
        {#if index % (values.length > 6 ? 2 : 1) === 0 || index === values.length - 1}
          <text x={x(index)} y="201" text-anchor="middle" fill="#8c928b" font-size="9"
            >{months[start + index]}</text
          >
        {/if}
      {/each}
    {/if}
    {#if hovered !== null && hovered < values.length && !mini}
      <line
        x1={x(hovered)}
        y1="20"
        x2={x(hovered)}
        y2="177"
        stroke="#547653"
        stroke-opacity=".35"
        stroke-dasharray="3 3"
      />
      <circle
        cx={x(hovered)}
        cy={y(values[hovered])}
        r="5"
        fill={city.color}
        stroke="white"
        stroke-width="2"
      />
      <rect
        x={Math.min(414, Math.max(30, x(hovered) - 38))}
        y="0"
        width="82"
        height="24"
        rx="5"
        fill="#203d32"
      />
      <text
        x={Math.min(414, Math.max(30, x(hovered) - 38)) + 41}
        y="16"
        text-anchor="middle"
        fill="white"
        font-size="10">{months[start + hovered]} · {values[hovered]}</text
      >
    {/if}
  </svg>
</div>

<style>
  .trend-chart {
    width: 100%;
  }
  svg {
    display: block;
    width: 100%;
    overflow: visible;
    font-family: var(--font-sans);
  }
  .mini {
    pointer-events: none;
  }
</style>
