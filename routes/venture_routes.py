"""Public Venture route module.

The governed facade retains the legacy route surface while replacing high-churn
Quest reads and routing every synthesis action through the single worker queue.
"""

from routes.venture_routes_legacy import *  # noqa: F401,F403
from routes.venture_routes_governed import setup_venture_routes
