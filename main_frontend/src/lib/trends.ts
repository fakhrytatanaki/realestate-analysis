/** Trends page state, URL encoding and data shaping. Shared by server load and UI. */
import type {
  ListingType,
  RegionSummary,
  TrendInterval,
  TrendMetric,
  TrendSeries
} from '$lib/types';

export const MAX_REGIONS = 4;
/** Must not exceed MAX_RANGE_YEARS in core/application/dto/market.py. */
export const MAX_RANGE_YEARS = 25;
/** Places combined into one line; must not exceed MAX_PLACES in the same module. */
export const MAX_PLACES = 50;
export const COUNTRY_LABEL = 'All of Egypt';
/**
 * Validated with the dataviz palette checker against the card surface (#fbfcf8),
 * all pairs: the landing's muted hues re-stepped for chroma and separation.
 * Green/ochre is a protan WARN (ΔE 6.1), relieved by the legend, chips and table.
 */
export const SERIES_COLORS = ['#3e7b45', '#5b8fd6', '#c0782b', '#6a3d85'];

export const PROPERTY_TYPES = [
  ['', 'All property types'],
  ['APARTMENT', 'Apartments'],
  ['VILLA', 'Villas'],
  ['TOWNHOUSE', 'Townhouses'],
  ['STUDIO', 'Studios'],
  ['HOUSE', 'Houses'],
  ['LAND', 'Land'],
  ['OFFICE', 'Offices'],
  ['SHOP', 'Shops']
] as const;

/** A whole city when `district` is null. */
export type Place = { city: string; district: string | null };
/** One chart line: its places pooled into one median. Empty means the whole country. */
export type Region = Place[];

export type TrendState = {
  type: ListingType;
  metric: TrendMetric;
  interval: TrendInterval;
  ptype: string;
  from: string | null;
  to: string | null;
  regions: Region[];
};

export type City = {
  name: string;
  listingCount: number;
  districts: { name: string; listingCount: number }[];
};

const oneOf = <T extends string>(value: string | null, allowed: readonly T[], fallback: T): T =>
  allowed.includes(value as T) ? (value as T) : fallback;

const isoDate = /^\d{4}-\d{2}-\d{2}$/;

/** `City` or `City/District`: the core API's `place` parameter. */
export function placeKey(place: Place): string {
  return place.district ? `${place.city}/${place.district}` : place.city;
}

export function placeLabel(place: Place): string {
  return place.district ? `${place.city} · ${place.district}` : place.city;
}

/** Whether every advert in `inner` is also in `outer`. */
export function covers(outer: Place, inner: Place): boolean {
  return (
    outer.city === inner.city && (outer.district === null || outer.district === inner.district)
  );
}

/**
 * Drop repeats and places another one already covers, keeping first-seen order;
 * mirrors `pool_places` in core. Pooling is a union, so Cairo + Cairo · Maadi is Cairo.
 */
export function poolPlaces(places: Place[]): Region {
  const unique = [...new Map(places.map((place) => [placeKey(place), place])).values()];
  return unique.filter((place) => !unique.some((other) => other !== place && covers(other, place)));
}

/** Order-insensitive identity: the same places in another order are the same line. */
export function regionId(region: Region): string {
  return region.map(placeKey).sort().join('|') || '*';
}

export function regionLabel(region: Region): string {
  return region.map(placeLabel).join(' + ') || COUNTRY_LABEL;
}

/** Drop lines that repeat an earlier one. */
export function uniqueRegions(regions: Region[]): Region[] {
  const seen = new Set<string>();
  return regions.filter((region) => {
    const id = regionId(region);
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

function parsePlace(raw: string): Place | null {
  const [city, ...rest] = raw.split('/');
  if (!city?.trim()) return null;
  const district = rest.join('/').trim();
  return { city: city.trim(), district: district || null };
}

/** URL form: places joined by `|`, or `*` for the whole country. */
function encodeRegion(region: Region): string {
  return region.map(placeKey).join('|') || '*';
}

function parseRegion(raw: string): Region | null {
  if (raw.trim() === '*') return [];
  const places = raw.split('|').flatMap((part) => parsePlace(part) ?? []);
  return places.length ? poolPlaces(places).slice(0, MAX_PLACES) : null;
}

export function parseState(params: URLSearchParams): TrendState {
  const parsed = params.getAll('region').flatMap((raw) => {
    const region = parseRegion(raw);
    return region ? [region] : [];
  });
  const regions = uniqueRegions(parsed).slice(0, MAX_REGIONS);
  const from = params.get('from');
  const to = params.get('to');
  return {
    type: oneOf(params.get('type'), ['SALE', 'RENT'] as const, 'SALE'),
    metric: oneOf(
      params.get('metric'),
      ['median_price', 'median_price_per_sqm'] as const,
      'median_price'
    ),
    interval: oneOf(params.get('interval'), ['week', 'month', 'quarter'] as const, 'quarter'),
    ptype: oneOf(
      params.get('ptype'),
      PROPERTY_TYPES.map(([value]) => value),
      ''
    ),
    from: from && isoDate.test(from) ? from : null,
    to: to && isoDate.test(to) ? to : null,
    regions
  };
}

/** Inverse of parseState; defaults are omitted to keep shared URLs short. */
export function stateToSearch(state: TrendState): string {
  const params = new URLSearchParams();
  const countryOnly = state.regions.length === 1 && !state.regions[0].length;
  if (!countryOnly)
    for (const region of state.regions) params.append('region', encodeRegion(region));
  if (state.type !== 'SALE') params.set('type', state.type);
  if (state.metric !== 'median_price') params.set('metric', state.metric);
  if (state.interval !== 'quarter') params.set('interval', state.interval);
  if (state.ptype) params.set('ptype', state.ptype);
  if (state.from) params.set('from', state.from);
  if (state.to) params.set('to', state.to);
  const search = params.toString();
  return search ? `?${search}` : '';
}

export function buildCities(regions: RegionSummary[]): City[] {
  const byCity = new Map<string, City>();
  for (const region of regions) {
    const city = byCity.get(region.city) ?? { name: region.city, listingCount: 0, districts: [] };
    city.listingCount += region.listing_count;
    if (region.district) {
      city.districts.push({ name: region.district, listingCount: region.listing_count });
    }
    byCity.set(region.city, city);
  }
  const cities = [...byCity.values()].sort((a, b) => b.listingCount - a.listingCount);
  for (const city of cities) city.districts.sort((a, b) => b.listingCount - a.listingCount);
  return cities;
}

/** Observed date span (YYYY-MM-DD) of the given regions, or of everything when none match. */
export function dataSpan(
  regions: RegionSummary[],
  selected: Region[]
): { first: string; last: string } | null {
  const matches = (summary: RegionSummary) =>
    selected.some((region) => !region.length || region.some((place) => covers(place, summary)));
  const pool = regions.filter(matches).length ? regions.filter(matches) : regions;
  if (!pool.length) return null;
  const first = pool.reduce(
    (min, r) => (r.first_observed < min ? r.first_observed : min),
    pool[0].first_observed
  );
  const last = pool.reduce(
    (max, r) => (r.last_observed > max ? r.last_observed : max),
    pool[0].last_observed
  );
  return { first: first.slice(0, 10), last: last.slice(0, 10) };
}

export function shiftYears(day: string, years: number): string {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCFullYear(date.getUTCFullYear() + years);
  return date.toISOString().slice(0, 10);
}

export function clampRange(from: string, to: string): { from: string; to: string } {
  if (from > to) [from, to] = [to, from];
  const earliest = shiftYears(to, -MAX_RANGE_YEARS);
  return { from: from < earliest ? earliest : from, to };
}

// -- chart data ---------------------------------------------------------------

export type ChartSeries = { key: string; label: string; color: string };
/** A loaded series with the label and colour of the line it was requested for. */
export type Line = { series: TrendSeries; label: string; color: string };
export type ChartRow = { date: Date } & Record<string, number | null | Date>;

function nextPeriod(date: Date, interval: TrendInterval): Date {
  const next = new Date(date);
  if (interval === 'week') next.setUTCDate(next.getUTCDate() + 7);
  else next.setUTCMonth(next.getUTCMonth() + (interval === 'quarter' ? 3 : 1));
  return next;
}

/**
 * Wide rows, one per period, with `s{i}`, `s{i}_n`, `s{i}_p25`, `s{i}_p75` columns.
 *
 * Periods with no observations at all are filled in as null rows: the API only
 * returns buckets that exist, and without the filler a line would be drawn
 * straight across years of missing data as if prices moved smoothly through them.
 */
export function mergeSeries(
  lines: Line[],
  interval: TrendInterval
): { rows: ChartRow[]; keys: ChartSeries[] } {
  const rows = new Map<number, ChartRow>();
  const keys = lines.map(({ label, color }, index) => ({ key: `s${index}`, label, color }));
  lines.forEach(({ series: item }, index) => {
    for (const point of item.points) {
      const date = new Date(point.period_start);
      const row = rows.get(date.getTime()) ?? ({ date } as ChartRow);
      row[`s${index}`] = point.value === null ? null : Number(point.value);
      row[`s${index}_n`] = point.sample_size;
      row[`s${index}_p25`] = point.p25 === null ? null : Number(point.p25);
      row[`s${index}_p75`] = point.p75 === null ? null : Number(point.p75);
      rows.set(date.getTime(), row);
    }
  });
  const times = [...rows.keys()].sort((a, b) => a - b);
  if (times.length > 1) {
    // Bucket starts from the API are aligned, so stepping from the first lands on each.
    for (let at = new Date(times[0]); at.getTime() < times[times.length - 1];) {
      at = nextPeriod(at, interval);
      if (!rows.has(at.getTime())) rows.set(at.getTime(), { date: new Date(at) } as ChartRow);
    }
  }
  const sorted = [...rows.values()].sort((a, b) => a.date.getTime() - b.date.getTime());
  // A series missing from a period must read as a gap, not as "no key".
  for (const row of sorted) {
    for (const { key } of keys) {
      row[key] ??= null;
      row[`${key}_p25`] ??= null;
      row[`${key}_p75`] ??= null;
    }
  }
  return { rows: sorted, keys };
}

export type SeriesSummary = {
  label: string;
  color: string;
  latest: number | null;
  latestDate: Date | null;
  change: number | null;
  samples: number;
  hidden: number;
};

export function summarise({ series: item, label, color }: Line): SeriesSummary {
  const valued = item.points.filter((point) => point.value !== null);
  const first = valued[0];
  const last = valued[valued.length - 1];
  return {
    label,
    color,
    latest: last ? Number(last.value) : null,
    latestDate: last ? new Date(last.period_start) : null,
    change:
      first && last && first !== last && Number(first.value) > 0
        ? (Number(last.value) - Number(first.value)) / Number(first.value)
        : null,
    samples: item.points.reduce((sum, point) => sum + point.sample_size, 0),
    hidden: item.points.filter((point) => point.value === null && point.sample_size > 0).length
  };
}

// -- formatting -----------------------------------------------------------------

const compact = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 });
const full = new Intl.NumberFormat('en', { maximumFractionDigits: 0 });

export function formatMoney(value: number | null | undefined, currency: string, exact = false) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${currency} ${exact ? full.format(value) : compact.format(value)}`;
}

export function formatPeriod(date: Date, interval: TrendInterval): string {
  const year = date.getUTCFullYear();
  if (interval === 'quarter') return `Q${Math.floor(date.getUTCMonth() / 3) + 1} ${year}`;
  if (interval === 'month') {
    return date.toLocaleDateString('en', { month: 'short', year: 'numeric', timeZone: 'UTC' });
  }
  return `Week of ${date.toLocaleDateString('en', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })}`;
}

export function formatDay(day: string): string {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString('en', {
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC'
  });
}

export const METRIC_LABEL: Record<TrendMetric, string> = {
  median_price: 'Median asking price',
  median_price_per_sqm: 'Median price per m²'
};
