/** Shapes returned by the core API (`/api/v1`). */

export type User = {
  id: string;
  email: string;
  display_name: string;
  created_at: string;
};

export type Session = {
  token: string;
  expires_at: string;
  user: User;
};

export type ListingType = 'SALE' | 'RENT';
export type TrendMetric = 'median_price' | 'median_price_per_sqm';
export type TrendInterval = 'week' | 'month' | 'quarter';

export type RegionSummary = {
  country_code: string;
  city: string;
  district: string | null;
  listing_count: number;
  observation_count: number;
  first_observed: string;
  last_observed: string;
};

export type TrendPoint = {
  period_start: string;
  sample_size: number;
  /** Decimals arrive as strings; null below the sample threshold. */
  value: string | null;
  p25: string | null;
  p75: string | null;
};

export type TrendSeries = {
  region_label: string;
  city: string;
  district: string | null;
  metric: TrendMetric;
  interval: TrendInterval;
  currency: string;
  points: TrendPoint[];
};
