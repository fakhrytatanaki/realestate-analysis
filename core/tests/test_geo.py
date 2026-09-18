"""Bounding box and haversine maths."""

from __future__ import annotations

from decimal import Decimal

import pytest

from realestate.infrastructure.db.geo import bounding_box, haversine_km

CAIRO = (30.0444, 31.2357)
ALEXANDRIA = (31.2001, 29.9187)
SEATTLE = (47.6145, -122.3448)


def test_distance_to_self_is_zero() -> None:
    assert haversine_km(*CAIRO, *CAIRO) == pytest.approx(0.0, abs=1e-9)


def test_known_distance_is_accurate() -> None:
    """Cairo to Alexandria is about 180 km."""
    assert haversine_km(*CAIRO, *ALEXANDRIA) == pytest.approx(180.0, abs=2.0)


def test_distance_is_symmetric() -> None:
    assert haversine_km(*CAIRO, *SEATTLE) == pytest.approx(haversine_km(*SEATTLE, *CAIRO))


def test_box_contains_the_query_point() -> None:
    box = bounding_box(Decimal("30.0444"), Decimal("31.2357"), 10)

    assert box.min_latitude < Decimal("30.0444") < box.max_latitude
    assert box.min_longitude < Decimal("31.2357") < box.max_longitude


def test_longitude_span_widens_with_latitude() -> None:
    """Meridians converge towards the poles, so the same radius needs a wider
    longitude window further north -- otherwise matches are silently dropped."""
    equator = bounding_box(Decimal("0"), Decimal("0"), 50)
    northern = bounding_box(Decimal("60"), Decimal("0"), 50)

    equator_span = equator.max_longitude - equator.min_longitude
    northern_span = northern.max_longitude - northern.min_longitude
    assert northern_span > equator_span * Decimal("1.5")


def test_box_is_a_superset_of_the_circle() -> None:
    """Every point within the radius must fall inside the box, since the box is
    the indexed prefilter and anything it excludes is lost for good."""
    import math

    lat, lon, radius = Decimal("30.0444"), Decimal("31.2357"), 25.0
    box = bounding_box(lat, lon, radius)

    for bearing_degrees in range(0, 360, 5):
        bearing = math.radians(bearing_degrees)
        # Project `radius` km along this bearing.
        d_lat = math.degrees((radius / 6371.0088) * math.cos(bearing))
        d_lon = math.degrees(
            (radius / 6371.0088) * math.sin(bearing) / math.cos(math.radians(float(lat)))
        )
        point_lat = float(lat) + d_lat
        point_lon = float(lon) + d_lon

        assert float(box.min_latitude) <= point_lat <= float(box.max_latitude)
        assert float(box.min_longitude) <= point_lon <= float(box.max_longitude)


def test_polar_radius_disables_longitude_filtering() -> None:
    """Near a pole the longitude window degenerates, so the box must fall back
    to the full range rather than emit an inverted comparison."""
    box = bounding_box(Decimal("89.9"), Decimal("0"), 100)

    assert box.spans_all_longitudes
    assert box.min_longitude == Decimal("-180")


def test_antimeridian_crossing_disables_longitude_filtering() -> None:
    box = bounding_box(Decimal("0"), Decimal("179.99"), 100)

    assert box.spans_all_longitudes
