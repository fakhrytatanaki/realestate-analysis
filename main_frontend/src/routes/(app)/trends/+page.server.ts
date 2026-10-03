import { error } from '@sveltejs/kit';
import { api, ApiError } from '$lib/server/api';
import { buildCities, clampRange, dataSpan, parseState } from '$lib/trends';
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
  if (!state.regions.length && cities[0])
    state.regions = [{ city: cities[0].name, district: null }];

  const span = dataSpan(regions, state.regions);
  if (span) {
    const range = clampRange(state.from ?? span.first, state.to ?? span.last);
    state.from = range.from;
    state.to = range.to;
  }

  // One request per region, in parallel; a failing region does not sink the others.
  const results = await Promise.allSettled(
    state.regions.map((region) => {
      const query = new URLSearchParams({
        country_code: 'EG',
        city: region.city,
        listing_type: state.type,
        metric: state.metric,
        interval: state.interval
      });
      if (region.district) query.set('district', region.district);
      if (state.ptype) query.set('property_type', state.ptype);
      if (state.from) query.set('date_from', state.from);
      if (state.to) query.set('date_to', state.to);
      return api<TrendSeries>(event, `/api/v1/markets/trends?${query}`);
    })
  );

  const errors = results.flatMap((result, index) =>
    result.status === 'rejected'
      ? [
          `${state.regions[index].district ?? state.regions[index].city}: ${
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
