"""API route registry.

The route families live in focused modules so provider, EPG, sports, guide,
and output concerns can evolve independently without turning this file back
into a monolith.
"""

from .epg import register_epg_routes
from .groups import register_group_routes
from .guide import register_guide_routes
from .outputs import register_output_routes
from .providers import register_provider_routes
from .sports_routes import register_sports_routes


def register_routes(app):
    register_provider_routes(app)
    register_epg_routes(app)
    register_group_routes(app)
    register_sports_routes(app)
    register_guide_routes(app)
    register_output_routes(app)
