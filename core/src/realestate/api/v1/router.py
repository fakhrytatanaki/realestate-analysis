"""Version 1 router assembly."""

from __future__ import annotations

from fastapi import APIRouter

from realestate.api.v1 import auth, health, listings, markets, runs, sources

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(listings.router)
api_router.include_router(markets.router)
api_router.include_router(sources.router)
api_router.include_router(runs.router)
