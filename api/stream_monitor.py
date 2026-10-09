from flask import jsonify
import core
import media_pipeline
import stream_monitor
import resource_monitor
import vpn_runtime
from settings import load_settings
from .http import no_cache


def register_stream_monitor_routes(app):
    @app.get('/api/streams')
    def streams_status():
        result = stream_monitor.snapshot()
        result['resources'] = resource_monitor.snapshot()
        settings = load_settings()
        vpn = vpn_runtime.status(core.DB_PATH)
        result['network'] = dict(lan_ip=settings.lan_host or '',
            vpn_ip=vpn.get('vpn_public_ip', '') if vpn.get('app_vpn_active') else '',
            vpn_enabled=vpn.get('enabled', False), vpn_connected=vpn.get('app_vpn_active', False),
            route='blocked' if vpn_runtime.protection_missing(core.DB_PATH) else
                  ('vpn' if vpn.get('network_protected') else 'normal'))
        pipeline = media_pipeline.status()['runtime']
        result['ffmpeg_sessions'] = pipeline['active_sessions']
        # These are sessions observed by the app, not the provider's account total.
        from media import upstream_relay, hls
        result['relay_requests'] = upstream_relay.active_count()
        with hls._LOCK:
            result['recoveries'] = sum(s.recovery_count for s in hls._SESSIONS.values())
        return no_cache(jsonify(result))

    @app.after_request
    def monitor_response(response):
        # HLS assets come from existing session registries. Do not call
        # get_session/touch_session, which can initiate recovery work.
        from flask import request
        from media import hls
        parts = request.path.strip('/').split('/')
        if len(parts) == 4 and parts[:2] in (['guide', 'cast'], ['guide', 'roku']):
            with hls._LOCK:
                session = hls._SESSIONS.get(parts[2])
                if session:
                    stream_monitor.attach(response, session.target, 'encoding', 'HLS',
                                          session.pipeline_token, segmented=True)
        try:
            return stream_monitor.observe(response)
        except Exception:
            # Diagnostics must not turn a successful stream into an error.
            app.logger.warning('Stream observation unavailable')
            return response
