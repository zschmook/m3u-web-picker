"""Relay integration checks with a real local upstream HTTP server."""
import threading,unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
from flask import Flask,Response
from media import upstream_relay as relay

class Handler(BaseHTTPRequestHandler):
 calls=[]
 def log_message(self,*args):pass
 def do_HEAD(self):self.do_GET()
 def do_GET(self):
  type(self).calls.append((self.path,self.headers.get('Range')))
  if self.path=='/redirect':self.send_response(302);self.send_header('Location','/cdn/master.m3u8');self.end_headers();return
  if self.path=='/cdn/master.m3u8':body=b'#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,URI="audio.m3u8"\n#EXT-X-STREAM-INF:BANDWIDTH=1\nvideo.m3u8\n';kind='application/vnd.apple.mpegurl'
  elif self.path.endswith('.m3u8'):body=b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXT-X-MAP:URI="init.mp4"\n#EXT-X-PART:DURATION=1,URI="part.ts"\n#EXTINF:1,\nsegment.ts\n';kind='application/vnd.apple.mpegurl'
  else:body=b'0123456789';kind='video/mp2t'
  status=200
  if self.headers.get('Range')=='bytes=2-5':body=body[2:6];status=206
  self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
  if status==206:self.send_header('Content-Range','bytes 2-5/10')
  self.end_headers()
  if self.command!='HEAD':self.wfile.write(body)

class RelayTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.url='http://127.0.0.1:'+str(cls.server.server_port)
 @classmethod
 def tearDownClass(cls):cls.server.shutdown();cls.server.server_close()
 def setUp(self):
  self.app=Flask(__name__);relay.register_routes(self.app)
  self.app.add_url_rule('/playlist/channels.direct.m3u','playlist',lambda:Response('#EXTM3U\n#EXTINF:-1,WGAL\n'+self.url+'/redirect\n',content_type='audio/x-mpegurl'))
  self.app.add_url_rule('/guide/stream/manual/wgal','guide',lambda:Response(status=307,headers={'Location':self.url+'/redirect'}))
  self.client=self.app.test_client()
  for name,value in [('required',True),('healthy',True),('read',{'requested':True})]:
   mock=patch.object(relay.vpn_runtime,name,return_value=value);mock.start();self.addCleanup(mock.stop)
 def test_export_and_redirect_hide_upstream_and_relay_nested_hls(self):
  exported=self.client.get('/playlist/channels.direct.m3u').text;self.assertNotIn(self.url,exported)
  token=exported.splitlines()[-1].split('http://localhost')[-1];master=self.client.get(token)
  self.assertEqual(master.status_code,200);self.assertNotIn(self.url,master.text);self.assertNotIn('video.m3u8',master.text)
  video=master.text.splitlines()[-1];self.assertTrue(video.endswith('.m3u8'));media=self.client.get(video)
  import re
  paths=re.findall(r'URI="([^"]+)"',media.text)+[media.text.splitlines()[-1]]
  self.assertEqual(len(paths),4)
  self.assertTrue(paths[-1].endswith('.ts'))
  for path in paths:self.assertEqual(self.client.get(path).data,b'0123456789')
  self.assertTrue(any(path=='/cdn/key.bin' for path,_ in Handler.calls))
  self.assertEqual(self.client.get('/guide/stream/manual/wgal').status_code,200)
 def test_ranges_head_and_cleanup(self):
  path=relay.register(self.url+'/binary.ts');response=self.client.get(path,headers={'Range':'bytes=2-5'})
  self.assertEqual(response.status_code,206);self.assertEqual(response.data,b'2345');self.assertEqual(response.headers['Content-Range'],'bytes 2-5/10');response.close();self.assertEqual(relay.active_count(),0)
  head=self.client.head(path);self.assertEqual(head.headers['Content-Length'],'10');self.assertEqual(head.data,b'')
 def test_down_vpn_blocks_without_fetching_and_unknown_token_is_not_proxy(self):
  count=len(Handler.calls)
  with patch.object(relay.vpn_runtime,'healthy',return_value=False):self.assertEqual(self.client.get(relay.register(self.url+'/binary.ts')).status_code,503)
  self.assertEqual(len(Handler.calls),count);self.assertEqual(self.client.get('/stream/relay/unknown').status_code,404)
 def test_unapplied_requested_vpn_fails_closed(self):
  with patch.object(relay.vpn_runtime,'required',return_value=False):self.assertEqual(self.client.get('/guide/stream/manual/wgal').status_code,503)
 def test_unsupported_hls_does_not_expose_provider_uris(self):
  with self.assertRaises(ValueError):relay.rewrite_hls('#EXTM3U\n#EXT-X-CONTENT-STEERING:SERVER-URI="secret"',self.url)
