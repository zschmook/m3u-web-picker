"""Executed inside the disposable Python probe; never imports Picker."""
import http.client
import ipaddress
import json
import socket
import ssl
import sys
import urllib.request
from pathlib import Path

args=json.load(sys.stdin)
def request_local(url,method='GET',body=None):
 headers={'User-Agent':'M3U-VPN-Connection-Test/1.0'}
 if args.get('api_key') and url.startswith('http://127.0.0.1:8000/'):headers['X-API-Key']=args['api_key']
 data=None if body is None else json.dumps(body).encode()
 with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(urllib.request.Request(url,data=data,headers=headers,method=method),timeout=8) as r:
  return r.status,r.read(65536)

def public_ip(address):
 # Connect to an already resolved public address; the VPN-down test cannot be
 # mistaken for a DNS failure or a cached DNS answer.
 raw=socket.create_connection((address,443),timeout=8)
 with ssl.create_default_context().wrap_socket(raw,server_hostname='api.ipify.org') as tls:
  tls.sendall(b'GET / HTTP/1.1\r\nHost: api.ipify.org\r\nConnection: close\r\n\r\n')
  response=http.client.HTTPResponse(tls);response.begin()
  if response.status!=200:raise ValueError('Public IP service failed')
  value=response.read(1024).decode().strip();ipaddress.ip_address(value);return value

def local_checks():
 results={}
 for key,url in args.get('local_urls',{}).items():
  try:status,_=request_local(url);results[key]=status==200
  except Exception:results[key]=False
 return results
try:
 action=args['action']
 if action=='health':
  code,_=request_local('http://127.0.0.1:9990/');result={'healthy':code==200}
 elif action in ('stop','start'):
  status,_=request_local('http://127.0.0.1:8000/v1/vpn/status','PUT',{'status':'stopped' if action=='stop' else 'running'});result={'accepted':status in (200,204)}
 elif action=='connect':
  resolvers=[line.split()[1] for line in Path('/etc/resolv.conf').read_text().splitlines() if line.startswith('nameserver ')]
  if resolvers!=['127.0.0.1']:raise ValueError('Probe is not using Gluetun DNS')
  addresses=list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo('api.ipify.org',443,socket.AF_INET,socket.SOCK_STREAM)))
  addresses=[v for v in addresses if ipaddress.ip_address(v).is_global]
  if not addresses:raise ValueError('Public DNS lookup failed')
  ip=public_ip(addresses[0]);result={'dns_resolved':True,'resolvers':resolvers,'public_ip':ip,'probe_address':addresses[0],'local_access':local_checks()}
 elif action=='blocked':
  try:value=public_ip(args['probe_address']);result={'internet_blocked':False,'unexpected_public_ip':value}
  except (OSError,ValueError,http.client.HTTPException):result={'internet_blocked':True}
  result['local_access']=local_checks()
 else:raise ValueError('Unknown probe action')
 print(json.dumps(result))
except Exception as error:
 # Return only the exception type, never raw requests, keys, or server logs.
 print(json.dumps({'error':type(error).__name__}))
