import { error } from '@sveltejs/kit';
import { api, ApiError } from '$lib/server/api';
import { buildCities, clampRange, dataSpan, parseState, placeKey, regionLabel } from '$lib/trends';
import type { RegionSummary, TrendSeries } from '$lib/types';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async (event) => {
  const state = parseState(event.url.searchParams);

  let regions: RegionSummary[];
  try {
    regions = await api<RegionSummary[]>(
      event,
      `/api/v1/markets/regions?country_code=EG&listing_type=${state.type}`
    );
  } catch (cause) {
    if (cause instanceof ApiError) error(cause.status === 401 ? 401 : 503, cause.detail);
    throw cause;
  }

  const cities = buildCities(regions);
  if (!state.regions.length) state.regions = [[]];

  const span = dataSpan(regions, state.regions);
  if (span) {
    const range = clampRange(state.from ?? span.first, state.to ?? span.last);
    state.from = range.from;
    state.to = range.to;
  }

  // One request per line, in parallel; a failing line does not sink the others.
  // A line's places are pooled by the API; a line without places is the whole country.
  const results = await Promise.allSettled(
    state.regions.map((region) => {
      const query = new URLSearchParams({
        country_code: 'EG',
        listing_type: state.type,
        metric: state.metric,
        interval: state.interval
      });
      for (const place of region) query.append('place', placeKey(place));
      if (state.ptype) query.set('property_type', state.ptype);
      if (state.from) query.set('date_from', state.from);
      if (state.to) query.set('date_to', state.to);
      return api<TrendSeries>(event, `/api/v1/markets/trends?${query}`);
    })
  );

  const errors = results.flatMap((result, index) =>
    result.status === 'rejected'
      ? [
          `${regionLabel(state.regions[index])}: ${
            result.reason instanceof ApiError ? result.reason.detail : 'request failed'
          }`
        ]
      : []
  );

  return {
    state,
    cities,
    span,
    series: results.map((result) => (result.status === 'fulfilled' ? result.value : null)),
    errors
  };
};
