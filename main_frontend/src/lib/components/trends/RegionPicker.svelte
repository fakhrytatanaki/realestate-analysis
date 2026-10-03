<script lang="ts">
  import { Plus, X } from '@lucide/svelte';
  import {
    MAX_REGIONS,
    SERIES_COLORS,
    regionKey,
    regionLabel,
    type City,
    type RegionRef
  } from '$lib/trends';

  let {
    cities,
    selected,
    onchange
  }: { cities: City[]; selected: RegionRef[]; onchange: (regions: RegionRef[]) => void } = $props();

  let city = $state('');
  let district = $state('');
  const current = $derived(cities.find((item) => item.name === city));
  const candidate = $derived<RegionRef | null>(city ? { city, district: district || null } : null);
  const duplicate = $derived(
    candidate !== null && selected.some((item) => regionKey(item) === regionKey(candidate))
  );
  const full = $derived(selected.length >= MAX_REGIONS);

  function add() {
    if (!candidate || duplicate || full) return;
    onchange([...selected, candidate]);
    city = '';
    district = '';
  }
</script>

<div class="picker">
  <ul class="chips" aria-label="Compared regions">
    {#each selected as region, index (regionKey(region))}
      <li class="chip">
        <span class="swatch" style:background={SERIES_COLORS[index]} aria-hidden="true"></span>
        {regionLabel(region)}
        {#if selected.length > 1}
          <button
            type="button"
            aria-label={`Remove ${regionLabel(region)}`}
            onclick={() => onchange(selected.filter((_, i) => i !== index))}
          >
            <X size={13} />
          </button>
        {/if}
      </li>
    {/each}
  </ul>

  <div class="add">
    <label class="visually-hidden" for="region-city">City</label>
    <select
      class="select"
      id="region-city"
      bind:value={city}
      onchange={() => (district = '')}
      disabled={full}
    >
      <option value="">{full ? `Up to ${MAX_REGIONS} regions` : 'Add a city…'}</option>
      {#each cities as item (item.name)}
        <option value={item.name}>{item.name} ({item.listingCount.toLocaleString('en')})</option>
      {/each}
    </select>
    <label class="visually-hidden" for="region-district">District</label>
    <select
      class="select"
      id="region-district"
      bind:value={district}
      disabled={full || !current?.districts.length}
    >
      <option value="">Whole city</option>
      {#each current?.districts ?? [] as item (item.name)}
        <option value={item.name}>{item.name} ({item.listingCount.toLocaleString('en')})</option>
      {/each}
    </select>
    <button
      type="button"
      class="button button-outline add-button"
      onclick={add}
      disabled={!candidate || duplicate || full}
      title={duplicate ? 'Already compared' : undefined}
    >
      <Plus size={15} strokeWidth={1.6} /> Compare
    </button>
  </div>
</div>

<style>
  .picker {
    display: grid;
    gap: 12px;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    height: 32px;
    padding: 0 6px 0 12px;
    border: 1px solid var(--border-strong);
    border-radius: 16px;
    background: #fff;
    font-size: 12px;
    font-weight: 500;
  }
  .chip:not(:has(button)) {
    padding-right: 12px;
  }
  .swatch {
    width: 10px;
    height: 10px;
    border-radius: 50%;
  }
  .chip button {
    display: grid;
    place-items: center;
    width: 22px;
    height: 22px;
    border: 0;
    border-radius: 50%;
    background: transparent;
    color: var(--muted);
  }
  .chip button:hover {
    background: #edf1e4;
    color: var(--ink);
  }
  .add {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
    gap: 8px;
  }
  .add-button {
    min-height: 44px;
    gap: 6px;
  }
  .add-button:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }
  @media (max-width: 560px) {
    .add {
      grid-template-columns: 1fr;
    }
  }
</style>
