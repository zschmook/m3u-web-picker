"""Opt-in Plex catalogs and persistent custom broadcast channels."""
import hashlib
import ipaddress
import json
import math
import os
import re
import secrets
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path, PureWindowsPath

from settings import load_settings
import movie_restart
from media.scheduled_channel import new_schedule, extend_schedule, save_schedule, trim_schedule

LOCK = threading.RLock()
SCAN_LOCK = threading.Lock()
JOB = {'running': False, 'message': ''}
COMMERCIAL_PROCESSING = set()
DEFAULTS = dict(enabled=False, minimum_episodes=30, commercials_enabled=False,
                commercials_folder='', commercial_mode='minutes', commercial_minutes=15,
                commercial_episodes=1, commercial_count=3, preroll=True)
CHANNEL_NUMBER_START = 1000
CHANNEL_NUMBER_END = 1999
CATEGORY_CHANNELS = (
    ('animation','Animation',('Animation',)),
    ('action-adventure','Action & Adventure',('Action','Adventure','Western','War & Politics')),
    ('comedy','Comedy',('Comedy',)),
    ('crime-mystery','Crime & Mystery',('Crime','Mystery','Thriller')),
    ('documentary-history','Documentary & History',('Documentary','History','Biography')),
    ('drama','Drama',('Drama',)),
    ('family-kids','Family & Kids',('Family','Children')),
    ('news-talk','News & Talk',('News','Talk Show','Talk','Podcast')),
    ('reality-game-shows','Reality & Game Shows',('Reality','Game Show')),
    ('scifi-fantasy-horror','Sci-Fi, Fantasy & Horror',('Science Fiction','Fantasy','Horror')),
)
CATEGORY_CHANNEL_NUMBER_START = 1900


def root():
    return Path(load_settings().data_dir)/'custom-channels'


def read(name, default):
    path = root()/name
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def settings():
    cfg={**DEFAULTS, **read('settings.json', {})}
    cfg['commercials_folder']=str(commercial_clips_root())
    return cfg


def commercials_root():
    return Path(os.environ.get('M3U_COMMERCIALS_ROOT','/commercials')).resolve()


def commercial_incoming_root():
    return commercials_root()/'incoming'


def commercial_clips_root():
    return commercials_root()/'clips'


def ensure_commercial_dirs():
    incoming,clips=commercial_incoming_root(),commercial_clips_root()
    incoming.mkdir(parents=True,exist_ok=True);clips.mkdir(parents=True,exist_ok=True);(commercials_root()/'work').mkdir(parents=True,exist_ok=True)
    return incoming,clips


def _import_path(identity,suffix):
    if not re.fullmatch(r'[0-9a-f]{24}',str(identity)): raise ValueError('Commercial import not found.')
    return commercial_incoming_root()/(str(identity)+suffix)


def _public_import(row):
    keys=('id','name','size','received','status','created_at','phase','processed','duration','clips_found','review','message')
    return {key:row[key] for key in keys if key in row}


def commercial_imports():
    incoming,_=ensure_commercial_dirs();rows=[]
    for path in incoming.glob('*.json'):
        try: rows.append(_public_import(json.loads(path.read_text(encoding='utf-8'))))
        except (OSError,ValueError,KeyError,TypeError): continue
    return sorted(rows,key=lambda row:row['created_at'],reverse=True)


def start_commercial_import(name,size):
    name=Path(str(name).replace('\\','/')).name.strip()
    if not name or Path(name).suffix.lower()!='.mp4': raise ValueError('Choose an MP4 file.')
    if isinstance(size,bool) or not isinstance(size,int) or size<=0: raise ValueError('The MP4 is empty.')
    maximum=int(os.environ.get('M3U_MAX_COMMERCIAL_IMPORT_BYTES',str(100*1024**3)))
    if size>maximum: raise ValueError(f'The MP4 exceeds the {maximum//1024**3} GB import limit.')
    with LOCK:
        ensure_commercial_dirs();identity=secrets.token_hex(12)
        row=dict(id=identity,name=name,size=size,received=0,status='copying',created_at=time.time())
        _import_path(identity,'.partial').touch(exist_ok=False)
        save_schedule(_import_path(identity,'.json'),row)
        return _public_import(row)


def append_commercial_import(identity,offset,stream):
    with LOCK:
        metadata=_import_path(identity,'.json')
        if not metadata.exists(): raise ValueError('Commercial import not found.')
        row=json.loads(metadata.read_text(encoding='utf-8'));partial=_import_path(identity,'.partial')
        if row['status']!='copying' or not partial.exists(): raise ValueError('Commercial import is already complete.')
        if offset!=partial.stat().st_size or offset!=row['received']: raise ValueError(f'Resume this import at byte {row["received"]}.')
        remaining=row['size']-offset;written=0
        with partial.open('ab') as target:
            while written<remaining:
                chunk=stream.read(min(1024*1024,remaining-written))
                if not chunk: break
                target.write(chunk);written+=len(chunk)
        if not written and remaining: raise ValueError('No MP4 data was received.')
        row['received']+=written
        if row['received']==row['size']:
            partial.replace(_import_path(identity,'.mp4'));row['status']='ready'
        save_schedule(metadata,row)
        return _public_import(row)


def _clock(seconds):
    seconds=max(0,int(seconds));hours,remainder=divmod(seconds,3600);minutes,seconds=divmod(remainder,60)
    return f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes}:{seconds:02d}'


def _update_commercial_import(identity,**values):
    with LOCK:
        path=_import_path(identity,'.json');row=json.loads(path.read_text(encoding='utf-8'))
        row.update(values);save_schedule(path,row);return row


def _process_commercial_import(identity):
    from media import commercial_processor
    source=_import_path(identity,'.mp4');work=commercials_root()/'work'/('batch-'+identity)
    published=commercial_clips_root()/('batch-'+identity)
    try:
        def progress(phase,current,total,found):
            if phase=='scanning': message=f'Found {found} commercials in {_clock(current)} of {_clock(total)} footage.'
            elif phase=='cutting': message=f'Cutting {found} commercials · {_clock(current)} of {_clock(total)} footage.'
            else: message=f'Validating commercial {int(current)} of {int(total)}.'
            _update_commercial_import(identity,status='processing',phase=phase,processed=current,
                duration=total,clips_found=found,message=message)
        result=commercial_processor.process(source,work,published,progress)
        source.unlink()
        _update_commercial_import(identity,status='complete',phase='complete',processed=result['duration'],
            duration=result['duration'],clips_found=result['clips'],review=result['review'],
            message=f'Found and prepared {result["clips"]} commercials from {_clock(result["duration"])} of footage.')
    except Exception as exc:
        _update_commercial_import(identity,status='failed',phase='failed',message=f'Processing failed: {type(exc).__name__}: {exc}')
    finally:
        with LOCK: COMMERCIAL_PROCESSING.discard(identity)


def start_commercial_processing(identity):
    with LOCK:
        path=_import_path(identity,'.json')
        if not path.exists(): raise ValueError('Commercial import not found.')
        row=json.loads(path.read_text(encoding='utf-8'))
        if row['status']!='ready': return False
        if identity in COMMERCIAL_PROCESSING: return False
        COMMERCIAL_PROCESSING.add(identity)
        _update_commercial_import(identity,status='processing',phase='starting',processed=0,
            duration=0,clips_found=0,message='Starting commercial scan…')
    threading.Thread(target=_process_commercial_import,args=(identity,),daemon=True,
        name='commercial-import-'+identity).start()
    return True


def folder_roots():
    """Only browse media folders explicitly shared with the application."""
    paths = [os.environ.get('M3U_COMMERCIALS_DIR','/commercials/clips')]
    paths.extend(json.loads(os.environ.get('M3U_CUSTOM_MEDIA_DIRS','[]')))
    current = settings()['commercials_folder']
    if current and Path(current).is_dir(): paths.append(current)
    return sorted({Path(p).resolve() for p in paths if p and Path(p).is_dir()},key=str)


def resolve_folder(value):
    value = str(value).strip()
    host = os.environ.get('M3U_COMMERCIALS_HOST_DIR','').strip()
    if host:
        try:
            relative=PureWindowsPath(value).relative_to(PureWindowsPath(host))
            base=Path(os.environ.get('M3U_COMMERCIALS_DIR','/commercials/clips')).resolve()
            mapped=base.joinpath(*relative.parts).resolve()
            if not mapped.is_relative_to(base): raise ValueError('Folder is outside the shared location.')
            value=str(mapped)
        except ValueError:
            pass
    if not value: raise ValueError('Choose your commercials folder.')
    path=Path(value).resolve()
    if not path.is_dir(): raise ValueError('That folder is not available to this server. Choose one using Browse.')
    return path


def folder_label(path):
    host=os.environ.get('M3U_COMMERCIALS_HOST_DIR','').strip()
    if host:
        try:
            relative=path.relative_to(Path(os.environ.get('M3U_COMMERCIALS_DIR','/commercials/clips')).resolve())
            return str(PureWindowsPath(host).joinpath(*relative.parts))
        except ValueError: pass
    return str(path)


def browse_folders(value=''):
    roots=folder_roots()
    def item(path): return dict(path=str(path),label=folder_label(path),name=path.name)
    if not value: return dict(path='',label='Shared media folders',parent=None,folders=[item(p) for p in roots],selectable=False)
    target=resolve_folder(value)
    if not any(target.is_relative_to(base) for base in roots):
        raise ValueError('Choose a folder from the shared media folders.')
    children=[]
    for path in sorted(target.iterdir(),key=lambda p:p.name.casefold()):
        if path.is_dir() and any(path.resolve().is_relative_to(base) for base in roots): children.append(item(path.resolve()))
    return dict(path=str(target),label=folder_label(target),parent='' if target in roots else str(target.parent),
                folders=children,selectable=True)


def save_settings(data):
    with LOCK:
        cfg = settings()
        for key in ('enabled','commercials_enabled','preroll'):
            if key in data:
                if not isinstance(data[key],bool): raise ValueError('Invalid switch value')
                cfg[key] = data[key]
        if data.get('commercials_enabled') is True:
            ensure_commercial_dirs();cfg['commercials_folder']=str(commercial_clips_root())
        for key, low, high in [('minimum_episodes',1,10000),('commercial_minutes',1,240),
                               ('commercial_episodes',1,100),('commercial_count',1,20)]:
            if key in data:
                value = data[key]
                if isinstance(value,bool) or not isinstance(value,int) or not low<=value<=high:
                    raise ValueError(f'{key.replace("_"," ")} must be {low}–{high}')
                cfg[key] = value
        if 'commercial_mode' in data:
            if data['commercial_mode'] not in ('minutes','episodes'): raise ValueError('Invalid commercial interval')
            cfg['commercial_mode'] = data['commercial_mode']
        if 'commercials_folder' in data:
            value=str(data['commercials_folder']).strip()
            cfg['commercials_folder'] = str(resolve_folder(value)) if value else ''
        validate_commercials_folder = data.get('commercials_enabled') is True or (
            'commercials_folder' in data and cfg['commercials_enabled'])
        if validate_commercials_folder and not cfg['commercials_folder']:
            raise ValueError('Specify your own commercials folder')
        if validate_commercials_folder and not Path(cfg['commercials_folder']).is_dir():
            raise ValueError('Your commercials folder is unavailable. Choose a folder using Browse.')
        commercial_keys={'commercials_enabled','commercials_folder','commercial_mode','commercial_minutes',
                         'commercial_episodes','commercial_count','preroll'}
        policy_requested=any(key in data for key in commercial_keys)
        rebuilt=_commercial_policy_schedules(cfg) if policy_requested else []
        save_schedule(root()/'settings.json',cfg)
        if rebuilt:
            updated={row['id']:row for row,_state in rebuilt}
            for row,state in rebuilt: save_schedule(root()/(row['id']+'.json'),state)
            rows=[updated.get(row['id'],row) for row in read('channels.json',[])]
            save_schedule(root()/'channels.json',rebalance_channel_numbers(rows))
        return cfg


def _commercial_policy_schedules(cfg):
    rows=read('channels.json',[])
    if not rows: return []
    assets=commercial_assets(cfg) if cfg['commercials_enabled'] else []
    options=dict(enabled=cfg['commercials_enabled'],mode=cfg['commercial_mode'],minutes=cfg['commercial_minutes'],
                 episodes=cfg['commercial_episodes'],count=cfg['commercial_count'],preroll=cfg['preroll'])
    now=time.time();rebuilt=[]
    for row in rows:
        previous=read(row['id']+'.json',None)
        if not previous or not previous.get('episodes'): continue
        state=new_schedule(previous['episodes'],assets,now,secrets.token_hex(16),
                           order=previous.get('order',row.get('order','random')),ads=options)
        state['until_break']=options['minutes']*60
        extend_schedule(state,now+6*86400)
        rebuilt.append(({**row,'started_at':now},state))
    return rebuilt


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None


def plex_xml(base, path, token):
    req = urllib.request.Request(base.rstrip('/')+path,headers={'X-Plex-Token':token,'Accept':'application/xml'})
    with urllib.request.build_opener(NoRedirect).open(req,timeout=10) as response:
        return ET.fromstring(response.read(32*1024*1024))


def movie_restart_url(server_id, part, title=''):
    """Mint a private guide URL for one Plex movie part."""
    server=next((row for row in read('servers.json',[]) if row.get('id')==server_id),None)
    if not server: raise ValueError('Plex server unavailable')
    return movie_restart.issue_plex(server.get('url',''),server.get('token',''),part,title=title)


def seed_connection():
    path = Path(load_settings().data_dir)/'plex-test-channel.json'
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}


def discover_servers():
    """Use saved connections, Plex account resources, and local GDM discovery."""
    saved = read('servers.json',[])
    seed = seed_connection()
    token = next((s['token'] for s in saved if s.get('token')),seed.get('token',''))
    candidates = list(saved)
    if seed.get('server'):
        candidates.append(dict(url=seed['server'],token=seed.get('token','')))
    if token:
        try:
            resources = plex_xml('https://plex.tv','/api/resources?includeHttps=1',token)
            for device in resources.findall('Device'):
                if 'server' not in device.get('provides','').split(','): continue
                for connection in device.findall('Connection'):
                    if connection.get('local') != '1': continue
                    uri = connection.get('uri','')
                    if urllib.parse.urlsplit(uri).scheme in ('http','https'):
                        candidates.append(dict(url=uri,token=device.get('accessToken') or token))
        except Exception:
            pass  # Local discovery remains usable without plex.tv.
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)
            sock.settimeout(.2)
            for addr in ('239.0.0.250','255.255.255.255'):
                sock.sendto(b'M-SEARCH * HTTP/1.0\r\n\r\n',(addr,32414))
            deadline = time.monotonic()+2
            while time.monotonic()<deadline:
                try: payload,remote = sock.recvfrom(8192)
                except socket.timeout: continue
                if not ipaddress.ip_address(remote[0]).is_private: continue
                headers = dict(line.split(':',1) for line in payload.decode(errors='replace').splitlines() if ':' in line)
                port = next((v.strip() for k,v in headers.items() if k.lower()=='port'),'32400')
                if port.isdigit() and 1<=int(port)<=65535:
                    candidates.append(dict(url=f'http://{remote[0]}:{port}',token=token))
    except OSError:
        pass
    servers = {s['id']:s for s in saved}
    seen = set()
    for candidate in candidates[:60]:
        url = candidate['url'].rstrip('/')
        if url in seen: continue
        seen.add(url)
        try:
            meta = plex_xml(url,'/',candidate.get('token',''))
            identity = meta.get('machineIdentifier')
            if not identity: continue
            # Prefer a currently working connection; keep an inaccessible saved server for its cache.
            if identity in servers and servers[identity].get('_verified'): continue
            servers[identity] = dict(id=identity,name=meta.get('friendlyName','Plex'),url=url,
                                     token=candidate.get('token',''),_verified=True)
        except Exception:
            continue
    result = [{k:v for k,v in s.items() if k!='_verified'} for s in servers.values()]
    save_schedule(root()/'servers.json',result)
    return result


def _library_genres(server,library):
    genres={};offset=0
    while True:
        path=f"/library/sections/{library.get('key')}/all?type=2&X-Plex-Container-Start={offset}&X-Plex-Container-Size=500"
        page=plex_xml(server['url'],path,server['token']);rows=list(page)
        for item in rows:
            rating_key=item.get('ratingKey','')
            if rating_key:
                genres[rating_key]=sorted({node.get('tag','').strip() for node in item.findall('Genre') if node.get('tag','').strip()})
        offset+=len(rows)
        if not rows or offset>=int(page.get('totalSize',str(offset))): break
        if offset>100000: raise ValueError('Catalog exceeds scan limit')
    return genres


def scan_server(server):
    groups = {}
    server['_unavailable_files']=0
    for library in plex_xml(server['url'],'/library/sections',server['token']).findall('Directory'):
        if library.get('type') != 'show': continue
        genre_map=_library_genres(server,library)
        offset = 0
        while True:
            path = f"/library/sections/{library.get('key')}/all?type=4&X-Plex-Container-Start={offset}&X-Plex-Container-Size=500"
            page = plex_xml(server['url'],path,server['token'])
            rows = page.findall('Video')
            checked_rows=[]
            for begin in range(0,len(rows),100):
                JOB['message']=f"Checking files on {server['name']}: {offset+begin:,} / {page.get('totalSize',len(rows))}…"
                batch=rows[begin:begin+100]
                keys=[ep.get('ratingKey') for ep in batch]
                if all(keys):
                    verified=plex_xml(server['url'],'/library/metadata/'+','.join(keys)+'?checkFiles=1',server['token'])
                    checked_rows.extend(verified.findall('Video'))
                else:
                    checked_rows.extend(batch)
            for ep in checked_rows:
                season,number = int(ep.get('parentIndex','0')),int(ep.get('index','0'))
                media = ep.find('Media')
                if season<1 or number<1 or media is None: continue
                media=next((m for m in ep.findall('Media') if len(m.findall('Part'))==1 and m.find('Part').get('exists')=='1' and m.find('Part').get('accessible')=='1'),media)
                parts=media.findall('Part')
                # Multi-part files need a separate concat source; report rather than silently truncate them.
                if len(parts)!=1 or not parts[0].get('key','').startswith('/library/parts/'): continue
                if parts[0].get('exists')=='0' or parts[0].get('accessible')=='0':
                    server['_unavailable_files']+=1
                    continue
                duration = float(ep.get('duration','0'))/1000
                if not math.isfinite(duration) or duration<=0: continue
                key = server['id']+':'+ep.get('grandparentRatingKey','')
                group = groups.setdefault(key,dict(id=hashlib.sha256(key.encode()).hexdigest()[:20],
                    server_id=server['id'],server=server['name'],library=library.get('title','TV Shows'),
                    title=ep.get('grandparentTitle','Unknown'),genres=genre_map.get(ep.get('grandparentRatingKey',''),[]),
                    episodes=[],_seen=set(),_files={}))
                identity=(season,number)
                if identity in group['_seen']: continue
                group['_seen'].add(identity)
                part=parts[0].get('key')
                file_identity=parts[0].get('file') or part
                if file_identity in group['_files']:
                    group['_files'][file_identity]['covered_episodes'].append(number)
                    continue
                source_file=parts[0].get('file','episode')
                source_path=PureWindowsPath(source_file) if '\\' in source_file else Path(source_file)
                entry=dict(season=season,episode=number,duration=duration,part=part,covered_episodes=[number],
                    filename=source_path.name,
                    title=f"{group['title']} S{season:02d}E{number:02d} — {ep.get('title','Episode')}",
                    folder=str(source_path.parent))
                group['_files'][file_identity]=entry
                group['episodes'].append(entry)
            offset += len(rows)
            if not rows or offset>=int(page.get('totalSize',str(offset))): break
            if offset>100000: raise ValueError('Catalog exceeds scan limit')
    for show in groups.values():
        show.pop('_seen');show.pop('_files')
        show['episodes'].sort(key=lambda e:(e['season'],e['episode']))
    return list(groups.values())


def _category_episodes(shows,tags):
    tags=set(tags);episodes={}
    for show in shows:
        if not tags.intersection(show.get('genres',[])): continue
        title=' '.join(show['title'].split())
        for episode in show['episodes']:
            # A title may exist on more than one Plex server. Broadcast each
            # logical episode once while retaining the server that owns it.
            key=(title.casefold(),episode['season'],episode['episode'])
            if key in episodes: continue
            episodes[key]={**episode,'server_id':show['server_id']}
    return list(episodes.values())


def refresh_category_channels(shows,now=None):
    """Rebuild the generated genre mixes from the latest real Plex catalog."""
    now=time.time() if now is None else now
    cfg=settings();channels=read('channels.json',[])
    regular=[row for row in channels if row.get('kind')!='category']
    previous={row.get('category_id'):row for row in channels if row.get('kind')=='category'}
    generated=[];used_numbers={int(str(row.get('number',''))) for row in regular if str(row.get('number','')).isdigit()}
    default_ads=dict(enabled=cfg['commercials_enabled'],mode=cfg['commercial_mode'],minutes=cfg['commercial_minutes'],
                     episodes=cfg['commercial_episodes'],count=cfg['commercial_count'],preroll=cfg['preroll'])
    assets=None
    for index,(identity,name,tags) in enumerate(CATEGORY_CHANNELS):
        episodes=_category_episodes(shows,tags)
        if not episodes: continue
        old=previous.get(identity,{})
        old_state=read(old.get('id','')+'.json',None) if old.get('id') else None
        if old_state:
            commercials=old_state.get('commercials',[]);ads=old_state.get('ads',default_ads)
        else:
            if assets is None: assets=commercial_assets(cfg)
            commercials=assets;ads=default_ads
        channel_id='category-'+identity
        state=new_schedule(episodes,commercials,now,secrets.token_hex(16),order='random',ads=ads)
        state['until_break']=(ads or {}).get('minutes',15)*60
        extend_schedule(state,now+6*86400)
        save_schedule(root()/(channel_id+'.json'),state)
        preferred=CATEGORY_CHANNEL_NUMBER_START+index
        number=next(value for value in range(preferred,CHANNEL_NUMBER_END+1) if value not in used_numbers)
        used_numbers.add(number)
        episode_count=sum(len(item.get('covered_episodes',[item['episode']])) for item in episodes)
        generated.append(dict(id=channel_id,number=str(number),name=name,
            kind='category',category_id=identity,genres=list(tags),order='random',count=episode_count,
            started_at=now,enabled=old.get('enabled',True),auto_numbered=False))
    save_schedule(root()/'channels.json',sorted(regular+generated,key=_channel_sort_key))
    return generated


def refresh(discover=False):
    if not settings()['enabled']:
        JOB.update(running=False,message='Custom Channels is disabled.')
        return {'status':'disabled','message':JOB['message']}
    with SCAN_LOCK:
        JOB.update(running=True,message='Discovering Plex…' if discover else 'Refreshing Plex TV libraries…')
        try:
            servers = discover_servers() if discover else read('servers.json',[])
            if not servers: servers=discover_servers()
            shows=[];warnings=[];statuses=[]
            for server in servers:
                JOB['message']='Scanning '+server['name']+'…'
                try:
                    found=scan_server(server)
                    shows.extend(found)
                    unavailable=server.get('_unavailable_files',0)
                    if unavailable and not found:
                        warnings.append(f"{server['name']}: Plex lists TV episodes, but their files are unavailable.")
                    statuses.append(dict(id=server['id'],name=server['name'],status='warning' if unavailable and not found else 'ready',shows=len(found),unavailable_files=unavailable))
                except Exception as exc:
                    warning=server['name']+': scan unavailable; no cached shows reused ('+type(exc).__name__+').'
                    warnings.append(warning)
                    statuses.append(dict(id=server['id'],name=server['name'],status='unavailable',shows=0,unavailable_files=0))
            if not servers: warnings.append('No accessible Plex servers found. Add a connection below and try again.')
            result=dict(shows=shows,servers=statuses,updated_at=time.time(),warnings=warnings)
            category_count=0
            try:
                with LOCK: category_count=len(refresh_category_channels(shows,result['updated_at']))
            except Exception as exc:
                warnings.append('Category channels were not refreshed ('+type(exc).__name__+').')
            with LOCK: save_schedule(root()/'catalog.json',result)
            JOB['message']=f'{len(shows)} TV series cataloged across {len(servers)} servers. {category_count} category channels randomized.'
            return dict(status='warning' if warnings else 'success',message=JOB['message'],warnings=warnings)
        except Exception as exc:
            JOB['message']='Plex scan failed ('+type(exc).__name__+'); no cached shows reused.'
            result=dict(shows=[],servers=[],updated_at=time.time(),warnings=[JOB['message']])
            try:
                with LOCK: save_schedule(root()/'catalog.json',result)
            except Exception:
                pass
            return dict(status='warning',message=JOB['message'],warnings=result['warnings'])
        finally:
            JOB['running']=False


def start_discovery():
    with LOCK:
        if not settings()['enabled']: raise ValueError('Enable Custom Channels and save settings first.')
        if JOB['running']: return
        JOB.update(running=True,message='Discovering Plex…')
        threading.Thread(target=refresh,kwargs={'discover':True},daemon=True,name='plex-custom-discovery').start()


def connect_server(url,token):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Enter a Plex server URL without credentials or query parameters.')
    if '\r' in token or '\n' in token: raise ValueError('Invalid Plex token')
    try: meta=plex_xml(url,'/',token)
    except Exception: raise ValueError('Could not connect to Plex. Check the URL and token.') from None
    identity=meta.get('machineIdentifier')
    if not identity: raise ValueError('That address is not a Plex server.')
    with LOCK:
        servers=[s for s in read('servers.json',[]) if s['id']!=identity]
        servers.append(dict(id=identity,name=meta.get('friendlyName','Plex'),url=url.rstrip('/'),token=token))
        save_schedule(root()/'servers.json',servers)


def selected_episodes(show,first,last):
    return [e for e in show['episodes'] if first<=e['season']<=last]


def summary(show, detailed=False):
    seasons={}
    for ep in show['episodes']:
        seasons.setdefault(ep['season'],[]).extend(ep.get('covered_episodes',[ep['episode']]))
    gaps=[]
    for season,numbers in sorted(seasons.items()):
        if min(numbers)>100:
            gaps.append(f'S{season:02d}: unusual Plex numbering starts at E{min(numbers)}; order retained')
            continue
        previous=0;missing=[]
        for number in sorted(set(numbers)):
            if number>previous+1:
                missing.append(f'E{previous+1:02d}' if number==previous+2 else f'E{previous+1:02d}–E{number-1:02d}')
            previous=number
        if missing: gaps.append(f'S{season:02d}: missing '+', '.join(missing[:20]))
    if seasons:
        absent=sorted(set(range(min(seasons),max(seasons)+1))-set(seasons))
        if absent: gaps.append('Missing seasons: '+', '.join(map(str,absent)))
    return {k:v for k,v in show.items() if k!='episodes'}|dict(count=sum(len(e.get('covered_episodes',[e['episode']])) for e in show['episodes']),
        hours=round(sum(e['duration'] for e in show['episodes'])/3600,2),seasons=sorted(seasons),gaps=gaps,
        files=[dict(season=e['season'],episode=e['episode'],duration=e['duration'],covered_episodes=e.get('covered_episodes',[e['episode']]),
                    **(dict(filename=e['filename'],folder=e['folder']) if detailed else {})) for e in show['episodes']])


def payload():
    cfg=settings();catalog=read('catalog.json',{})
    return dict(settings=cfg,commercials_folder_label=folder_label(Path(cfg['commercials_folder'])) if cfg['commercials_folder'] else '',job=dict(JOB),catalog={k:v for k,v in catalog.items() if k!='shows'},
        shows=[summary(s) for s in catalog.get('shows',[])],channels=read('channels.json',[]),
        servers=[dict(id=s['id'],name=s['name'],url=s['url']) for s in read('servers.json',[])],
        commercial_imports=commercial_imports())


def commercial_assets(cfg):
    if not cfg['commercials_enabled']: return []
    folder=Path(cfg['commercials_folder'])
    if not cfg['commercials_folder'] or not folder.is_dir(): raise ValueError('Commercials folder is unavailable.')
    excluded=set()
    for manifest in folder.rglob('manifest.json'):
        try:
            excluded.update((manifest.parent/r['clip']).resolve() for r in json.loads(manifest.read_text())['clips']
                if not r.get('include_in_ad_pool',True))
        except (OSError,ValueError,KeyError,TypeError):
            continue
    cache=read('commercial-probes.json',{})
    result=[]
    for path in sorted(folder.rglob('*')):
        if not path.is_file() or path.resolve() in excluded or path.suffix.lower() not in ('.mp4','.mkv','.mov','.ts','.avi','.webm'): continue
        stat=path.stat();key=str(path.resolve());stamp=[stat.st_size,stat.st_mtime_ns]
        record=cache.get(key,{})
        if record.get('stamp')!=stamp:
            try:
                probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type','-of','json',str(path)],capture_output=True,text=True,timeout=15,check=True)
                info=json.loads(probe.stdout);duration=float(info['format']['duration'])
                if not {'video','audio'}<={s['codec_type'] for s in info['streams']} or not math.isfinite(duration) or duration<=0: continue
                record=dict(stamp=stamp,duration=duration);cache[key]=record
            except (OSError,ValueError,KeyError,subprocess.SubprocessError): continue
        result.append(dict(path=key,filename=str(path.relative_to(folder)),duration=record['duration']))
    save_schedule(root()/'commercial-probes.json',cache)
    if not result: raise ValueError('No usable video commercials with audio were found in that folder.')
    return result


def _next_channel_number(channels):
    used={int(str(c.get('number',''))) for c in channels if str(c.get('number','')).isdigit()}
    number=next((value for value in range(CHANNEL_NUMBER_START,CHANNEL_NUMBER_END+1) if value not in used),None)
    if number is None: raise ValueError('The custom TV channel range 1000–1999 is full.')
    return str(number)


def _channel_sort_key(row):
    value=str(row.get('number',''))
    try: return (0,float(value),str(row.get('name','')).casefold(),str(row.get('id','')))
    except ValueError: return (1,float('inf'),str(row.get('name','')).casefold(),str(row.get('id','')))


def rebalance_channel_numbers(channels):
    automatic=[];reserved=set()
    for row in channels:
        value=str(row.get('number',''))
        inferred=value.isdigit() and CHANNEL_NUMBER_START<=int(value)<=9999
        if row.get('auto_numbered',inferred): automatic.append(row)
        elif value.isdigit(): reserved.add(int(value))
    available=(number for number in range(CHANNEL_NUMBER_START,CHANNEL_NUMBER_END+1) if number not in reserved)
    for row in sorted(automatic,key=lambda item:(str(item.get('name','')).casefold(),str(item.get('id','')))):
        number=next(available,None)
        if number is None: raise ValueError('The custom TV channel range 1000–1999 is full.')
        row['number']=str(number);row['auto_numbered']=True
    return sorted(channels,key=_channel_sort_key)


def migrate_channel_numbers():
    """Move legacy auto-assigned 9000-series channels into the 1000 block once."""
    marker=root()/'channel-numbering-v2.json'
    with LOCK:
        if marker.exists(): return 0
        channels=read('channels.json',[]);used={int(str(row.get('number',''))) for row in channels
            if str(row.get('number','')).isdigit() and CHANNEL_NUMBER_START<=int(str(row['number']))<=CHANNEL_NUMBER_END}
        changed=0
        for row in channels:
            value=str(row.get('number',''))
            if not value.isdigit() or int(value)<9000: continue
            number=next((candidate for candidate in range(CHANNEL_NUMBER_START,CHANNEL_NUMBER_END+1) if candidate not in used),None)
            if number is None: break
            row['number']=str(number);row['auto_numbered']=True;used.add(number);changed+=1
        if changed: save_schedule(root()/'channels.json',channels)
        save_schedule(marker,dict(version=2,migrated=changed,updated_at=time.time()))
        return changed


def _build_channel(show,cfg,channels,ads,now,order='ordered',allow_below_minimum=False,first=None,last=None):
    if show.get('stale'): raise ValueError('This server is unavailable. Refresh its catalog before creating a channel.')
    first=min(e['season'] for e in show['episodes']) if first is None else int(first)
    last=max(e['season'] for e in show['episodes']) if last is None else int(last)
    if first<1 or last<first: raise ValueError('Choose a valid season range.')
    episodes=selected_episodes(show,first,last)
    episode_count=sum(len(e.get('covered_episodes',[e['episode']])) for e in episodes)
    if episode_count<cfg['minimum_episodes'] and not allow_below_minimum:
        raise ValueError(f'This range contains {episode_count} episodes; the minimum is {cfg["minimum_episodes"]}.')
    if order not in ('random','ordered'): raise ValueError('Choose Random order or From start.')
    opts=dict(enabled=cfg['commercials_enabled'],mode=cfg['commercial_mode'],minutes=cfg['commercial_minutes'],
              episodes=cfg['commercial_episodes'],count=cfg['commercial_count'],preroll=cfg['preroll'])
    identity=secrets.token_hex(8)
    state=new_schedule(episodes,ads,now,secrets.token_hex(16),order=order,ads=opts)
    state['until_break']=opts['minutes']*60
    extend_schedule(state,now+6*86400)
    row=dict(id=identity,number=_next_channel_number(channels),name=show['title'],show_id=show['id'],server_id=show['server_id'],
             order=order,season_start=first,season_end=last,count=episode_count,started_at=now,enabled=True,auto_numbered=True)
    return row,state


def create_channel(data):
    with LOCK:
        cfg=settings()
        if not cfg['enabled']: raise ValueError('Enable Custom Channels first.')
        show=next((s for s in read('catalog.json',{}).get('shows',[]) if s['id']==data.get('show_id')),None)
        if not show: raise ValueError('Show not found. Discover Plex first.')
        channels=read('channels.json',[])
        row,state=_build_channel(show,cfg,channels,commercial_assets(cfg),time.time(),data.get('order','ordered'),
            data.get('allow_below_minimum') is True,data.get('season_start'),data.get('season_end'))
        save_schedule(root()/(row['id']+'.json'),state)
        save_schedule(root()/'channels.json',rebalance_channel_numbers(channels+[row]))
        return row


def create_channels(data):
    if not isinstance(data,dict) or not isinstance(data.get('show_ids'),list): raise ValueError('Choose series to add.')
    identities=[]
    for value in data['show_ids']:
        value=str(value)
        if value not in identities: identities.append(value)
    if not identities or len(identities)>10000: raise ValueError('Choose between 1 and 10,000 series.')
    with LOCK:
        cfg=settings()
        if not cfg['enabled']: raise ValueError('Enable Custom Channels first.')
        catalog={show['id']:show for show in read('catalog.json',{}).get('shows',[])}
        if any(identity not in catalog for identity in identities): raise ValueError('One or more series are no longer in the Plex catalog.')
        channels=read('channels.json',[]);existing={row.get('show_id') for row in channels}
        selected=[catalog[identity] for identity in identities if identity not in existing]
        if not selected: return dict(created=[],skipped=len(identities))
        ads=commercial_assets(cfg);now=time.time();built=[]
        for show in selected:
            row,state=_build_channel(show,cfg,channels+[item[0] for item in built],ads,now,'ordered',
                data.get('allow_below_minimum') is True)
            built.append((row,state))
        for row,state in built: save_schedule(root()/(row['id']+'.json'),state)
        save_schedule(root()/'channels.json',rebalance_channel_numbers(channels+[row for row,_state in built]))
        return dict(created=[row for row,_state in built],skipped=len(identities)-len(built))


def delete_channels(data):
    if not isinstance(data,dict) or not isinstance(data.get('show_ids'),list): raise ValueError('Choose series to remove.')
    identities=[]
    for value in data['show_ids']:
        value=str(value)
        if value not in identities: identities.append(value)
    if not identities or len(identities)>10000: raise ValueError('Choose between 1 and 10,000 series.')
    with LOCK:
        rows=read('channels.json',[]);selected=[row for row in rows if row.get('show_id') in identities]
        if not selected: return dict(deleted=[],skipped=len(identities))
        selected_ids={row['id'] for row in selected}
        save_schedule(root()/'channels.json',rebalance_channel_numbers([row for row in rows if row['id'] not in selected_ids]))
        for identity in selected_ids: (root()/(identity+'.json')).unlink(missing_ok=True)
        return dict(deleted=selected,skipped=len(identities)-len(selected))


def channel(identity):
    if not settings()['enabled']: raise ValueError('Custom Channels is disabled.')
    row=next((c for c in read('channels.json',[]) if c['id']==identity and c['enabled']),None)
    if not row: raise ValueError('Custom channel unavailable.')
    return row


def schedule(identity,now=None):
    channel(identity)
    now=time.time() if now is None else now
    with LOCK:
        path=root()/(identity+'.json')
        state=read(identity+'.json',None)
        if not state: raise ValueError('Schedule unavailable.')
        if state['cursor']<now+5*86400:
            extend_schedule(state,now+6*86400);trim_schedule(state,now-86400);save_schedule(path,state)
        return state


def update_channel(identity,data):
    with LOCK:
        rows=read('channels.json',[])
        row=next((c for c in rows if c['id']==identity),None)
        if not row: raise ValueError('Channel not found.')
        if not isinstance(data,dict): raise ValueError('Invalid channel update')
        if 'enabled' in data:
            if not isinstance(data['enabled'],bool): raise ValueError('Invalid enabled value')
            row['enabled']=data['enabled']
        if 'name' in data:
            name=str(data['name']).strip()
            if not name or len(name)>120 or any(ord(c)<32 for c in name): raise ValueError('Channel name must be 1–120 characters.')
            row['name']=name
        if 'number' in data:
            number=str(data['number']).strip()
            if not re.fullmatch(r'\d+(?:\.\d+)?',number) or len(number)>16: raise ValueError('Enter a numeric channel number.')
            if any(c is not row and c.get('number')==number for c in rows): raise ValueError('That channel number is already in use.')
            row['number']=number;row['auto_numbered']=False
        order=data.get('order',row['order'])
        first=data.get('season_start',row['season_start'])
        last=data.get('season_end',row['season_end'])
        if isinstance(first,bool) or isinstance(last,bool): raise ValueError('Choose a valid season range.')
        try: first,last=int(first),int(last)
        except (TypeError,ValueError): raise ValueError('Choose a valid season range.') from None
        if order not in ('random','ordered'): raise ValueError('Choose Random order or From start.')
        if first<1 or last<first: raise ValueError('Choose a valid season range.')
        rebuild=order!=row['order'] or first!=row['season_start'] or last!=row['season_end']
        if rebuild:
            show=next((s for s in read('catalog.json',{}).get('shows',[]) if s['id']==row['show_id']),None)
            if not show: raise ValueError('This show is unavailable in the latest Plex scan. Its saved channel was not changed.')
            episodes=selected_episodes(show,first,last)
            episode_count=sum(len(e.get('covered_episodes',[e['episode']])) for e in episodes)
            if episode_count<settings()['minimum_episodes']:
                raise ValueError(f'This range contains {episode_count} episodes; the minimum is {settings()["minimum_episodes"]}.')
            previous=read(identity+'.json',None)
            if not previous: raise ValueError('The saved channel schedule is unavailable.')
            now=time.time()
            state=new_schedule(episodes,previous.get('commercials',[]),now,secrets.token_hex(16),order=order,ads=previous.get('ads'))
            state['until_break']=(state.get('ads') or {}).get('minutes',15)*60
            extend_schedule(state,now+6*86400)
            save_schedule(root()/(identity+'.json'),state)
            row.update(order=order,season_start=first,season_end=last,count=episode_count,started_at=now)
        save_schedule(root()/'channels.json',rebalance_channel_numbers(rows))
        return row


def delete_channel(identity):
    with LOCK:
        rows=read('channels.json',[])
        if not any(c['id']==identity for c in rows): raise ValueError('Channel not found.')
        save_schedule(root()/'channels.json',rebalance_channel_numbers([c for c in rows if c['id']!=identity]))
        (root()/(identity+'.json')).unlink(missing_ok=True)


def install(core):
    migrate_channel_numbers()
    channels=read('channels.json',[])
    if channels: save_schedule(root()/'channels.json',rebalance_channel_numbers(channels))
    current=core.run_master_update
    if getattr(current,'_custom_channels',False): return
    def run(*,trigger='manual'):
        # Independent catalog refresh still runs when the IPTV provider is offline.
        result=refresh() if settings()['enabled'] else {'status':'disabled'}
        output=current(trigger=trigger)
        output['custom_channels']=result
        if result.get('warnings'):
            output.setdefault('provider_warnings',[]).extend('Custom Channels: '+w for w in result['warnings'])
        return output
    run._custom_channels=True
    core.run_master_update=run
