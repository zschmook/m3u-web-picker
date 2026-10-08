"""Opaque media relay: fetch upstream bytes and HLS resources through Picker."""
from collections import OrderedDict
import hmac,hashlib,secrets,threading,time,re,urllib.request,urllib.error
from urllib.parse import urljoin,urlsplit
from pathlib import PurePosixPath
from flask import Response,request,stream_with_context
import vpn_runtime
_KEY=secrets.token_bytes(32);_TARGETS=OrderedDict();_LOCK=threading.RLock();_TTL=21600
_URI=re.compile(r'(?<![\w-])URI="([^"]*)"')
_ACTIVE=0

def active_count():
 with _LOCK:return _ACTIVE

def register(target):
 if urlsplit(target).scheme not in ('http','https'):raise ValueError('Unsupported media URL.')
 token=hmac.new(_KEY,target.encode(),hashlib.sha256).hexdigest()
 with _LOCK:
  _TARGETS[token]=(target,time.time());_TARGETS.move_to_end(token)
  while len(_TARGETS)>8192:_TARGETS.popitem(last=False)
 suffix=PurePosixPath(urlsplit(target).path).suffix.lower()
 if suffix not in ('.m3u8','.m3u','.ts','.m4s','.mp4','.aac','.mp3','.key','.bin','.vtt','.webvtt','.png','.jpg','.jpeg'):suffix=''
 return '/stream/relay/'+token+suffix

def resolve(token):
 token=token.split('.',1)[0]
 with _LOCK:
  entry=_TARGETS.get(token)
  if not entry or time.time()-entry[1]>_TTL:return ''
  _TARGETS.move_to_end(token);_TARGETS[token]=(entry[0],time.time());return entry[0]

def rewrite_hls(text,source):
 if '#EXT-X-CONTENT-STEERING:' in text or '{$' in text:raise ValueError('Unsupported HLS extension.')
 lines=[]
 for raw in text.splitlines():
  line=raw.strip()
  if line and not line.startswith('#'):raw=register(urljoin(source,line))
  elif line.startswith('#'):raw=_URI.sub(lambda m:'URI="'+register(urljoin(source,m[1]))+'"',raw)
  lines.append(raw)
 return '\n'.join(lines)+'\n'

def response_for(target):
 if vpn_runtime.required() and not vpn_runtime.healthy():return Response('VPN disconnected. Playback is blocked.\n',status=503)
 headers={'User-Agent':'M3U-Web-Picker/2.0','Accept':'*/*','Accept-Encoding':'identity'}
 for name in ('Range','If-Range'):
  if request.headers.get(name):headers[name]=request.headers[name]
 upstream=None
 try:
  opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
  upstream=opener.open(urllib.request.Request(target,headers=headers,method=request.method),timeout=20)
  content=upstream.headers.get('Content-Type','application/octet-stream')
  if request.method=='HEAD':
   result=Response(status=upstream.status,content_type=content)
   for key in ('Content-Length','Content-Range','Accept-Ranges'):
    if upstream.headers.get(key):result.headers[key]=upstream.headers[key]
   upstream.close();result.headers['X-Picker-Relay']='1';return result
  prefix=upstream.read(4096)
  if prefix.lstrip().startswith(b'#EXTM3U') or 'mpegurl' in content.lower():
   payload=prefix+upstream.read(2097153)
   if len(payload)>2097152:raise ValueError('Playlist too large.')
   text=rewrite_hls(payload.decode('utf-8-sig'),upstream.geturl());upstream.close()
   result=Response(text,content_type='application/vnd.apple.mpegurl');result.headers['X-Picker-Relay']='1'
  else:
   selected={key:upstream.headers[key] for key in ('Content-Length','Content-Range','Accept-Ranges') if upstream.headers.get(key)}
   @stream_with_context
   def chunks():
    global _ACTIVE
    with _LOCK:_ACTIVE+=1
    try:
     yield prefix
     while True:
      chunk=upstream.read(65536)
      if not chunk:break
      yield chunk
    finally:
     upstream.close()
     with _LOCK:_ACTIVE-=1
   result=Response(chunks(),status=upstream.status,content_type=content,headers=selected)
  result.headers['Cache-Control']='no-store';result.headers['X-Accel-Buffering']='no';return result
 except (OSError,ValueError,urllib.error.URLError):
  if upstream:upstream.close()
  return Response('Upstream media could not be relayed.\n',status=502)

def register_routes(app):
 @app.route('/stream/relay/<token>',methods=['GET','HEAD'])
 def resource(token):
  target=resolve(token)
  return response_for(target) if target else Response('Media link expired. Reload the playlist.\n',status=404)
 @app.before_request
 def guard():
  if request.path.startswith(('/stream/','/guide/play/','/guide/stream/','/hdhr/stream/','/auto/','/sports/stream')):
   import core
   state=vpn_runtime.read(core.DB_PATH)
   if vpn_runtime.protection_missing(core.DB_PATH):return Response('VPN is unavailable. Playback is blocked.\n',status=503)
 @app.after_request
 def protected_outputs(response):
  if request.path.startswith('/stream/relay/'):
   response.headers['Access-Control-Allow-Origin']='*';response.headers['Access-Control-Allow-Headers']='Range, If-Range';response.headers['Access-Control-Expose-Headers']='Content-Length, Content-Range, Accept-Ranges'
  if not vpn_runtime.required():return response
  base=request.url_root.rstrip('/')
  def external(value):return value.startswith(('http://','https://')) and not value.startswith(base+'/')
  location=response.headers.get('Location','')
  if 300<=response.status_code<400 and external(location):return response_for(location)
  if 'mpegurl' not in response.content_type.lower() or response.headers.get('X-Picker-Relay'):return response
  if response.is_streamed:return response
  lines=[]
  for line in response.get_data(as_text=True).splitlines():
   if external(line.strip()):line=base+register(line.strip())
   else:line=re.sub(r'(tvg-logo|url-tvg|x-tvg-url)="(https?://[^"]+)"',lambda m:m[1]+'="'+(base+register(m[2]) if external(m[2]) else m[2])+'"',line)
   lines.append(line)
  response.set_data('\n'.join(lines)+'\n');return response
