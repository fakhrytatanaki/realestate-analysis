<script lang="ts">
  import { heatmapPalettes } from '$lib/market-data';
  let { metric = 'price', compact = false } = $props<{ metric?: string; compact?: boolean }>();
  const colors = $derived(heatmapPalettes[metric] ?? heatmapPalettes.price);
</script>

<div class:compact class="map-illustration">
  <svg
    viewBox="0 0 520 240"
    role="img"
    aria-label={`Illustrative London neighborhood ${metric} heatmap. Darker blocks represent higher values.`}
  >
    <rect width="520" height="240" fill="#f1f2e9" />
    <g transform="translate(5 -54) rotate(-18 260 120)">
      {#each Array.from({ length: 8 }) as _, row}
        {#each Array.from({ length: 13 }) as _, col}
          <rect
            x={col * 46}
            y={row * 39}
            width={36 + (col % 3) * 2}
            height={29 + (row % 2) * 2}
            rx="3"
            fill={colors[(row * 7 + col * 3 + Math.floor(col / 3)) % 5]}
            fill-opacity={0.55 + ((row + col) % 3) * 0.15}
          />
        {/each}
      {/each}
      <path d="M50 0h40v320H50zM300 0h13v320h-13zM0 156h650v13H0z" fill="#f1f2e9" />
      <rect x="94" y="62" width="83" height="86" rx="12" fill="#d7e2c7" />
      <path
        d="M-10 255c75-90 126 62 230-18 78-59 100 6 184-17 50-14 91-52 150-18"
        fill="none"
        stroke="#f1f2e9"
        stroke-width="32"
      />
      <path
        d="M-10 255c75-90 126 62 230-18 78-59 100 6 184-17 50-14 91-52 150-18"
        fill="none"
        stroke="#d0e0df"
        stroke-width="22"
      />
    </g>
    <text x="98" y="86" fill="#52654c" font-size="9" letter-spacing="1">HYDE PARK</text>
    <text x="324" y="42" fill="#3e5237" font-size="10" letter-spacing="1.2">SOHO</text>
    <text x="202" y="137" fill="#3e5237" font-size="10" letter-spacing="1.2">MAYFAIR</text>
    <text x="369" y="164" fill="#3e5237" font-size="10" letter-spacing="1.2">WESTMINSTER</text>
    <circle cx="277" cy="96" r="12" fill="white" fill-opacity=".55" />
    <circle cx="277" cy="96" r="5" fill="#294e36" stroke="white" stroke-width="2" />
    {#if !compact}
      <rect x="298" y="72" width="125" height="44" rx="7" fill="white" />
      <text x="310" y="89" fill="#6b7568" font-size="9">Mayfair · Sample data</text>
      <text x="310" y="105" fill="#203d32" font-size="12" font-weight="600"
        >{metric === 'price'
          ? '$18,420 / m²'
          : metric === 'growth'
            ? '+7.2% year over year'
            : '4.8% rental yield'}</text
      >
    {/if}
  </svg>
</div>

<style>
  .map-illustration {
    overflow: hidden;
    border-radius: 8px;
  }
  svg {
    display: block;
    width: 100%;
    font-family: var(--font-sans);
  }
  .compact svg {
    height: 180px;
    object-fit: cover;
  }
</style>
