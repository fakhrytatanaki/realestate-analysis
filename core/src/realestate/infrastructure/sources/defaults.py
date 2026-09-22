"""Default registry population.

The one place that knows which sources ship with the application. Adding a
portal means importing it here and adding a :meth:`register` call.
"""

from __future__ import annotations

from realestate.infrastructure.sources.dubizzle_eg.source import DubizzleEgDataSource
from realestate.infrastructure.sources.fixture.source import FixtureDataSource
from realestate.infrastructure.sources.registry import DataSourceRegistry, SourceContext
from realestate.infrastructure.sources.zillow.source import ZillowDataSource


def register_default_sources(registry: DataSourceRegistry) -> DataSourceRegistry:
    """Register every source that ships with this build."""
    registry.register(
        key=FixtureDataSource.key,
        display_name=FixtureDataSource.display_name,
        country_code=FixtureDataSource.country_code,
        factory=lambda ctx: FixtureDataSource(
            log=ctx.log, directory=ctx.params.get("directory", "var/fixtures")
        ),
    )
    registry.register(
        key=DubizzleEgDataSource.key,
        display_name=DubizzleEgDataSource.display_name,
        country_code=DubizzleEgDataSource.country_code,
        factory=lambda ctx: DubizzleEgDataSource(log=ctx.log, params=ctx.params),
        implemented=True,
    )
    registry.register(
        key=ZillowDataSource.key,
        display_name=ZillowDataSource.display_name,
        country_code=ZillowDataSource.country_code,
        factory=lambda ctx: ZillowDataSource(log=ctx.log, params=ctx.params),
        implemented=False,
    )
    return registry


__all__ = ["DataSourceRegistry", "SourceContext", "register_default_sources"]
