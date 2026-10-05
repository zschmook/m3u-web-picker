sub initMovieLighting(config as Dynamic)
    m.movieLighting = invalid
    m.movieLightTask = invalid
    m.movieLightSnapshot = invalid
    m.movieLightDesired = "restore"
    m.movieLightApplied = "restore"
    m.movieLightAttempted = ""
    m.movieLightConfigLoading = false
    m.movieLightRevision = -1
    m.movieLightOriginalSettings = invalid
    m.movieLightRokuHost = ""
    interfaces = CreateObject("roDeviceInfo").getIPAddrs()
    for each iface in interfaces
        if movieLightingLocalAddress(interfaces[iface]) then m.movieLightRokuHost = interfaces[iface]
    end for
end sub

function movieLightingLocalAddress(address as String) as Boolean
    if not CreateObject("roRegex", "^[0-9]{1,3}[.][0-9]{1,3}[.][0-9]{1,3}[.][0-9]{1,3}$", "").isMatch(address) then return false
    parts = address.tokenize(".")
    for each part in parts
        if Val(part) > 255 then return false
    end for
    first = Val(parts[0])
    second = Val(parts[1])
    return first = 10 or (first = 172 and second >= 16 and second <= 31) or (first = 192 and second = 168)
end function

sub refreshMovieLighting()
    if m.server = "" or m.movieLightRokuHost = "" or m.movieLightConfigLoading then return
    m.movieLightConfigLoading = true
    requestApi("/api/roku/movie-lighting?roku_host=" + m.movieLightRokuHost, "GET", {}, "movie_lighting")
end sub

sub onMovieLightingConfig(result as Object)
    m.movieLightConfigLoading = false
    if not result.ok
        runMovieLighting()
        return
    end if
    settings = result.data
    if type(settings) <> "roAssociativeArray" then return
    if settings.enabled <> true
        m.movieLighting = invalid
        m.movieLightDesired = "restore"
        m.movieLightAttempted = ""
        runMovieLighting()
        return
    end if
    if settings.roku_host <> m.movieLightRokuHost then return
    if type(settings.target) <> "roAssociativeArray" then return
    if settings.target.ip = invalid or settings.target.name = invalid or settings.target.model = invalid or settings.target.device_id = invalid then return
    if settings.brightness = invalid or settings.paused_brightness = invalid or settings.transition_ms = invalid then return
    if settings.brightness < 1 or settings.brightness > 100 then return
    if settings.paused_brightness < 1 or settings.paused_brightness > 100 then return
    if settings.transition_ms < 0 or settings.transition_ms > 5000 then return
    ' A different bulb waits until this movie ends. Keep pause/restore bound
    ' to the original device, even if the web selection changes mid-movie.
    if m.movieLightSnapshot <> invalid and m.movieLightOriginalSettings <> invalid
        if settings.target.device_id <> m.movieLightOriginalSettings.target.device_id
            runMovieLighting()
            return
        end if
    end if
    if settings.revision <> m.movieLightRevision
        m.movieLightRevision = settings.revision
        m.movieLightApplied = ""
        m.movieLightAttempted = ""
    end if
    m.movieLighting = settings
    runMovieLighting()
end sub

function movieLightingPlayback() as Boolean
    if m.movie then return true
    if m.currentChannel <> invalid
        if m.currentChannel.is_movie = true then return true
    end if
    if m.session <> invalid
        if m.session.kind = "movie" then return true
    end if
    return false
end function

sub setMovieLighting(active as Boolean)
    mode = "guide"
    if active then mode = "dim"
    setMovieLightMode(mode)
end sub

sub setMovieLightMode(mode as String)
    if mode <> "dim" and mode <> "pause" and mode <> "guide" and mode <> "restore" then return
    if mode <> m.movieLightDesired then m.movieLightAttempted = ""
    m.movieLightDesired = mode
    if mode = "restore"
        runMovieLighting()
    else
        if mode = "guide" and m.movieLightSnapshot = invalid and m.movieLightTask = invalid then return
        ' Fetch saved preferences on playback changes, without a recurring
        ' timer or any blocking network work on the render thread.
        refreshMovieLighting()
    end if
end sub

sub runMovieLighting()
    if m.movieLightTask <> invalid then return
    action = m.movieLightDesired
    if action = m.movieLightApplied or action = m.movieLightAttempted then return
    if (action = "restore" or action = "guide") and m.movieLightSnapshot = invalid then return
    if action <> "restore" and action <> "guide" and m.movieLighting = invalid then return
    if action <> "restore" and m.movieLightConfigLoading then return
    m.movieLightAttempted = action
    ' A single bounded worker owns light changes; buffering/resume events do
    ' not repeat them, and the latest pause/resume/exit intent runs next.
    task = CreateObject("roSGNode", "MovieLightTask")
    if action = "restore" or action = "guide"
        task.settings = m.movieLightOriginalSettings
        if action = "guide" and m.movieLighting <> invalid
            if m.movieLighting.target.device_id = m.movieLightOriginalSettings.target.device_id then task.settings = m.movieLighting
        end if
    else
        task.settings = m.movieLighting
    end if
    task.action = action
    if m.movieLightSnapshot <> invalid then task.snapshot = m.movieLightSnapshot
    task.observeField("result", "onMovieLightingDone")
    m.movieLightTask = task
    task.control = "run"
end sub

sub onMovieLightingDone(event as Object)
    task = event.getRoSGNode()
    result = event.getData()
    if m.movieLightTask = invalid then return
    if not m.movieLightTask.isSameNode(task) then return
    m.movieLightTask = invalid
    if result.action = "dim" or result.action = "pause"
        ' Retain the original state even if acknowledgement was lost after
        ' sending: returning to the guide still resets the same light.
        if m.movieLightSnapshot = invalid and result.command_sent = true and result.snapshot <> invalid
            m.movieLightSnapshot = result.snapshot
            m.movieLightOriginalSettings = task.settings
            if m.movieLighting <> invalid
                if m.movieLighting.target.device_id <> task.settings.target.device_id
                    m.movieLighting = task.settings
                    m.movieLightRevision = task.settings.revision
                end if
            end if
        end if
    else if (result.action = "restore" or result.action = "guide") and result.ok
        m.movieLightSnapshot = invalid
        m.movieLightOriginalSettings = invalid
        if m.movieLightDesired = "dim" or m.movieLightDesired = "pause" then refreshMovieLighting()
    end if
    if result.ok
        if result.action = "restore" or result.action = "guide" or task.settings.revision = m.movieLightRevision
            m.movieLightApplied = result.action
        else
            m.movieLightApplied = ""
            m.movieLightAttempted = ""
        end if
    end if
    print "M3U movie lighting "; FormatJson(result)
    runMovieLighting()
end sub
