"""Public Venture route module.

The optimized facade keeps the legacy route surface while replacing the
high-churn Quest status, Artifact, Memory, and management endpoints.
"""

from routes.venture_routes_legacy import *  # noqa: F401,F403
from routes.venture_routes_optimized import setup_venture_routes
