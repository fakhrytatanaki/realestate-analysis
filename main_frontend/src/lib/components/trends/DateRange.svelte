<script lang="ts">
  import { shiftYears, clampRange } from '$lib/trends';
  import Segmented from './Segmented.svelte';

  let {
    from,
    to,
    span,
    onchange
  }: {
    from: string;
    to: string;
    span: { first: string; last: string };
    onchange: (range: { from: string; to: string }) => void;
  } = $props();

  type Preset = '1Y' | '3Y' | '5Y' | 'ALL' | 'CUSTOM';
  const presetRange = (preset: Exclude<Preset, 'CUSTOM'>) => {
    if (preset === 'ALL') return clampRange(span.first, span.last);
    const years = Number(preset[0]);
    const start = shiftYears(span.last, -years);
    return { from: start < span.first ? span.first : start, to: span.last };
  };
  const active = $derived.by<Preset>(() => {
    for (const preset of ['ALL', '1Y', '3Y', '5Y'] as const) {
      const range = presetRange(preset);
      if (range.from === from && range.to === to) return preset;
    }
    return 'CUSTOM';
  });

  const options = [
    { value: '1Y', label: '1Y' },
    { value: '3Y', label: '3Y' },
    { value: '5Y', label: '5Y' },
    { value: 'ALL', label: 'All' }
  ] as const;

  // <input type="month"> speaks YYYY-MM; the API speaks inclusive days.
  const month = (day: string) => day.slice(0, 7);
  function monthEnd(value: string): string {
    const [year, mon] = value.split('-').map(Number);
    return new Date(Date.UTC(year, mon, 0)).toISOString().slice(0, 10);
  }
  function commit(next: { from?: string; to?: string }) {
    onchange(clampRange(next.from ?? from, next.to ?? to));
  }
</script>

<div class="range">
  <Segmented
    label="Date range preset"
    {options}
    value={active === 'CUSTOM' ? null : active}
    onchange={(value) => onchange(presetRange(value))}
  />
  <div class="months">
    <label>
      <span class="visually-hidden">From</span>
      <input
        class="input"
        type="month"
        value={month(from)}
        min={month(span.first)}
        max={month(to)}
        onchange={(event) =>
          event.currentTarget.value && commit({ from: `${event.currentTarget.value}-01` })}
      />
    </label>
    <span aria-hidden="true">–</span>
    <label>
      <span class="visually-hidden">To</span>
      <input
        class="input"
        type="month"
        value={month(to)}
        min={month(from)}
        max={month(span.last)}
        onchange={(event) =>
          event.currentTarget.value && commit({ to: monthEnd(event.currentTarget.value) })}
      />
    </label>
  </div>
</div>

<style>
  .range {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
  }
  .months {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--muted);
  }
  .input {
    width: 150px;
    height: 38px;
    font-size: 12px;
  }
</style>
