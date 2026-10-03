export const cities = [
  {
    name: 'London',
    country: 'United Kingdom',
    code: 'GB',
    price: 12480,
    growth: 5.8,
    yield: 4.1,
    color: '#547653',
    trend: [99, 101, 100, 103, 102, 106, 105, 109, 108, 112, 111, 115]
  },
  {
    name: 'New York',
    country: 'United States',
    code: 'US',
    price: 16250,
    growth: 4.2,
    yield: 3.8,
    color: '#6e84a3',
    trend: [100, 102, 99, 102, 104, 102, 107, 105, 109, 107, 110, 112]
  },
  {
    name: 'Dubai',
    country: 'United Arab Emirates',
    code: 'AE',
    price: 5980,
    growth: 12.4,
    yield: 6.8,
    color: '#bd9261',
    trend: [94, 98, 101, 99, 105, 110, 109, 115, 119, 118, 125, 132]
  },
  {
    name: 'Singapore',
    country: 'Singapore',
    code: 'SG',
    price: 18720,
    growth: 3.6,
    yield: 3.2,
    color: '#8a79a3',
    trend: [102, 100, 103, 105, 104, 107, 106, 108, 111, 110, 112, 114]
  },
  {
    name: 'Cairo',
    country: 'Egypt',
    code: 'EG',
    price: 1120,
    growth: 9.7,
    yield: 6.2,
    color: '#bc826b',
    trend: [95, 98, 96, 102, 106, 103, 110, 114, 112, 119, 120, 126]
  },
  {
    name: 'Berlin',
    country: 'Germany',
    code: 'DE',
    price: 6840,
    growth: 2.1,
    yield: 3.6,
    color: '#648a85',
    trend: [105, 102, 104, 101, 103, 104, 102, 105, 106, 104, 107, 109]
  }
];

export const heatmapPalettes: Record<string, string[]> = {
  price: ['#dce7cd', '#bacd97', '#95b572', '#709250', '#4d703c'],
  growth: ['#ebdfbf', '#d9c58e', '#c4a660', '#aa8945', '#876831'],
  yield: ['#d8e8e0', '#aecdc0', '#82b4a0', '#5c957f', '#38775e']
};

export type City = (typeof cities)[number];
export type Period = '3M' | '6M' | '1Y';
export const months = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec'
];
export const formatPrice = (price: number) =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0
  }).format(price);
