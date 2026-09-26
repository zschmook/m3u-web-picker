import time
from datetime import datetime,timezone
from xml.etree import ElementTree as ET

from flask import Response,has_request_context,jsonify,request,stream_with_context
import core
import custom_channels as service
from media import browser
from media.scheduled_channel import segments_at
from settings import load_settings
from .episode_test import stream_plan

service.install(core)


def local_url(identity):
    service.channel(identity)
    return f'http://127.0.0.1:{load_settings().port}/stream/custom/{identity}.ts'


def programme(p,state):
    ads=state.get('ads') or {}
    return dict(title=p['title'],subtitle='With commercials' if ads.get('enabled') else '',
        description='Custom Plex channel',categories=['TV'],season=p['season'],episode=p['episode'],
        start=datetime.fromtimestamp(p['start'],timezone.utc).isoformat(),
        stop=datetime.fromtimestamp(p['stop'],timezone.utc).isoformat())


def channel_logo(row):
    category=row.get('category_id') if row.get('kind')=='category' else None
    if category not in {identity for identity,_name,_tags in service.CATEGORY_CHANNELS}: return ''
    return f'/static/icons/categories/{category}.png?v=1'


def guide_items():
    if not service.settings()['enabled']: return []
    now=time.time();items=[]
    for row in service.read('channels.json',[]):
        if not row['enabled']: continue
        try: state=service.schedule(row['id'],now)
        except (OSError,ValueError,KeyError): continue
        entries=[programme(p,state) for p in state['programmes'] if p['stop']>now and p['start']<now+5*86400]
        current=next((p for p in entries if datetime.fromisoformat(p['start']).timestamp()<=now),None)
        upcoming=[p for p in entries if p is not current]
        items.append(dict(number=row['number'],name=row['name'],group='Custom Channels',logo=channel_logo(row),
            tvg_id='custom-'+row['id'],generated=True,available=True,play_url='/guide/play/custom/'+row['id'],
            now=current,next=upcoming[0] if upcoming else None,upcoming=upcoming))
    return items


def playout(identity,now):
    while True:
        state=service.schedule(identity,now)
        yield from segments_at(state,now)
        now=state['cursor']


def playlist_lines(base):
    lines=[]
    for item in guide_items():
        name=item['name'].replace('\n',' ').replace('\r',' ')
        logo=f' tvg-logo="{base.rstrip("/")}{item["logo"]}"' if item.get('logo') else ''
        lines.extend([f'#EXTINF:-1 tvg-id="{item["tvg_id"]}" tvg-chno="{item["number"]}"{logo} group-title="Custom Channels",{name}',
            base+'/stream/custom/'+item['tvg_id'].removeprefix('custom-')+'.ts'])
    return lines


def merge_epg(root,items=None):
    """Merge at response time; leave the provider's published XMLTV file intact."""
    items=guide_items() if items is None else items
    identities={item['tvg_id'] for item in items}
    for element in list(root):
        if (element.tag=='channel' and element.get('id') in identities) or (element.tag=='programme' and element.get('channel') in identities): root.remove(element)
    for item in items:
        c=ET.Element('channel',id=item['tvg_id']);ET.SubElement(c,'display-name').text=item['name']
        if item.get('logo'):
            origin=request.url_root.rstrip('/') if has_request_context() else ''
            ET.SubElement(c,'icon',src=origin+item['logo'])
        position=next((i for i,e in enumerate(root) if e.tag=='programme'),len(root))
        root.insert(position,c)
        for entry in ([item['now']] if item['now'] else [])+item['upcoming']:
            stamp=lambda key:datetime.fromisoformat(entry[key]).astimezone(timezone.utc).strftime('%Y%m%d%H%M%S +0000')
            p=ET.SubElement(root,'programme',channel=item['tvg_id'],start=stamp('start'),stop=stamp('stop'))
            ET.SubElement(p,'title').text=entry['title']
            ET.SubElement(p,'desc').text=entry.get('subtitle') or 'Custom Plex channel'
            ET.SubElement(p,'episode-num',system='xmltv_ns').text=f"{entry['season']-1}.{entry['episode']-1}."
    return root


def register_custom_channel_routes(app):
    @app.get('/api/custom-channels/folders')
    def custom_folders():
        try: result=service.browse_folders(request.args.get('path',''))
        except (ValueError,OSError) as exc: return jsonify(error=str(exc)),400
        return jsonify(result)
    @app.get('/api/custom-channels')
    def custom_payload():
        response=jsonify(service.payload());response.headers['Cache-Control']='no-store';return response

    @app.get('/api/custom-channels/shows/<identity>')
    def custom_show(identity):
        show=next((s for s in service.read('catalog.json',{}).get('shows',[]) if s['id']==identity),None)
        if not show: return jsonify(error='Show not found'),404
        return jsonify(service.summary(show,detailed=True))

    @app.patch('/api/custom-channels/settings')
    def custom_settings():
        try: service.save_settings(request.get_json(silent=True) or {})
        except (ValueError,TypeError) as exc: return jsonify(error=str(exc)),400
        return jsonify(service.payload())

    @app.post('/api/custom-channels/discover')
    def custom_discover():
        try: service.start_discovery()
        except ValueError as exc: return jsonify(error=str(exc)),400
        return jsonify(job=dict(service.JOB)),202

    @app.post('/api/custom-channels/commercial-imports')
    def custom_commercial_import_start():
        data=request.get_json(silent=True) or {}
        try: row=service.start_commercial_import(data.get('name',''),data.get('size'))
        except (ValueError,OSError) as exc: return jsonify(error=str(exc)),400
        return jsonify(import_=row),201

    @app.put('/api/custom-channels/commercial-imports/<identity>')
    def custom_commercial_import_chunk(identity):
        try:
            offset=int(request.headers.get('Upload-Offset',''))
            row=service.append_commercial_import(identity,offset,request.stream)
            if row['status']=='ready': service.start_commercial_processing(identity)
        except (ValueError,OSError) as exc: return jsonify(error=str(exc)),400
        return jsonify(import_=next((item for item in service.commercial_imports() if item['id']==identity),row))

    @app.post('/api/custom-channels/servers')
    def custom_server():
        data=request.get_json(silent=True) or {}
        try: service.connect_server(str(data.get('url','')).strip(),str(data.get('token','')).strip())
        except ValueError as exc: return jsonify(error=str(exc)),400
        return jsonify(service.payload())

    @app.post('/api/custom-channels')
    def custom_create():
        try: row=service.create_channel(request.get_json(silent=True) or {})
        except (ValueError,TypeError) as exc: return jsonify(error=str(exc)),400
        return jsonify(channel=row),201

    @app.post('/api/custom-channels/bulk')
    def custom_create_bulk():
        try: result=service.create_channels(request.get_json(silent=True) or {})
        except (ValueError,TypeError) as exc: return jsonify(error=str(exc)),400
        return jsonify(result),201

    @app.delete('/api/custom-channels/bulk')
    def custom_delete_bulk():
        try: result=service.delete_channels(request.get_json(silent=True) or {})
        except (ValueError,TypeError) as exc: return jsonify(error=str(exc)),400
        return jsonify(result)

    @app.patch('/api/custom-channels/<identity>')
    def custom_change(identity):
        try: service.update_channel(identity,request.get_json(silent=True) or {})
        except (ValueError,TypeError) as exc: return jsonify(error=str(exc)),400
        return jsonify(service.payload())

    @app.delete('/api/custom-channels/<identity>')
    def custom_delete(identity):
        try: service.delete_channel(identity)
        except ValueError as exc: return jsonify(error=str(exc)),404
        return jsonify(service.payload())

    @app.get('/guide/play/custom/<identity>')
    def custom_play(identity):
        try: target=local_url(identity)
        except ValueError: return Response('Channel unavailable',status=404)
        return browser.response_for(target,remux_only=True)

    @app.route('/stream/custom/<identity>.ts',methods=['GET','HEAD'])
    def custom_stream(identity):
        try:
            row=service.channel(identity);service.schedule(identity)
            servers=service.read('servers.json',[])
            server=next((s for s in servers if s['id']==row.get('server_id')),None)
            if row.get('kind')!='category' and not server: raise ValueError('Plex server unavailable')
        except (ValueError,StopIteration,OSError): return Response('Channel unavailable',status=404)
        if request.method=='HEAD': return Response(content_type='video/mp2t')
        now=time.time()
        cfg=dict(wall_clock_anchor=now,servers={s['id']:dict(server=s['url'],token=s['token']) for s in servers})
        if server: cfg.update(server=server['url'],token=server['token'])
        return Response(stream_with_context(stream_plan(playout(identity,now),cfg,
            request.environ.get('waitress.client_disconnected'),channel=row['number'])),
            content_type='video/mp2t',direct_passthrough=True,headers={'Cache-Control':'no-store'})

    @app.get('/playlist/custom-channels.m3u')
    def custom_playlist():
        base=request.url_root.rstrip('/')
        lines=[f'#EXTM3U url-tvg="{base}/epg/epg.xml"',*playlist_lines(base)]
        return Response('\n'.join(lines)+'\n',mimetype='audio/x-mpegurl',headers={'Cache-Control':'no-store'})

    @app.get('/epg/custom-channels.xml')
    def custom_epg():
        root=merge_epg(ET.Element('tv'))
        return Response(ET.tostring(root,encoding='utf-8',xml_declaration=True),mimetype='application/xml')
