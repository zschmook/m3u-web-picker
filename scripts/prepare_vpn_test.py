"""Prepare a port-9998 Gluetun test overlay. Never starts or stops containers."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from vpn_config import parse_wireguard, lan_networks

ROOT=Path(__file__).resolve().parents[1]

def prepare(profile: Path, networks: list[str], output: Path):
    canonical,addresses=parse_wireguard(profile.read_text(encoding='utf-8-sig'))
    subnets=lan_networks(networks)
    import ipaddress
    if any(ipaddress.ip_interface(address).ip in ipaddress.ip_network(network) for address in addresses for network in subnets):
        raise ValueError('A LAN subnet overlaps the VPN tunnel address.')
    output=output.resolve();output.mkdir(parents=True,exist_ok=True);output.chmod(0o700)
    secret=output/'wg0.conf';secret.touch(mode=0o600,exist_ok=True);secret.chmod(0o600);secret.write_text(canonical,encoding='utf-8')
    quote=lambda value:json.dumps(str(value))
    overlay=f'''name: m3u-picker-setup
services:
  setup:
    network_mode: service:gluetun
    ports: !reset []
    depends_on:
      gluetun:
        condition: service_healthy
  gluetun:
    image: qmcgaw/gluetun:latest
    cap_add: [NET_ADMIN]
    devices: [/dev/net/tun:/dev/net/tun]
    sysctls:
      net.ipv6.conf.all.disable_ipv6: 1
    ports:
      - "9998:9998"
    environment:
      VPN_SERVICE_PROVIDER: custom
      VPN_TYPE: wireguard
      HEALTH_SERVER_ADDRESS: "127.0.0.1:9990"
      FIREWALL_INPUT_PORTS: "9998"
      FIREWALL_OUTBOUND_SUBNETS: {quote(','.join(subnets))}
      DNS_UPSTREAM_IPV6: "off"
    volumes:
      - type: bind
        source: {quote(secret.as_posix())}
        target: /gluetun/wireguard/wg0.conf
        read_only: true
    restart: unless-stopped
'''
    destination=output/'docker-compose.vpn-test.yml';destination.write_text(overlay,encoding='utf-8')
    # Keep metadata separate; no private key in manifests, stdout, or Compose.
    (output/'manifest.json').write_text(json.dumps({'port':9998,'project':'m3u-picker-setup','lan_subnets':subnets,'prepared_only':True,'relay_coverage':'not_verified'},indent=2)+'\n')
    return destination

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wireguard-config',type=Path,required=True)
    parser.add_argument('--lan-subnet',action='append',required=True)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'runtime/vpn-test')
    args=parser.parse_args()
    try: path=prepare(args.wireguard_config,args.lan_subnet,args.output_dir)
    except (ValueError,OSError) as exc: parser.exit(1,str(exc)+'\n')
    print('Prepared port-9998 overlay: '+str(path))
    print('No containers changed. Provider relay coverage and VPN/LAN connectivity still require testing before protected playback is claimed.')
if __name__=='__main__':main()
