from flask import jsonify, request
import core
import vpn_config
import vpn_testing
import re
import vpn_runtime
from .http import no_cache

def register_vpn_routes(app):
    @app.get('/api/vpn-state')
    def vpn_runtime_status():
        return no_cache(jsonify(vpn_runtime.status(core.DB_PATH)))
    @app.get('/api/vpn-activity')
    def vpn_activity():
        # Local registries only: safe even while provider traffic is blocked.
        from media import hls,upstream_relay
        import media_pipeline
        with hls._LOCK:
            streams=sum(1 for value in hls._SESSIONS.values() if value.process.poll() is None)
        return no_cache(jsonify(active_streams=streams+upstream_relay.active_count(),active_sessions=media_pipeline.status()['runtime']['active_sessions']))
    @app.post('/api/vpn-control')
    def vpn_power():
        origin=request.headers.get('Origin')
        if origin and origin.rstrip('/')!=request.host_url.rstrip('/'):
            return no_cache(jsonify(error='Use the power button from this app.')),403
        data=request.get_json(silent=True) or {}
        try: result=vpn_runtime.request_control(core.DB_PATH,data.get('enabled'))
        except ValueError as exc:return no_cache(jsonify(error=str(exc))),409
        return no_cache(jsonify(result)),202
    @app.post('/api/vpn-preference')
    def vpn_preference():
        origin=request.headers.get('Origin')
        if origin and origin.rstrip('/')!=request.host_url.rstrip('/'):
            return no_cache(jsonify(error='Change VPN settings from this app.')),403
        data=request.get_json(silent=True) or {}
        try: result=vpn_runtime.set_enabled(core.DB_PATH,data.get('enabled'))
        except ValueError as exc:return no_cache(jsonify(error=str(exc))),409
        return no_cache(jsonify(result)),202 if result.get('control_status')=='pending' else 200
    def session():
        value=request.headers.get('X-VPN-Session','')
        return value if re.fullmatch('[a-f0-9]{32}',value) else 'missing'
    @app.get('/api/vpn-test')
    def vpn_test_status():
        return no_cache(jsonify(vpn_testing.latest(core.DB_PATH,core.DATA_DIR,session())))
    @app.post('/api/vpn-test')
    def vpn_test_start():
        try: result=vpn_testing.create(core.DB_PATH,core.DATA_DIR,session())
        except ValueError as exc: return no_cache(jsonify(error=str(exc))),409
        return no_cache(jsonify(result)),202
    @app.get('/api/vpn-config')
    def vpn_status():
        return no_cache(jsonify(vpn_config.status(core.DB_PATH,core.DATA_DIR,session())))
    @app.delete('/api/vpn-config')
    def vpn_discard():
        data=request.get_json(silent=True) or {}
        return no_cache(jsonify(vpn_config.discard(core.DB_PATH,core.DATA_DIR,session(),data.get('profile_id'))))
    @app.patch('/api/vpn-config')
    def vpn_save():
        data=request.get_json(silent=True)
        if session()=='missing':return no_cache(jsonify(error='Reload the page before uploading a configuration.')),400
        try: result=vpn_config.save(core.DB_PATH,core.DATA_DIR,data,session())
        except ValueError as exc: return no_cache(jsonify(error=str(exc))),400
        return no_cache(jsonify(result))
