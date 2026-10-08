"""Persist applied VPN credentials in a private Docker volume, never in manifests."""
from __future__ import annotations

import json
import copy
import os
import re
from pathlib import Path

from vpn_test_host import run


def credential_volume(identity):
    if not re.fullmatch(r"[a-f0-9]{32}", identity):
        raise ValueError("Invalid VPN deployment identity.")
    return "m3u-picker-vpn-config-" + identity


def create_credentials(identity, canonical, auth, image):
    volume = credential_volume(identity)
    run(["docker", "volume", "create", "--label", "m3u.vpn-config=" + identity, volume])
    writer = ["docker", "run", "--rm", "-i", "--network", "none", "--mount",
              "type=volume,source=" + volume + ",target=/saved", "--entrypoint", "sh", image, "-c"]
    # stdin carries the secret; it never appears in argv, env, stdout or host files.
    try:
        for directory, filename, value in (("wireguard", "wg0.conf", canonical), ("auth", "config.toml", auth)):
            command = ("umask 077; mkdir -p /saved/" + directory + "; chmod 700 /saved /saved/" + directory
                       + "; cat > /saved/" + directory + "/upload; chmod 600 /saved/" + directory
                       + "/upload; mv /saved/" + directory + "/upload /saved/" + directory + "/" + filename)
            run(writer + [command], value)
    except Exception:
        run(["docker", "volume", "rm", volume])
        raise
    return volume


def manifest(app, glue, image, volume):
    """A complete Compose deployment survives recreation and installer UP."""
    labels = app["Config"].get("Labels") or {}
    project = labels.get("com.docker.compose.project", app["Name"].lstrip("/"))
    if not project.endswith("-vpn"):
        project += "-vpn"
    service = labels.get("com.docker.compose.service", "m3u-picker")
    env = dict(value.split("=", 1) for value in app["Config"]["Env"])
    if any(key.startswith("WIREGUARD_") for key in env):
        raise ValueError("WireGuard keys belong in the private VPN volume.")
    mounts, volumes = [], {"vpn-config": {"external": True, "name": volume}}
    for index, mount in enumerate(app["Mounts"]):
        item = {"type": mount["Type"], "target": mount["Destination"], "read_only": not mount["RW"]}
        if mount["Type"] == "volume":
            key = "app-volume-" + str(index)
            volumes[key] = {"external": True, "name": mount["Name"]}
            item["source"] = key
        elif mount["Type"] == "bind":
            item["source"] = mount["Source"]
        else:
            raise ValueError("Unsupported app mount.")
        mounts.append(item)
    app_service = {"image": "${M3U_IMAGE:-" + image + "}", "container_name": app["Name"].lstrip("/"),
                   "network_mode": "service:gluetun", "depends_on": {"gluetun": {"condition": "service_healthy", "restart": True}},
                   "environment": env, "volumes": mounts, "restart": "unless-stopped",
                   "entrypoint": app["Config"]["Entrypoint"], "command": app["Config"]["Cmd"]}
    if app["HostConfig"].get("DeviceRequests"):
        app_service["deploy"] = {"resources": {"reservations": {"devices": [{"driver": "nvidia", "count": "all", "capabilities": ["gpu"]}]}}}
    glue_env = dict(value.split("=", 1) for value in glue["Config"]["Env"])
    # Persist public network settings, never provider keys or control passwords.
    glue_env = {key: value for key, value in glue_env.items() if key.startswith(("VPN_", "HEALTH_", "FIREWALL_", "DNS_", "BLOCK_"))}
    glue_env["HEALTH_RESTART_VPN"] = "on"
    ports = sorted({value["HostPort"] + ":" + key.split("/")[0]
                    for key, values in glue["NetworkSettings"]["Ports"].items() for value in (values or [])})
    glue_service = {"image": glue["Config"]["Image"], "container_name": glue["Name"].lstrip("/"),
                    "cap_add": ["NET_ADMIN"], "devices": ["/dev/net/tun:/dev/net/tun"],
                    "sysctls": {"net.ipv6.conf.all.disable_ipv6": "1"}, "ports": ports,
                    "environment": glue_env, "volumes": [{"type": "volume", "source": "vpn-config", "target": "/gluetun"}],
                    "restart": "unless-stopped", "entrypoint": ["/gluetun-entrypoint"]}
    normal_command=app["Config"]["Cmd"]
    normal_entrypoint=app["Config"]["Entrypoint"]
    if normal_entrypoint==["/bin/sh"] and len(normal_command)>=4 and normal_command[0]=='-c' and normal_command[2]=='picker' and 'nameserver 127.0.0.1' in normal_command[1]:
        normal_command=normal_command[3:];normal_entrypoint=None
    return {"name": project, "services": {service: app_service, "gluetun": glue_service}, "volumes": volumes,
            "x-m3u-vpn": {"enabled": True,"service":service,"ports":ports,"normal_command":normal_command,"normal_entrypoint":normal_entrypoint}}


def connection_mode(configuration, enabled):
    """Move app networking, not just the tunnel, so explicit Off uses normal internet."""
    if type(enabled) is not bool:raise ValueError('Invalid VPN mode.')
    result=copy.deepcopy(configuration);settings=result['x-m3u-vpn'];service=settings['service']
    app=result['services'][service];glue=result['services']['gluetun']
    settings['enabled']=enabled;app['environment']['M3U_VPN_REQUIRED']='true' if enabled else 'false'
    if enabled:
        app.pop('ports',None);glue.pop('profiles',None);glue['ports']=settings['ports']
        app['network_mode']='service:gluetun';app['depends_on']={'gluetun':{'condition':'service_healthy','restart':True}}
        app['entrypoint']=['/bin/sh']
        app['command']=['-c','printf "nameserver 127.0.0.1\\n" > /etc/resolv.conf; exec "$@"','picker',*settings['normal_command']]
    else:
        app.pop('network_mode',None);app.pop('depends_on',None);app['ports']=settings['ports']
        app['entrypoint']=settings['normal_entrypoint'];app['command']=settings['normal_command']
        glue.pop('ports',None);glue['profiles']=['vpn']
    return result


def startup_manifest(info, image, glue_name, volume, digest, subnets):
    app = copy.deepcopy(info)
    env = dict(value.split("=", 1) for value in app["Config"]["Env"])
    env.update(M3U_VPN_REQUIRED="true", M3U_VPN_CONTAINER=glue_name)
    app["Config"]["Env"] = [key + "=" + value for key, value in env.items()]
    if app["Config"].get("Entrypoint") != ["/bin/sh"]:
        app["Config"]["Cmd"] = ["-c", 'printf "nameserver 127.0.0.1\\n" > /etc/resolv.conf; exec "$@"', "picker", *app["Config"]["Cmd"]]
        app["Config"]["Entrypoint"] = ["/bin/sh"]
    glue_env = {"VPN_SERVICE_PROVIDER": "custom", "VPN_TYPE": "wireguard", "HEALTH_SERVER_ADDRESS": "127.0.0.1:9990",
                "HEALTH_RESTART_VPN": "on", "FIREWALL_INPUT_PORTS": "9998", "FIREWALL_OUTBOUND_SUBNETS": ",".join(subnets),
                "BLOCK_MALICIOUS": "off", "DNS_UPDATE_PERIOD": "0"}
    glue = {"Name": "/" + glue_name, "Config": {"Image": digest, "Env": [key + "=" + value for key, value in glue_env.items()]},
            "NetworkSettings": {"Ports": {"9998/tcp": [{"HostPort": "9998"}]}}}
    return manifest(app, glue, image, volume)


def save_manifest(path, configuration):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(configuration, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)
