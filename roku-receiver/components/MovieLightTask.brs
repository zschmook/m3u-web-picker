sub init()
    m.top.functionName = "changeMovieLight"
end sub

sub changeMovieLight()
    settings = m.top.settings
    action = m.top.action
    result = {action: action, source: "roku", ok: false, command_sent: false}
    if type(settings) <> "roAssociativeArray"
        result.error = "invalid_settings"
    else if action <> "dim" and action <> "pause" and action <> "guide" and action <> "restore"
        result.error = "invalid_action"
    else
        target = settings.target
        if type(target) <> "roAssociativeArray"
            result.error = "invalid_target"
        else if not movieLightLocalAddress(target.ip)
            result.error = "home_network_required"
        else
            result.target = target.name
            product = movieLightProduct(movieLightRequest(target.ip, {system: {get_sysinfo: {}}}))
            if product = invalid
                result.error = "light_unavailable"
            else if product.alias = invalid or product.model = invalid or product.deviceId = invalid or target.device_id = invalid
                result.error = "light_identity_unavailable"
            else if product.deviceId <> target.device_id or product.model <> target.model
                result.error = "light_identity_mismatch"
            else if type(product.light_state) <> "roAssociativeArray"
                result.error = "dimmable_bulb_required"
            else
                state = product.light_state
                result.before = movieLightState(state)
                desired = invalid
                if action = "dim"
                    if state.on_off = 0
                        result.ok = true
                        result.skipped = "already_off"
                    else if state.on_off <> 1 or state.brightness = invalid
                        result.error = "brightness_unavailable"
                    else
                        result.snapshot = {on_off: state.on_off, brightness: state.brightness}
                        desired = {on_off: 1, brightness: settings.brightness}
                    end if
                else if action = "pause" or action = "guide"
                    if state.on_off <> 0 and state.on_off <> 1
                        result.error = "original_state_unavailable"
                    else
                        result.snapshot = movieLightState(state)
                        desired = {on_off: 1, brightness: settings.paused_brightness}
                    end if
                else
                    previous = m.top.snapshot
                    if type(previous) <> "roAssociativeArray"
                        result.error = "original_state_unavailable"
                    else if previous.on_off = 0
                        desired = {on_off: 0}
                    else if previous.on_off <> 1 or previous.brightness = invalid
                        result.error = "invalid_original_state"
                    else
                        desired = {on_off: previous.on_off, brightness: previous.brightness}
                    end if
                end if
                if desired <> invalid
                    desired.transition_period = settings.transition_ms
                    reply = movieLightRequest(target.ip, {"smartlife.iot.smartbulb.lightingservice": {transition_light_state: desired}})
                    result.command_sent = true
                    result.acknowledged = movieLightAcknowledged(reply)
                    ' Verify after the fade, independently of acknowledgement.
                    sleep(settings.transition_ms + 200)
                    after = movieLightProduct(movieLightRequest(target.ip, {system: {get_sysinfo: {}}}))
                    if after <> invalid
                        if type(after.light_state) = "roAssociativeArray"
                            result.after = movieLightState(after.light_state)
                            result.ok = result.after.on_off = desired.on_off
                            if desired.brightness <> invalid then result.ok = result.ok and result.after.brightness = desired.brightness
                        end if
                    end if
                    if not result.ok then result.error = "light_change_not_confirmed"
                end if
            end if
        end if
    end if
    m.top.result = result
end sub

function movieLightLocalAddress(address as String) as Boolean
    if not CreateObject("roRegex", "^[0-9]{1,3}[.][0-9]{1,3}[.][0-9]{1,3}[.][0-9]{1,3}$", "").isMatch(address) then return false
    parts = address.tokenize(".")
    for each part in parts
        if Val(part) > 255 then return false
    end for
    first = Val(parts[0])
    second = Val(parts[1])
    return first = 10 or (first = 172 and second >= 16 and second <= 31) or (first = 192 and second = 168)
end function

function movieLightState(state as Object) as Object
    result = {}
    if state.on_off <> invalid then result.on_off = state.on_off
    if state.brightness <> invalid then result.brightness = state.brightness
    return result
end function

function movieLightProduct(response as Dynamic) as Dynamic
    if type(response) <> "roAssociativeArray" then return invalid
    if type(response.system) <> "roAssociativeArray" then return invalid
    if type(response.system.get_sysinfo) <> "roAssociativeArray" then return invalid
    return response.system.get_sysinfo
end function

function movieLightAcknowledged(response as Dynamic) as Boolean
    if type(response) <> "roAssociativeArray" then return false
    service = response["smartlife.iot.smartbulb.lightingservice"]
    if type(service) <> "roAssociativeArray" then return false
    reply = service.transition_light_state
    if type(reply) <> "roAssociativeArray" then return false
    return reply.err_code = 0
end function

function movieLightRequest(ip as String, request as Object) as Dynamic
    messages = CreateObject("roMessagePort")
    socket = CreateObject("roDatagramSocket")
    socket.setMessagePort(messages)
    address = CreateObject("roSocketAddress")
    address.setAddress("0.0.0.0:0")
    socket.setAddress(address)
    address.setAddress(ip + ":9999")
    socket.setSendToAddress(address)
    socket.notifyReadable(true)
    plain = CreateObject("roByteArray")
    plain.fromAsciiString(FormatJson(request))
    encrypted = CreateObject("roByteArray")
    key = 171
    for each byte in plain
        key = movieLightXor(key, byte)
        encrypted.push(key)
    end for
    if socket.send(encrypted, 0, encrypted.count()) <> encrypted.count()
        socket.close()
        return invalid
    end if
    clock = CreateObject("roTimespan")
    clock.mark()
    while clock.totalMilliseconds() < 2500
        wait(20, messages)
        if socket.isReadable()
            buffer = CreateObject("roByteArray")
            buffer[8191] = 0
            received = socket.receive(buffer, 0, 8192)
            sender = socket.getReceivedFromAddress()
            if received > 0 and sender <> invalid
                if sender.getAddress() = ip + ":9999"
                    decoded = CreateObject("roByteArray")
                    key = 171
                    for index = 0 to received - 1
                        decoded.push(movieLightXor(key, buffer[index]))
                        key = buffer[index]
                    end for
                    socket.close()
                    return ParseJson(decoded.toAsciiString())
                end if
            end if
        end if
    end while
    socket.close()
    return invalid
end function

function movieLightXor(first as Integer, second as Integer) as Integer
    value = 0
    bit = 1
    for index = 0 to 7
        if (first mod 2) <> (second mod 2) then value += bit
        first = Int(first / 2)
        second = Int(second / 2)
        bit *= 2
    end for
    return value
end function
