"""Backward-compatible exports for independently scoped workflow identities."""
from app.services.briefing_route_identity import (
    BRIEFING_CONTRACT_VERSION,
    BRIEFING_ROUTE_SCOPE,
    briefing_route_identity,
)
from app.services.committee_route_identity import (
    COMMITTEE_CONTRACT_VERSION,
    COMMITTEE_ROUTE_SCOPE,
    committee_route_identity,
)
from app.services.route_identity_common import ROUTE_IDENTITY_SCHEMA_VERSION


__all__ = [
    "BRIEFING_CONTRACT_VERSION",
    "BRIEFING_ROUTE_SCOPE",
    "COMMITTEE_CONTRACT_VERSION",
    "COMMITTEE_ROUTE_SCOPE",
    "ROUTE_IDENTITY_SCHEMA_VERSION",
    "briefing_route_identity",
    "committee_route_identity",
]
