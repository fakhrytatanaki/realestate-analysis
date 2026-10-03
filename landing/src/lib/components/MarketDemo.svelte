<script lang="ts">
  import {
    ArrowUpRight,
    ChartNoAxesCombined,
    ChevronDown,
    Globe2,
    Layers,
    LayoutGrid,
    Map,
    SlidersHorizontal,
    TrendingUp
  } from '@lucide/svelte';
  import { cities, formatPrice, heatmapPalettes, type Period } from '$lib/market-data';
  import Logo from './Logo.svelte';
  import TrendChart from './TrendChart.svelte';
  import Heatmap from './Heatmap.svelte';

  let { tab = $bindable('trends') } = $props<{ tab?: string }>();
  let cityName = $state('London');
  let comparisonName = $state('New York');
  let period = $state<Period>('1Y');
  let metric = $state('price');
  const city = $derived(cities.find((item) => item.name === cityName) ?? cities[0]);
  const comparison = $derived(cities.find((item) => item.name === comparisonName) ?? cities[1]);
  $effect(() => {
    if (comparisonName === cityName)
      comparisonName = cities.find((item) => item.name !== cityName)!.name;
  });
  const tabs = [
    { id: 'trends', label: 'Market trends', icon: TrendingUp },
    { id: 'compare', label: 'Compare cities', icon: ChartNoAxesCombined },
    { id: 'heatmap', label: 'Price heatmap', icon: Map }
  ];
</script>

<div class="dashboard" id="product-preview">
  <div class="dashboard-topbar">
    <Logo small />
    <span class="demo-label"><span></span> PRODUCT PREVIEW</span>
    <span class="avatar">SE</span>
  </div>
  <div class="dashboard-layout">
    <aside class="dashboard-sidebar" aria-hidden="true">
      <span class="sidebar-active"><LayoutGrid size={17} /></span>
      <span><ChartNoAxesCombined size={17} /></span>
      <span><Globe2 size={17} /></span>
      <span><Layers size={17} /></span>
      <span class="sidebar-bottom"><SlidersHorizontal size={17} /></span>
    </aside>
    <div class="dashboard-main">
      <div class="dashboard-heading">
        <div>
          <span class="tiny-label">YOUR NEXT OPPORTUNITY STARTS HERE</span>
          <h3>Market overview <span>↗</span></h3>
        </div>
        <Globe2 size={21} strokeWidth={1.4} />
      </div>
      <div class="dashboard-tabs" aria-label="Explore the product preview">
        {#each tabs as item}<button
            class:active={tab === item.id}
            onclick={() => (tab = item.id)}
            aria-pressed={tab === item.id}><item.icon size={13} />{item.label}</button
          >{/each}
      </div>
      <div class="dashboard-controls">
        {#if tab === 'heatmap'}<span class="city-select"
            ><Globe2 size={12} /><span class="map-city">London</span></span
          >{:else}<label class="city-select"
            ><Globe2 size={12} /><select aria-label="Market city" bind:value={cityName}
              >{#each cities as item (item.name)}<option value={item.name}>{item.name}</option
                >{/each}</select
            ><ChevronDown size={12} /></label
          >{/if}
        {#if tab === 'compare'}
          <span class="versus">vs.</span><label class="city-select"
            ><select aria-label="Comparison city" bind:value={comparisonName}
              >{#each cities.filter((item) => item.name !== cityName) as item (item.name)}<option
                  value={item.name}>{item.name}</option
                >{/each}</select
            ><ChevronDown size={12} /></label
          >
        {:else if tab === 'heatmap'}
          <label class="city-select"
            ><select aria-label="Heatmap metric" bind:value={metric}
              ><option value="price">Price / m²</option><option value="growth">Annual growth</option
              ><option value="yield">Rental yield</option></select
            ><ChevronDown size={12} /></label
          >
        {:else}<span class="data-status"><span></span> Sample dataset</span>{/if}
      </div>
      {#if tab === 'heatmap'}
        <div class="heatmap-panel">
          <div class="chart-heading">
            <div>
              <span class="tiny-label">NEIGHBORHOOD INTELLIGENCE</span>
              <h4>Look closer. See more.</h4>
            </div>
            <Map size={15} />
          </div>
          <Heatmap {metric} />
          <div class="map-legend">
            <span>London neighborhood illustration</span>
            <div>
              <span>Low</span>{#each heatmapPalettes[metric] as color}<i style:background={color}
                ></i>{/each}<span>High</span>
            </div>
          </div>
        </div>
      {:else}
        <div class="stats-row">
          <div>
            <span>{tab === 'compare' ? city.name : 'Average price / m²'}</span><strong
              >{formatPrice(city.price)}<span>USD / m²</span></strong
            ><small><ArrowUpRight size={12} /> {city.growth}% <span>year over year</span></small>
          </div>
          <div>
            <span>{tab === 'compare' ? comparison.name : 'Gross rental yield'}</span><strong
              >{tab === 'compare' ? formatPrice(comparison.price) : `${city.yield}%`}<span
                >{tab === 'compare' ? 'USD / m²' : 'annual'}</span
              ></strong
            ><small
              ><ArrowUpRight size={12} />
              {tab === 'compare' ? `${comparison.growth}%` : 'Income potential'}
              <span>{tab === 'compare' ? 'year over year' : ''}</span></small
            >
          </div>
        </div>
        <div class="chart-panel">
          <div class="chart-heading">
            <div>
              <span class="tiny-label">THE BIGGER PICTURE</span>
              <h4>Property price index</h4>
            </div>
            <div class="period-picker" aria-label="Chart time range">
              {#each ['3M', '6M', '1Y'] as range}<button
                  class:chosen={period === range}
                  onclick={() => (period = range as Period)}
                  aria-pressed={period === range}>{range}</button
                >{/each}
            </div>
          </div>
          <div class="chart-legend">
            <span><i style:background={city.color}></i>{city.name}</span><span
              ><i style:background={tab === 'compare' ? comparison.color : '#a4afa3'}></i>{tab ===
              'compare'
                ? comparison.name
                : 'Market benchmark'}</span
            ><span class="index-label">Illustrative index</span>
          </div>
          <TrendChart {city} {period} comparison={tab === 'compare' ? comparison : undefined} />
        </div>
        <div class="dashboard-bottom">
          <span
            ><span class="tiny-market-icon"><Globe2 size={14} /></span><span
              >One perspective.<strong>A world of possibilities.</strong></span
            ></span
          >
          <div class="market-bars" aria-hidden="true">
            {#each [25, 42, 32, 55, 45, 66, 58, 75, 67, 88, 78, 98] as height}<i
                style:height={`${height}%`}
              ></i>{/each}
          </div>
        </div>
      {/if}
      <div class="dashboard-footnote">
        Illustrative data · Explore the controls above <ArrowUpRight size={11} />
      </div>
    </div>
  </div>
</div>

<style>
  .dashboard {
    background: #fff;
    border: 1px solid #dce2d5;
    border-radius: 13px;
    box-shadow:
      0 28px 60px -24px #243d312b,
      0 4px 12px #243d3107;
    overflow: hidden;
    position: relative;
    width: 100%;
  }
  .dashboard-topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    height: 64px;
    padding: 0 21px;
    border-bottom: 1px solid var(--border);
  }
  .demo-label {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 8px;
    letter-spacing: 1.25px;
    color: #7c8678;
    margin-left: auto;
  }
  .demo-label > span {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: #6b8b52;
  }
  .avatar {
    width: 26px;
    height: 26px;
    display: grid;
    place-items: center;
    border-radius: 50%;
    background: #ecf0e7;
    color: #53684c;
    font-size: 8px;
    font-weight: 600;
  }
  .dashboard-layout {
    display: flex;
  }
  .dashboard-sidebar {
    width: 51px;
    flex: 0 0 51px;
    border-right: 1px solid var(--border);
    display: flex;
    align-items: center;
    flex-direction: column;
    gap: 17px;
    padding: 20px 0;
    color: #96a18e;
  }
  .dashboard-sidebar > span {
    display: grid;
    place-items: center;
    width: 31px;
    height: 31px;
  }
  .dashboard-sidebar .sidebar-active {
    background: #eef3e6;
    color: #55734a;
    border-radius: 6px;
  }
  .dashboard-sidebar .sidebar-bottom {
    margin-top: auto;
  }
  .dashboard-main {
    padding: 24px 21px 14px;
    flex: 1;
    min-width: 0;
  }
  .dashboard-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    color: #708466;
  }
  .tiny-label {
    font-size: 7px;
    font-weight: 500;
    letter-spacing: 1.1px;
    color: #909788;
  }
  h3 {
    font-size: 21px;
    font-weight: 550;
    color: var(--ink);
    margin: 6px 0 20px;
    letter-spacing: -0.6px;
  }
  h3 > span {
    color: #789965;
    font-weight: 400;
    margin-left: 4px;
  }
  .dashboard-tabs {
    display: flex;
    border-bottom: 1px solid var(--border);
    gap: 19px;
  }
  .dashboard-tabs button {
    padding: 0 0 12px;
    border: 0;
    border-bottom: 2px solid transparent;
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: 10px;
    color: #838d7c;
    background: none;
    white-space: nowrap;
  }
  .dashboard-tabs button.active {
    color: #42613a;
    border-bottom-color: #668b54;
  }
  .dashboard-controls {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 15px 0;
  }
  .city-select {
    display: flex;
    align-items: center;
    gap: 5px;
    border: 1px solid #e2e7dd;
    border-radius: 5px;
    padding: 6px 8px;
    color: #59704d;
  }
  .city-select select {
    appearance: none;
    border: 0;
    padding-right: 4px;
    background: transparent;
    font-size: 10px;
    font-weight: 500;
    color: var(--ink);
    max-width: 135px;
    cursor: pointer;
  }
  .map-city {
    font-size: 10px;
    color: var(--ink);
  }
  .versus {
    font-size: 10px;
    color: #818b7a;
  }
  .data-status {
    margin-left: auto;
    font-size: 8px;
    color: #82917a;
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .data-status > span {
    width: 4px;
    height: 4px;
    border-radius: 50%;
    background: #8ca47b;
  }
  .stats-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }
  .stats-row > div {
    padding: 14px;
    background: #fafbf7;
    border: 1px solid #e6ebdf;
    border-radius: 7px;
  }
  .stats-row > div > span {
    font-size: 9px;
    color: #7c8674;
  }
  .stats-row strong {
    display: flex;
    align-items: baseline;
    gap: 7px;
    font-size: 25px;
    font-weight: 550;
    letter-spacing: -0.8px;
    margin: 9px 0 7px;
  }
  .stats-row strong > span {
    font-size: 8px;
    letter-spacing: 0;
    color: #97a08f;
    font-weight: 400;
  }
  .stats-row small {
    display: flex;
    align-items: center;
    gap: 3px;
    font-size: 8px;
    color: #5d804f;
  }
  .stats-row small > span {
    color: #99a08e;
    margin-left: 3px;
  }
  .chart-panel {
    border: 1px solid #e6ebdf;
    border-radius: 7px;
    margin-top: 12px;
    padding: 14px 14px 5px;
  }
  .chart-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 14px;
  }
  h4 {
    font-size: 12px;
    font-weight: 550;
    margin: 4px 0 0;
  }
  .period-picker {
    display: flex;
    padding: 3px;
    border-radius: 5px;
    background: #f2f4ed;
    gap: 2px;
  }
  .period-picker button {
    border: 0;
    background: none;
    color: #8e9885;
    padding: 4px 6px;
    font-size: 8px;
    border-radius: 3px;
  }
  .period-picker button.chosen {
    color: #536b45;
    background: white;
    box-shadow: 0 1px 3px #0000000c;
  }
  .chart-legend {
    display: flex;
    align-items: center;
    gap: 13px;
    margin-bottom: 15px;
    font-size: 8px;
    color: #85907b;
  }
  .chart-legend > span {
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .chart-legend i {
    width: 5px;
    height: 5px;
    border-radius: 50%;
  }
  .chart-legend .index-label {
    margin-left: auto;
    font-size: 7px;
  }
  .dashboard-bottom {
    display: flex;
    justify-content: space-between;
    padding: 15px 0 12px;
    gap: 10px;
  }
  .dashboard-bottom > span {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 9px;
    color: #8b9681;
  }
  .dashboard-bottom strong {
    display: block;
    margin-top: 4px;
    font-weight: 500;
    color: #667d58;
  }
  .tiny-market-icon {
    width: 31px;
    height: 31px;
    background: #eff3e8;
    border-radius: 7px;
    display: grid;
    place-items: center;
    color: #8a9f77;
  }
  .market-bars {
    display: flex;
    align-items: flex-end;
    gap: 4px;
    height: 37px;
  }
  .market-bars i {
    width: 8px;
    background: #d3e2bf;
    border-radius: 2px 2px 0 0;
  }
  .market-bars i:nth-last-child(-n + 4) {
    background: #86a66c;
  }
  .dashboard-footnote {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
    padding-top: 10px;
    border-top: 1px solid #edf0e7;
    color: #949e8b;
    font-size: 8px;
  }
  .heatmap-panel {
    min-height: 360px;
    padding: 16px;
    border: 1px solid #e6ebdf;
    border-radius: 7px;
  }
  .map-legend {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: 14px;
    font-size: 8px;
    color: #839076;
    gap: 8px;
  }
  .map-legend > div {
    display: flex;
    align-items: center;
    gap: 3px;
  }
  .map-legend i {
    width: 11px;
    height: 7px;
  }
  .map-legend > div > span {
    margin: 0 4px;
  }
  @media (max-width: 600px) {
    .dashboard-topbar {
      padding: 0 13px;
      height: 54px;
    }
    .demo-label {
      font-size: 6px;
      letter-spacing: 0.6px;
    }
    .avatar {
      display: none;
    }
    .dashboard-sidebar {
      display: none;
    }
    .dashboard-main {
      padding: 18px 14px 12px;
    }
    .dashboard-tabs {
      gap: 14px;
    }
    .dashboard-tabs button {
      font-size: 9px;
      gap: 4px;
    }
    .stats-row strong {
      font-size: 24px;
    }
    .stats-row strong > span {
      font-size: 7px;
    }
    .stats-row > div {
      padding: 12px 10px;
    }
    .heatmap-panel {
      min-height: 280px;
      padding: 12px;
    }
  }
</style>
