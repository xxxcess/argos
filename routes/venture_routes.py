"""Public Venture route module.

The governed facade retains the Quest/Bible lifecycle and the current Venture
integrations: Live Capture, Meeting Brief, and Data Analysis sessions.
"""

from routes.meeting_brief_routes import setup_meeting_brief_routes
from routes.venture_analysis_briefing_routes import setup_venture_analysis_briefing_routes
from routes.venture_analysis_routes import setup_venture_analysis_routes
from routes.venture_routes_legacy import *  # noqa: F401,F403
from routes.venture_routes_governed import setup_venture_routes as _setup_governed_routes


def setup_venture_routes(session_manager):
    router = _setup_governed_routes(session_manager)
    router.include_router(setup_meeting_brief_routes(session_manager))
    # The briefing router is registered first so it replaces the legacy
    # prompt-driven analysis endpoints while preserving session compatibility.
    router.include_router(setup_venture_analysis_briefing_routes(session_manager))
    router.include_router(setup_venture_analysis_routes(session_manager))
    return router
