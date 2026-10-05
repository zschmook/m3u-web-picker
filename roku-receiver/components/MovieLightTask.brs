sub init()
    m.top.functionName = "changeMovieLight"
end sub

sub changeMovieLight()
    settings = m.top.settings
    action = m.top.action
    result = {action: action, source: "roku", ok: false, command_sent: false, snapshot: {}, remaining_snapshots: {}, lights: []}
    if type(settings) <> "roAssociativeArray"
        result.error = "invalid_settings"
        m.top.result = result
        return
    end if
    if action <> "dim" and action <> "pause" and action <> "guide" and action <> "restore"
        result.error = "invalid_action"
        m.top.result = result
        return
    end if
    targets = settings.targets
    if type(targets) <> "roArray" then targets = [settings.target]
    if targets.count() = 0 or targets.count() > 32
        result.error = "invalid_group"
        m.top.result = result
        return
    end if
    previous = m.top.snapshot
    if type(previous) <> "roAssociativeArray" then previous = {}
    ' Carry original states across pause/resume and partial failures.
    for each identity in previous
        result.snapshot[identity] = movieLightState(previous[identity])
    end for
    reads = []
    selected = []
    for each target in targets
        if type(target) = "roAssociativeArray"
            if GetInterface(target.ip, "ifString") <> invalid and GetInterface(target.device_id, "ifString") <> invalid
                if movieLightLocalAddress(target.ip)
                    if action <> "restore" or previous.doesExist(target.device_id)
                        selected.push(target)
                        reads.push({ip: target.ip, request: {system: {get_sysinfo: {}}}})
                    end if
                end if
            end if
        end if
    end for
    products = movieLightBatch(reads)
    controls = []
    changes = []
    for index = 0 to selected.count() - 1
        target = selected[index]
        item = movieLightPrepare(settings, action, target, movieLightProduct(products[index]), previous[target.device_id])
        item.device_id = target.device_id
        item.target = target.name
        result.lights.push(item)
        if item.desired <> invalid
            if not result.snapshot.doesExist(target.device_id) then result.snapshot[target.device_id] = movieLightState(item.before)
            item.desired.transition_period = settings.transition_ms
            controls.push({ip: target.ip, request: {"smartlife.iot.smartbulb.lightingservice": {transition_light_state: item.desired}}})
            changes.push({target: target, item: item})
            item.command_sent = true
            result.command_sent = true
        end if
    end for
    replies = movieLightBatch(controls)
    if changes.count() > 0 then sleep(settings.transition_ms + 200)
    verify = []
    for index = 0 to changes.count() - 1
        changes[index].item.acknowledged = movieLightAcknowledged(replies[index])
        verify.push({ip: changes[index].target.ip, request: {system: {get_sysinfo: {}}}})
    end for
    afters = movieLightBatch(verify)
    for index = 0 to changes.count() - 1
        change = changes[index]
        after = movieLightProduct(afters[index])
        if after <> invalid
            if after.deviceId = change.target.device_id and after.model = change.target.model and type(after.light_state) = "roAssociativeArray"
                change.item.after = movieLightState(after.light_state)
                change.item.ok = change.item.after.on_off = change.item.desired.on_off
                if change.item.desired.brightness <> invalid then change.item.ok = change.item.ok and change.item.after.brightness = change.item.desired.brightness
            end if
        end if
        if not change.item.ok then change.item.error = "light_change_not_confirmed"
    end for
    result.ok = selected.count() = targets.count() or action = "restore"
    for each item in result.lights
        if not item.ok then result.ok = false
        if action = "guide" or action = "restore"
            if not item.ok and result.snapshot.doesExist(item.device_id) then result.remaining_snapshots[item.device_id] = movieLightState(result.snapshot[item.device_id])
        end if
        item.delete("desired")
    end for
    m.top.result = result
end sub

function movieLightPrepare(settings as Object, action as String, target as Object, product as Dynamic, previous as Dynamic) as Object
    item = {ok: false, command_sent: false}
    if product = invalid
        item.error = "light_unavailable"
    else if product.deviceId <> target.device_id or product.model <> target.model
        item.error = "light_identity_mismatch"
    else if type(product.light_state) <> "roAssociativeArray"
        item.error = "dimmable_bulb_required"
    else
        state = product.light_state
        item.before = movieLightState(state)
        if action = "dim"
            if state.on_off = 0
                item.ok = true
                item.skipped = "already_off"
            else if state.on_off <> 1 or state.brightness = invalid
                item.error = "brightness_unavailable"
            else
                item.desired = {on_off: 1, brightness: settings.brightness}
            end if
        else if action = "pause" or action = "guide"
            if state.on_off <> 0 and state.on_off <> 1
                item.error = "original_state_unavailable"
            else
                item.desired = {on_off: 1, brightness: settings.paused_brightness}
            end if
        else if type(previous) <> "roAssociativeArray"
            item.error = "original_state_unavailable"
        else if previous.on_off = 0
            item.desired = {on_off: 0}
        else if previous.on_off <> 1 or previous.brightness = invalid
            item.error = "invalid_original_state"
        else
            item.desired = {on_off: 1, brightness: previous.brightness}
        end if
    end if
    return item
end function

' Send each phase together, with one shared timeout and fade for the group.
' An unavailable bulb cannot delay every subsequent bulb by its own timeout.
function movieLightBatch(requests as Object) as Object
    results = []
    if requests.count() = 0 then return results
    messages = CreateObject("roMessagePort")
    sockets = []
    for each request in requests
        socket = CreateObject("roDatagramSocket")
        socket.setMessagePort(messages)
        address = CreateObject("roSocketAddress")
        address.setAddress("0.0.0.0:0")
        socket.setAddress(address)
        address.setAddress(request.ip + ":9999")
        socket.setSendToAddress(address)
        socket.notifyReadable(true)
        plain = CreateObject("roByteArray")
        plain.fromAsciiString(FormatJson(request.request))
        encrypted = CreateObject("roByteArray")
        key = 171
        for each byte in plain
            key = movieLightXor(key, byte)
            encrypted.push(key)
        end for
        sent = socket.send(encrypted, 0, encrypted.count()) = encrypted.count()
        sockets.push({socket: socket, ip: request.ip, done: not sent})
        results.push(invalid)
    end for
    timer = CreateObject("roTimespan")
    timer.mark()
    while timer.totalMilliseconds() < 2500
        remaining = 0
        for index = 0 to sockets.count() - 1
            entry = sockets[index]
            if not entry.done
                if entry.socket.isReadable()
                    buffer = CreateObject("roByteArray")
                    buffer[8191] = 0
                    received = entry.socket.receive(buffer, 0, 8192)
                    sender = entry.socket.getReceivedFromAddress()
                    if received > 0 and sender <> invalid
                        if sender.getAddress() = entry.ip + ":9999"
                            decoded = CreateObject("roByteArray")
                            key = 171
                            for offset = 0 to received - 1
                                decoded.push(movieLightXor(key, buffer[offset]))
                                key = buffer[offset]
                            end for
                            results[index] = ParseJson(decoded.toAsciiString())
                            entry.done = true
                        end if
                    end if
                end if
                if not entry.done then remaining += 1
            end if
        end for
        if remaining = 0 then exit while
        wait(20, messages)
    end while
    for each entry in sockets
        entry.socket.close()
    end for
    return results
end function

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
