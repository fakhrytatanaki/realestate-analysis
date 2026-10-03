<script lang="ts">
  import { formatMoney, formatPeriod, type ChartRow, type ChartSeries } from '$lib/trends';
  import type { TrendInterval } from '$lib/types';

  let {
    rows,
    keys,
    interval,
    currency
  }: { rows: ChartRow[]; keys: ChartSeries[]; interval: TrendInterval; currency: string } =
    $props();

  const cell = (row: ChartRow, key: string) => {
    const value = row[key];
    const n = row[`${key}_n`];
    if (typeof n !== 'number') return '—';
    return typeof value === 'number'
      ? `${formatMoney(value, currency, true)} (n=${n})`
      : `low data (n=${n})`;
  };
</script>

<details>
  <summary>View as table</summary>
  <div class="scroll">
    <table>
      <thead>
        <tr>
          <th scope="col">Period</th>
          {#each keys as item (item.key)}<th scope="col">{item.label}</th>{/each}
        </tr>
      </thead>
      <tbody>
        {#each rows as row (row.date.getTime())}
          <tr>
            <th scope="row">{formatPeriod(row.date, interval)}</th>
            {#each keys as item (item.key)}<td>{cell(row, item.key)}</td>{/each}
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</details>

<style>
  summary {
    cursor: pointer;
    font-size: 12px;
    color: var(--muted);
    width: fit-content;
  }
  summary:hover {
    color: var(--ink);
  }
  .scroll {
    max-height: 320px;
    overflow: auto;
    margin-top: 12px;
    border: 1px solid var(--border);
    border-radius: 6px;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    font-variant-numeric: tabular-nums;
  }
  th,
  td {
    padding: 8px 12px;
    text-align: right;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  th:first-child {
    text-align: left;
  }
  thead th {
    position: sticky;
    top: 0;
    background: #f0f3e9;
    font-weight: 500;
  }
  tbody th {
    font-weight: 400;
    color: var(--muted);
  }
</style>
