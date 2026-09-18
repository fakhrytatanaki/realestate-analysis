"""Great-circle helpers for radius search.

Deliberately plain arithmetic rather than PostGIS: the bounding box does the
index work and the haversine term does the precision, which is accurate enough
for "flats within 5 km" and keeps the stack on a stock Postgres image.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

#: Mean Earth radius in kilometres.
EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Latitude/longitude envelope fully containing a circle."""

    min_latitude: Decimal
    max_latitude: Decimal
    min_longitude: Decimal
    max_longitude: Decimal
    #: True when the box spans the entire longitude range (near a pole, or a
    #: radius big enough to wrap), in which case longitude must not be filtered.
    spans_all_longitudes: bool = False


def bounding_box(latitude: Decimal, longitude: Decimal, radius_km: float) -> BoundingBox:
    """Envelope around a point.

    The longitude delta widens with latitude (``/ cos(lat)``) because meridians
    converge towards the poles; without that scaling the box would be too narrow
    and would silently drop matches.
    """
    lat = float(latitude)
    lon = float(longitude)
    lat_delta = math.degrees(radius_km / EARTH_RADIUS_KM)

    min_lat = max(lat - lat_delta, -90.0)
    max_lat = min(lat + lat_delta, 90.0)

    # Use the latitude closest to a pole, so the box stays a superset of the circle.
    worst_lat = max(abs(min_lat), abs(max_lat))
    cos_lat = math.cos(math.radians(worst_lat))
    if cos_lat <= 1e-9 or lat_delta / cos_lat >= 180.0:
        return BoundingBox(
            min_latitude=_floor(min_lat),
            max_latitude=_ceil(max_lat),
            min_longitude=Decimal("-180"),
            max_longitude=Decimal("180"),
            spans_all_longitudes=True,
        )

    lon_delta = lat_delta / cos_lat
    min_lon = lon - lon_delta
    max_lon = lon + lon_delta
    # Antimeridian wrap: fall back to the full range rather than emit an
    # inverted BETWEEN that would match nothing.
    if min_lon < -180.0 or max_lon > 180.0:
        return BoundingBox(
            min_latitude=_floor(min_lat),
            max_latitude=_ceil(max_lat),
            min_longitude=Decimal("-180"),
            max_longitude=Decimal("180"),
            spans_all_longitudes=True,
        )

    return BoundingBox(
        min_latitude=_floor(min_lat),
        max_latitude=_ceil(max_lat),
        min_longitude=_floor(min_lon),
        max_longitude=_ceil(max_lon),
    )


def haversine_km(
    lat1: float | Decimal, lon1: float | Decimal, lat2: float | Decimal, lon2: float | Decimal
) -> float:
    """Great-circle distance in kilometres between two points."""
    phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
    d_phi = phi2 - phi1
    d_lambda = math.radians(float(lon2) - float(lon1))
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def haversine_sql(latitude: float, longitude: float, *, table: str = "listing") -> str:
    """SQL expression computing the distance from a fixed point, in kilometres.

    Coordinates are interpolated as float literals rather than bound parameters
    because Tortoise's ``RawSQL`` has no parameter slot; they are floats by the
    time they get here, so there is nothing to inject.
    """
    lat = float(latitude)
    lon = float(longitude)
    lat_col = f'CAST("{table}"."latitude" AS double precision)'
    lon_col = f'CAST("{table}"."longitude" AS double precision)'
    return (
        f"(2 * {EARTH_RADIUS_KM} * asin(sqrt("
        f"power(sin(radians({lat_col} - {lat!r}) / 2), 2)"
        f" + cos(radians({lat!r})) * cos(radians({lat_col}))"
        f" * power(sin(radians({lon_col} - {lon!r}) / 2), 2)"
        f")))"
    )


#: Precision of the stored latitude/longitude columns.
_PRECISION = Decimal("0.000001")


def _floor(value: float) -> Decimal:
    """Round a lower bound outward (down) to the stored precision."""
    return Decimal(repr(value)).quantize(_PRECISION, rounding=ROUND_FLOOR)


def _ceil(value: float) -> Decimal:
    """Round an upper bound outward (up) to the stored precision.

    Bounds must always widen, never narrow: the box is an indexed prefilter, so
    anything it excludes can never be recovered by the exact distance check.
    """
    return Decimal(repr(value)).quantize(_PRECISION, rounding=ROUND_CEILING)
