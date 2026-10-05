' This diagnostic is launched explicitly with network_scan=1. It uses the
' Roku's own sockets and reports only to the user's local helper.
sub init()
    m.top.functionName = "runNetworkScan"
end sub

sub runNetworkScan()
    clock = CreateObject("roTimespan")
    clock.mark()
    info = CreateObject("roDeviceInfo")
    localIp = ""
    interfaces = info.getIPAddrs()
    for each iface in interfaces
        candidate = interfaces[iface]
        if scanLocalHost(candidate) then localIp = candidate
    end for
    result = {ok: false, source: "roku", scanner_ip: localIp, network: "10.0.0.0/24", devices: []}
    if localIp = ""
        result.error = "Roku is not connected to the configured home network."
    else
        devices = {}
        scanAddDevice(devices, localIp, info.getFriendlyName(), "This Roku")
        scanSsdp(devices)
        result.probes = scanConnections(devices)
        result.smart_home_discovery = scanSmartHomeModels(devices)
        connection = info.getConnectionInfo()
        if connection <> invalid and connection.gateway <> invalid
            gateway = connection.gateway
            if devices.doesExist(gateway) then devices[gateway].kind = "Router"
        end if
        for lastOctet = 1 to 254
            ip = "10.0.0." + lastOctet.toStr()
            if devices.doesExist(ip) then result.devices.push(devices[ip])
        end for
        result.ok = true
    end if
    result.elapsed_ms = clock.totalMilliseconds()
    result.note = "Responding devices only; sleeping or filtered devices may not appear. TV Roku and production server port excluded."
    payload = FormatJson(result)
    WriteAsciiFile("tmp:/network-scan.json", payload)
    transfer = CreateObject("roUrlTransfer")
    port = CreateObject("roMessagePort")
    transfer.setMessagePort(port)
    transfer.setUrl("http://10.0.0.18:8765/report")
    transfer.addHeader("Content-Type", "application/json")
    reported = false
    if transfer.asyncPostFromString(payload)
        reply = wait(5000, port)
        if type(reply) = "roUrlEvent"
            reported = reply.getResponseCode() >= 200 and reply.getResponseCode() < 300
        else
            transfer.asyncCancel()
        end if
    end if
    result.report_delivered = reported
    m.top.result = result
    print "M3U network_scan "; FormatJson(result)
end sub

function scanLocalHost(ip as String) as Boolean
    if Left(ip, 7) <> "10.0.0." then return false
    parts = ip.split(".")
    if parts.count() <> 4 then return false
    if not CreateObject("roRegex", "^[0-9]{1,3}$", "").isMatch(parts[3]) then return false
    value = parts[3].toInt()
    return value >= 1 and value <= 254
end function

sub scanAddDevice(devices as Object, ip as String, name as String, kind as String)
    if not scanLocalHost(ip) or ip = "10.0.0.2" then return
    if not devices.doesExist(ip) then devices[ip] = {ip: ip, name: "", kind: "Device", open_ports: [], ssdp: false}
    if name <> "" then devices[ip].name = Left(name, 100)
    if kind <> "" then devices[ip].kind = Left(kind, 100)
end sub

sub scanSsdp(devices as Object)
    messages = CreateObject("roMessagePort")
    udp = CreateObject("roDatagramSocket")
    udp.setMessagePort(messages)
    local = CreateObject("roSocketAddress")
    local.setAddress("0.0.0.0:0")
    udp.setAddress(local)
    target = CreateObject("roSocketAddress")
    target.setAddress("239.255.255.250:1900")
    udp.setSendToAddress(target)
    udp.notifyReadable(true)
    newline = Chr(13) + Chr(10)
    request = "M-SEARCH * HTTP/1.1" + newline + "HOST: 239.255.255.250:1900" + newline
    request += "MAN: " + Chr(34) + "ssdp:discover" + Chr(34) + newline + "MX: 2" + newline
    for each service in ["ssdp:all", "roku:ecp"]
        udp.sendStr(request + "ST: " + service + newline + newline)
    end for
    clock = CreateObject("roTimespan")
    clock.mark()
    while clock.totalMilliseconds() < 3000
        wait(50, messages)
        if udp.isReadable()
            reply = udp.receiveStr(8192)
            sender = udp.getReceivedFromAddress()
            if sender <> invalid
                ip = sender.getAddress().split(":")[0]
                if scanLocalHost(ip)
                    kind = "UPnP device"
                    if Instr(1, LCase(reply), "roku") > 0 then kind = "Roku"
                    if Instr(1, LCase(reply), "internetgatewaydevice") > 0 then kind = "Router"
                    scanAddDevice(devices, ip, "", kind)
                    devices[ip].ssdp = true
                end if
            end if
        end if
    end while
    udp.close()
end sub

function scanConnections(devices as Object) as Object
    ports = [80, 443, 8060, 8008, 8080, 22, 445, 5357, 8765, 53, 81, 554, 631, 1883, 8883, 3074, 9295, 9308, 8000, 8009, 8081, 8123, 8200, 8443, 8888, 8096, 8920, 32400, 62078, 9999]
    stats = {attempted: 0, finished: 0, connected: 0, refused: 0, timed_out: 0, errors: 0, complete: false}
    messages = CreateObject("roMessagePort")
    clock = CreateObject("roTimespan")
    clock.mark()
    host = 1
    portIndex = 0
    pending = []
    while (host <= 254 or pending.count() > 0) and clock.totalMilliseconds() < 80000
        while host <= 254 and pending.count() < 64
            ip = "10.0.0." + host.toStr()
            skip = host = 2 or (host = 18 and ports[portIndex] = 9999)
            if not skip
            target = CreateObject("roSocketAddress")
            target.setAddress(ip + ":" + ports[portIndex].toStr())
            sock = CreateObject("roStreamSocket")
            sock.setMessagePort(messages)
            sock.setSendToAddress(target)
            sock.notifyWritable(true)
            sock.notifyException(true)
            sock.connect()
            pending.push({socket: sock, ip: ip, port: ports[portIndex], started: clock.totalMilliseconds()})
            stats.attempted++
            end if
            portIndex++
            if portIndex >= ports.count()
                portIndex = 0
                host++
            end if
        end while
        wait(20, messages)
        for index = pending.count() - 1 to 0 step -1
            probe = pending[index]
            sock = probe.socket
            ' Non-blocking Connect leaves EINPROGRESS cached until retried after
            ' the socket becomes writable, even when the handshake has completed.
            if not sock.isConnected() and sock.isWritable() then sock.connect()
            connected = sock.isConnected() or sock.eIsConn()
            refused = sock.eConnRefused()
            failed = not sock.eOK() and not connected
            expired = clock.totalMilliseconds() - probe.started >= 500
            if connected or refused
                scanAddDevice(devices, probe.ip, "", "")
                if connected then devices[probe.ip].open_ports.push(probe.port)
            end if
            if connected or failed or expired
                stats.finished++
                if connected
                    stats.connected++
                else if refused
                    stats.refused++
                else if failed
                    stats.errors++
                else
                    stats.timed_out++
                end if
                sock.close()
                pending.delete(index)
            end if
        end for
    end while
    for each probe in pending
        probe.socket.close()
    end for
    stats.complete = host > 254 and pending.count() = 0
    stats.ports = ports
    stats.hosts = 253
    stats.excluded = ["10.0.0.2", "10.0.0.18:9999"]
    for each ip in devices
        device = devices[ip]
        for each portNumber in device.open_ports
            if portNumber = 8060
                scanRokuName(device)
                exit for
            end if
        end for
    end for
    return stats
end function

sub scanRokuName(device as Object)
    transfer = CreateObject("roUrlTransfer")
    messages = CreateObject("roMessagePort")
    transfer.setMessagePort(messages)
    transfer.setUrl("http://" + device.ip + ":8060/query/device-info")
    if not transfer.asyncGetToString() then return
    reply = wait(800, messages)
    if type(reply) <> "roUrlEvent"
        transfer.asyncCancel()
        return
    end if
    if reply.getResponseCode() <> 200 then return
    xml = CreateObject("roXMLElement")
    if not xml.parse(reply.getString()) then return
    if xml.getName() <> "device-info" then return
    device.kind = "Roku"
    for each child in xml.getChildElements()
        if child.getName() = "user-device-name" then device.name = Left(child.getText(), 100)
        if child.getName() = "model-name" then device.model = Left(child.getText(), 100)
    end for
end sub

' Query only product information using the public Kasa discovery protocol.
' No authentication, state changes, aliases, serial numbers, or location data.
function scanSmartHomeModels(devices as Object) as Object
    messages = CreateObject("roMessagePort")
    udp = CreateObject("roDatagramSocket")
    udp.setMessagePort(messages)
    local = CreateObject("roSocketAddress")
    local.setAddress("0.0.0.0:0")
    udp.setAddress(local)
    udp.notifyReadable(true)
    plain = CreateObject("roByteArray")
    plain.fromAsciiString(FormatJson({system: {get_sysinfo: {}}}))
    legacy = CreateObject("roByteArray")
    key = 171
    for each value in plain
        key = scanXorByte(key, value)
        legacy.push(key)
    end for
    modern = CreateObject("roByteArray")
    modern.fromHexString("020000010000000000000000463cb5d3")
    stats = {requests: 0, product_responses: 0}
    for each ip in devices
        if ip <> "10.0.0.1" and ip <> "10.0.0.2" and ip <> "10.0.0.18" and ip <> "10.0.0.29"
            for each portNumber in [9999, 20002]
                target = CreateObject("roSocketAddress")
                target.setAddress(ip + ":" + portNumber.toStr())
                udp.setSendToAddress(target)
                payload = legacy
                if portNumber = 20002 then payload = modern
                if udp.send(payload, 0, payload.count()) > 0 then stats.requests++
            end for
        end if
    end for
    clock = CreateObject("roTimespan")
    clock.mark()
    while clock.totalMilliseconds() < 3000
        wait(20, messages)
        if udp.isReadable()
            buffer = CreateObject("roByteArray")
            buffer[8191] = 0
            received = udp.receive(buffer, 0, 8192)
            sender = udp.getReceivedFromAddress()
            if received > 0 and sender <> invalid
                address = sender.getAddress()
                ip = address.split(":")[0]
                if devices.doesExist(ip) and ip <> "10.0.0.2" and ip <> "10.0.0.18"
                    decoded = CreateObject("roByteArray")
                    if Right(address, 5) = "20002"
                        for index = 16 to received - 1
                            decoded.push(buffer[index])
                        end for
                    else
                        key = 171
                        for index = 0 to received - 1
                            decoded.push(scanXorByte(key, buffer[index]))
                            key = buffer[index]
                        end for
                    end if
                    response = ParseJson(decoded.toAsciiString())
                    if type(response) = "roAssociativeArray"
                        product = invalid
                        if response.doesExist("system")
                            if type(response.system) = "roAssociativeArray" then product = response.system.get_sysinfo
                        else if response.doesExist("result")
                            product = response.result
                        end if
                        if type(product) = "roAssociativeArray"
                            metadata = {}
                            for each field in ["model", "device_model", "dev_name", "device_type", "type", "mic_type", "hw_ver", "hw_version"]
                                if product.doesExist(field)
                                    if type(product[field]) = "String" or type(product[field]) = "roString" then metadata[field] = Left(product[field], 100)
                                end if
                            end for
                            if metadata.count() > 0
                                if not devices[ip].doesExist("product_metadata") then stats.product_responses++
                                devices[ip].product_metadata = metadata
                            end if
                        end if
                    end if
                end if
            end if
        end if
    end while
    udp.close()
    return stats
end function

function scanXorByte(first as Integer, second as Integer) as Integer
    result = 0
    bit = 1
    for index = 0 to 7
        if (first mod 2) <> (second mod 2) then result += bit
        first = Int(first / 2)
        second = Int(second / 2)
        bit *= 2
    end for
    return result
end function
