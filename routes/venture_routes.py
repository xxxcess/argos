"""Public Venture route module.

The governed facade retains the legacy route surface and the current
Venture integrations, including Live Capture and Meeting Brief.
"""

from routes.meeting_brief_routes import setup_meeting_brief_routes
from routes.venture_routes_legacy import *  # noqa: F401,F403
from routes.venture_routes_governed import setup_venture_routes as _setup_governed_routes


def setup_venture_routes(session_manager):
    router = _setup_governed_routes(session_manager)
    router.include_router(setup_meeting_brief_routes(session_manager))
    return router
