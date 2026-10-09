"""API route registry.

The route families live in focused modules so provider, EPG, sports, guide,
and output concerns can evolve independently without turning this file back
into a monolith.
"""

# Kept as a compatibility import because existing tests and external tooling
# patch ``api.routes.shutil.which`` to simulate ffmpeg availability. Python's
# module cache means that patch still reaches the shared media.ffmpeg helper.
import shutil  # noqa: F401

from .epg import register_epg_routes
from .dvr import register_dvr_routes
from .event_images import register_event_image_routes
from .groups import register_group_routes
from .guide import register_guide_routes
from .guide_debug import register_guide_debug_routes
from .hdhr import register_hdhr_routes
from .images import register_image_routes
from .logo_cache_status import register_logo_cache_status_routes
from .media_pipeline import register_media_pipeline_routes
from .network_config import register_network_config_routes
from .movie_lighting import register_movie_lighting_routes
from .outputs import register_output_routes
from .providers import register_provider_routes
from .remote import register_remote_routes
from .sports_routes import register_sports_routes
# Import onboarding before ui_status so its Jellyfin post-update wrapper sits
# inside the existing master-update reporting wrapper.
from .onboarding import register_onboarding_routes
from .custom_channels import register_custom_channel_routes
from .movie_channels import register_movie_channel_routes
from .roku_app import register_roku_app_routes
from .ui_status import register_ui_status_routes
from .vpn import register_vpn_routes
from .stream_monitor import register_stream_monitor_routes


def register_routes(app):
    register_stream_monitor_routes(app)
    from media.upstream_relay import register_routes as register_relay
    register_relay(app)
    register_custom_channel_routes(app)
    register_movie_channel_routes(app)
    register_provider_routes(app)
    register_epg_routes(app)
    register_group_routes(app)
    register_sports_routes(app)
    register_remote_routes(app)
    register_guide_routes(app)
    register_roku_app_routes(app)
    register_dvr_routes(app)
    register_guide_debug_routes(app)
    register_hdhr_routes(app)
    register_image_routes(app)
    register_event_image_routes(app)
    register_logo_cache_status_routes(app)
    register_media_pipeline_routes(app)
    register_network_config_routes(app)
    register_movie_lighting_routes(app)
    register_output_routes(app)
    register_onboarding_routes(app)
    register_ui_status_routes(app)
    register_vpn_routes(app)
