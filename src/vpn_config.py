"""Temporary RAM-only WireGuard uploads; preferences alone are persisted."""
from __future__ import annotations
import base64
import configparser
import hashlib
import secrets
import time
import ipaddress
import json
import os
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from database import connect

PROVIDERS = ['custom', 'mullvad', 'protonvpn', 'nordvpn', 'surfshark', 'airvpn', 'ivpn', 'windscribe', 'private internet access', 'cyberghost', 'expressvpn', 'ipvanish', 'privado', 'privatevpn', 'purevpn', 'torguard', 'vpnunlimited', 'vyprvpn']
_LOCK = threading.RLock()
_PRIVATE = [ipaddress.ip_network(value) for value in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')]

def suggested_lan_subnet(host):
    """Offer the common home /24 from the host LAN IP, never the Docker/tailnet IP."""
    try: address=ipaddress.ip_address(str(host or '').strip())
    except ValueError: return ''
    if address.version!=4 or not any(address in block for block in _PRIVATE): return ''
    return str(ipaddress.ip_network(str(address)+'/24',strict=False))

def detected_lan_subnet(host, subnet):
    try:
        address=ipaddress.ip_address(str(host or '').strip())
        network=ipaddress.ip_network(str(subnet or '').strip(),strict=False)
        if network.version==4 and address in network and any(network.subnet_of(block) for block in _PRIVATE): return str(network)
    except ValueError: pass
    return ''

def lan_networks(values):
    if not isinstance(values, list) or not values:
        raise ValueError('Enter at least one LAN subnet, for example 192.168.1.0/24.')
    if len(values)>8: raise ValueError('Use at most eight LAN subnets.')
    result=[]
    for value in values:
        try: network=ipaddress.ip_network(str(value).strip(), strict=False)
        except ValueError: raise ValueError('Enter valid private IPv4 LAN subnets.') from None
        if network.version!=4 or not any(network.subnet_of(block) for block in _PRIVATE):
            raise ValueError('LAN exceptions must be private IPv4 subnets, not internet routes.')
        if str(network) not in result: result.append(str(network))
    return result

def _key(value, label):
    try:
        if len(base64.b64decode(value, validate=True))!=32: raise ValueError()
    except (ValueError, TypeError): raise ValueError('The WireGuard '+label+' must be a 32-byte base64 key.') from None
    return value

def parse_wireguard(text):
    if not isinstance(text,str) or not text.strip() or len(text.encode())>32768:
        raise ValueError('Import a WireGuard configuration file under 32 KB.')
    parser=configparser.ConfigParser(interpolation=None, strict=True, inline_comment_prefixes=('#',';'))
    try: parser.read_string(text)
    except configparser.Error: raise ValueError('The WireGuard configuration could not be read.') from None
    if parser.defaults() or parser.sections()!=['Interface','Peer']:
        raise ValueError('Use a configuration with one Interface and one Peer.')
    allowed={'Interface':{'privatekey','address','dns','mtu'},'Peer':{'publickey','presharedkey','endpoint','allowedips','persistentkeepalive'}}
    for section in allowed:
        if set(parser[section])-allowed[section]:
            raise ValueError('This import supports plain WireGuard settings only; remove hooks or extra options.')
    interface,peer=parser['Interface'],parser['Peer']
    private=_key(interface.get('privatekey',''),'private key')
    public=_key(peer.get('publickey',''),'public key')
    try:
        addresses=[ipaddress.ip_interface(value.strip()) for value in interface.get('address','').split(',')]
        routes=[ipaddress.ip_network(value.strip(),strict=False) for value in peer.get('allowedips','').split(',')]
        host,port=peer.get('endpoint','').rsplit(':',1)
        endpoint=ipaddress.ip_address(host);port=int(port)
        mtu=int(interface.get('mtu','1320'))
        keepalive=int(peer.get('persistentkeepalive','25'))
    except (ValueError,TypeError): raise ValueError('Check the WireGuard addresses, endpoint IP/port, and numeric settings.') from None
    v4=[value for value in addresses if value.version==4]
    if not v4 or endpoint.version!=4 or not endpoint.is_global or not 1<=port<=65535:
        raise ValueError('Use an IPv4 tunnel address and a public IPv4 VPN server endpoint with a valid port.')
    if ipaddress.ip_network('0.0.0.0/0') not in routes:
        raise ValueError('The configuration must route all IPv4 internet traffic (AllowedIPs = 0.0.0.0/0).')
    if not 1280<=mtu<=1500 or not 0<=keepalive<=65535:
        raise ValueError('Use an MTU between 1280 and 1500 and a valid keepalive interval.')
    lines=['[Interface]', 'PrivateKey = '+private,'Address = '+', '.join(str(value) for value in v4),'MTU = '+str(mtu),'','[Peer]','PublicKey = '+public]
    if peer.get('presharedkey'): lines.append('PresharedKey = '+_key(peer['presharedkey'],'preshared key'))
    lines += ['Endpoint = '+str(endpoint)+':'+str(port),'AllowedIPs = 0.0.0.0/0','PersistentKeepalive = '+str(keepalive)]
    # Gluetun handles DNS and blocks untunneled IPv6; imported DNS/hooks are not executed.
    return '\n'.join(lines)+'\n', [str(value) for value in v4]

def _read(db_path):
    with closing(connect(db_path)) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS vpn_settings (id INTEGER PRIMARY KEY CHECK(id=1), settings_json TEXT NOT NULL)')
        row=conn.execute('SELECT settings_json FROM vpn_settings WHERE id=1').fetchone();conn.commit()
    return json.loads(row[0]) if row else {'provider':'custom','protocol':'wireguard','lan_subnets':[], 'profile_id':None,'updated_at':None}

# Secret uploads live only on Linux tmpfs, not the application data volume.
PROFILE_TTL = 1200
RAM_ROOT = Path('/dev/shm/m3u-vpn-profiles')

def profile_directory(db_path):
    return RAM_ROOT/hashlib.sha256(str(Path(db_path).resolve()).encode()).hexdigest()[:16]

def profile_for_test(db_path, identity):
    if not isinstance(identity,str) or len(identity)!=64 or any(c not in '0123456789abcdef' for c in identity):
        raise ValueError('Invalid temporary profile identity.')
    path=profile_directory(db_path)/(identity+'.conf')
    try:
        if time.time()-path.stat().st_mtime<=PROFILE_TTL:return path.read_text(encoding='utf-8')
    except FileNotFoundError:pass
    path.unlink(missing_ok=True)
    raise ValueError('The temporary upload expired. Select the file again.')

def _purge(db_path):
    directory=profile_directory(db_path)
    if directory.exists():
        for path in directory.glob('*.conf'):
            try:
                if time.time()-path.stat().st_mtime>PROFILE_TTL:path.unlink(missing_ok=True)
            except FileNotFoundError:pass

def status(db_path, data_dir, session=None):
    with _LOCK:
        _purge(db_path);saved=_read(db_path)
        host=os.environ.get('M3U_LAN_HOST','')
        detected=detected_lan_subnet(host,os.environ.get('M3U_LAN_SUBNET',''))
        identity=saved.get('profile_id');present=False
        if identity and (session is None or session==saved.get('profile_session')):
            try:profile_for_test(db_path,identity);present=True
            except ValueError:pass
        import vpn_runtime
        activation=vpn_runtime.status(db_path)
        return {'provider':saved['provider'],'protocol':'wireguard','lan_subnets':saved['lan_subnets'],
                'configuration_present':present,'profile_id':identity if present else None,
                'updated_at':saved.get('updated_at'),'activation':activation['status'],'vpn_runtime':activation,'providers':PROVIDERS,'requires_host_apply':True,
                'suggested_lan_subnet':detected or suggested_lan_subnet(host),
                'lan_subnet_detection':'detected' if detected else 'estimated' if suggested_lan_subnet(host) else 'unavailable',
                'temporary_upload':True,'expires_at':saved.get('expires_at') if present else None}

def discard(db_path, data_dir, session=None, identity=None):
    with _LOCK:
        saved=_read(db_path)
        if (session is None or session==saved.get('profile_session')) and (identity is None or identity==saved.get('profile_id')):
            identity=saved.get('profile_id')
            if identity and len(identity)==64 and all(c in '0123456789abcdef' for c in identity):
                (profile_directory(db_path)/(identity+'.conf')).unlink(missing_ok=True)
            for field in ['profile_id','profile_session','expires_at']:saved.pop(field,None)
            saved['updated_at']=None
            with closing(connect(db_path)) as conn:
                conn.execute('UPDATE vpn_settings SET settings_json=? WHERE id=1',(json.dumps(saved),));conn.commit()
        return status(db_path,data_dir,session)

def save(db_path, data_dir, data, session='internal'):
    if not isinstance(data,dict):raise ValueError('Use a VPN configuration object.')
    with _LOCK:
        previous=_read(db_path);provider=data.get('provider',previous['provider'])
        if provider not in PROVIDERS:raise ValueError('Choose a supported provider or Custom WireGuard.')
        networks=lan_networks(data.get('lan_subnets',previous['lan_subnets']))
        canonical,addresses=parse_wireguard(data.get('wireguard_config'))
        if any(ipaddress.ip_interface(address).ip in ipaddress.ip_network(network) for address in addresses for network in networks):
            raise ValueError('A LAN exception overlaps the VPN tunnel address. Use a narrower LAN subnet.')
        identity=secrets.token_hex(32);directory=profile_directory(db_path)
        if RAM_ROOT==Path('/dev/shm/m3u-vpn-profiles'):
            if os.name!='posix' or not any(line.split()[1:3]==['/dev/shm','tmpfs'] for line in Path('/proc/mounts').read_text().splitlines()):
                raise ValueError('Temporary VPN uploads require the Linux Docker instance with memory storage enabled.')
        directory.mkdir(parents=True,exist_ok=True);directory.chmod(0o700)
        target=directory/(identity+'.conf');target.touch(mode=0o600);target.chmod(0o600);target.write_text(canonical,encoding='utf-8')
        saved={'provider':provider,'protocol':'wireguard','lan_subnets':networks,'profile_id':identity,
               'profile_session':session,'expires_at':time.time()+PROFILE_TTL,'updated_at':datetime.now(timezone.utc).isoformat()}
        with closing(connect(db_path)) as conn:
            conn.execute('INSERT INTO vpn_settings(id,settings_json) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET settings_json=excluded.settings_json',(json.dumps(saved),));conn.commit()
        old=previous.get('profile_id')
        if isinstance(old,str) and len(old)==64 and all(c in '0123456789abcdef' for c in old):
            (directory/(old+'.conf')).unlink(missing_ok=True)
        # Expire the secret even when the page is closed and no status requests arrive.
        timer=threading.Timer(PROFILE_TTL,lambda:target.unlink(missing_ok=True));timer.daemon=True;timer.start()
        _purge(db_path)
        return status(db_path,data_dir,session)
