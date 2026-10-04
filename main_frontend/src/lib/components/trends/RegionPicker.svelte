<script lang="ts">
  import { Plus, X } from '@lucide/svelte';
  import {
    COUNTRY_LABEL,
    MAX_PLACES,
    MAX_REGIONS,
    SERIES_COLORS,
    covers,
    placeKey,
    placeLabel,
    poolPlaces,
    regionId,
    regionLabel,
    uniqueRegions,
    type City,
    type Region
  } from '$lib/trends';

  let {
    cities,
    selected,
    onchange
  }: { cities: City[]; selected: Region[]; onchange: (regions: Region[]) => void } = $props();

  /** Place-select value for the whole country; no city is called this. */
  const COUNTRY = '*';
  /** Line-select value for a new line; every region id is non-empty. */
  const NEW_LINE = '';

  let city = $state('');
  let district = $state('');
  let target = $state(NEW_LINE);

  const current = $derived(cities.find((item) => item.name === city));
  const whole = $derived(city === COUNTRY);
  const full = $derived(selected.length >= MAX_REGIONS);
  const combinable = $derived(selected.some((region) => region.length > 0));
  /** The line the place joins, or -1 for a new line. Falls back if that line is gone. */
  const into = $derived(
    whole ? -1 : selected.findIndex((region) => region.length > 0 && regionId(region) === target)
  );

  /** The lines after adding, or why the place cannot be added. */
  const proposal = $derived.by((): { next: Region[] } | { reason: string } => {
    if (!city) return { reason: '' };
    const place = whole ? null : { city, district: district || null };
    let next: Region[];
    if (place && into >= 0) {
      const line = selected[into];
      if (line.some((other) => covers(other, place))) return { reason: 'Already in that line.' };
      if (line.length >= MAX_PLACES) return { reason: `Up to ${MAX_PLACES} places per line.` };
      next = selected.with(into, poolPlaces([...line, place]));
    } else {
      if (full) return { reason: `Up to ${MAX_REGIONS} lines; combine with one instead.` };
      next = [...selected, place ? [place] : []];
    }
    return uniqueRegions(next).length < next.length ? { reason: 'Already compared.' } : { next };
  });
  const reason = $derived('reason' in proposal ? proposal.reason : '');

  function add() {
    if (!('next' in proposal)) return;
    onchange(proposal.next);
    city = '';
    district = '';
    target = NEW_LINE;
  }

  function removePlace(index: number, key: string) {
    const line = selected[index].filter((place) => placeKey(place) !== key);
    // Taking a place out can leave two identical lines; the first one stays.
    onchange(uniqueRegions(selected.with(index, line)));
  }
</script>

<div class="picker">
  <ul class="chips" aria-label="Compared lines">
    {#each selected as region, index (regionId(region))}
      <li class="chip">
        <span class="swatch" style:background={SERIES_COLORS[index]} aria-hidden="true"></span>
        {#if !region.length}
          {COUNTRY_LABEL}
        {:else}
          {#each region as place, at (placeKey(place))}
            {#if at > 0}<span class="plus">+</span>{/if}
            <span class="place">
              {placeLabel(place)}
              {#if region.length > 1}
                <button
                  type="button"
                  class="drop"
                  aria-label={`Remove ${placeLabel(place)} from ${regionLabel(region)}`}
                  onclick={() => removePlace(index, placeKey(place))}
                >
                  <X size={11} />
                </button>
              {/if}
            </span>
          {/each}
        {/if}
        {#if selected.length > 1}
          <button
            type="button"
            class="remove"
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
    <label class="visually-hidden" for="region-city">Place</label>
    <select class="select" id="region-city" bind:value={city} onchange={() => (district = '')}>
      <option value="">Add a place…</option>
      <option value={COUNTRY}>{COUNTRY_LABEL}</option>
      {#each cities as item (item.name)}
        <option value={item.name}>{item.name} ({item.listingCount.toLocaleString('en')})</option>
      {/each}
    </select>
    <label class="visually-hidden" for="region-district">District</label>
    <select
      class="select"
      id="region-district"
      bind:value={district}
      disabled={!current?.districts.length}
    >
      <option value="">{whole ? 'Whole country' : 'Whole city'}</option>
      {#each current?.districts ?? [] as item (item.name)}
        <option value={item.name}>{item.name} ({item.listingCount.toLocaleString('en')})</option>
      {/each}
    </select>
    <label class="visually-hidden" for="region-target">Line</label>
    <select
      class="select"
      id="region-target"
      value={into >= 0 ? target : NEW_LINE}
      onchange={(event) => (target = event.currentTarget.value)}
      disabled={whole || !combinable}
    >
      <option value={NEW_LINE}>As a new line</option>
      {#each selected as region (regionId(region))}
        {#if region.length}
          <option value={regionId(region)}>Combine with {regionLabel(region)}</option>
        {/if}
      {/each}
    </select>
    <button
      type="button"
      class="button button-outline add-button"
      onclick={add}
      disabled={!('next' in proposal)}
      aria-describedby={reason ? 'region-reason' : undefined}
    >
      <Plus size={15} strokeWidth={1.6} />
      {into >= 0 ? 'Combine' : 'Compare'}
    </button>
  </div>
  {#if reason}<p class="reason" id="region-reason" aria-live="polite">{reason}</p>{/if}
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
    flex-wrap: wrap;
    align-items: center;
    gap: 4px 8px;
    min-height: 32px;
    padding: 4px 6px 4px 12px;
    border: 1px solid var(--border-strong);
    border-radius: 16px;
    background: #fff;
    font-size: 12px;
    font-weight: 500;
  }
  .chip:not(:has(> .remove)) {
    padding-right: 12px;
  }
  .swatch {
    width: 10px;
    height: 10px;
    border-radius: 50%;
  }
  .place {
    display: inline-flex;
    align-items: center;
    gap: 2px;
  }
  .plus {
    color: var(--muted);
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
  .chip button.drop {
    width: 18px;
    height: 18px;
  }
  .chip button:hover {
    background: #edf1e4;
    color: var(--ink);
  }
  .add {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr)) auto;
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
  .reason {
    margin-top: -4px;
    font-size: 11px;
    color: var(--muted);
  }
  @media (max-width: 760px) {
    .add {
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    }
  }
  @media (max-width: 560px) {
    .add {
      grid-template-columns: 1fr;
    }
  }
</style>
