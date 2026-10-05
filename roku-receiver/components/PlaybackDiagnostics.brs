' Opt-in telemetry for a diagnostic package; never changes player recovery.
sub initPlaybackDiagnostics(config as Dynamic)
    m.playbackDiagnostics = false
    if config <> invalid
        if config.DoesExist("playback_diagnostics") then m.playbackDiagnostics = config.playback_diagnostics = true
    end if
    if not m.playbackDiagnostics then return
    m.video.enableDecoderStats = true
    m.diagnosticClock = CreateObject("roTimespan")
    m.diagnosticClock.mark()
    m.previousDiagnosticPosition = invalid
    m.video.observeField("position", "onDiagnosticPosition")
    m.video.observeField("downloadedSegment", "onDiagnosticDownload")
    m.video.observeField("streamingSegment", "onDiagnosticStreaming")
end sub

sub resetPlaybackDiagnostics()
    if not m.playbackDiagnostics then return
    m.diagnosticClock.mark()
    m.previousDiagnosticPosition = invalid
    playbackDiagnostic("start")
end sub

function diagnosticNumbers(values as Dynamic, keys as Object) as Object
    output = {}
    if values = invalid then return output
    for each key in keys
        if values.DoesExist(key)
            value = values[key]
            valueType = type(value)
            if valueType = "roInt" or valueType = "roInteger"
                value = value.getInt()
            else if valueType = "roFloat"
                value = value.getFloat()
            else if valueType = "roDouble"
                value = value.getDouble()
            else if valueType = "roLongInteger"
                value = value.getLongInt()
            else if valueType = "roBoolean"
                value = value.getBoolean()
            end if
            valueType = type(value)
            if valueType = "Integer" or valueType = "Float" or valueType = "Double" or valueType = "LongInteger" or valueType = "Boolean"
                output[key] = value
            end if
        end if
    end for
    return output
end function

function playbackDiagnosticSnapshot(eventName as String) as Object
    result = {event: eventName, elapsed_ms: m.diagnosticClock.totalMilliseconds(), state: m.video.state, position: m.video.position}
    result.rendered = diagnosticNumbers(m.video.positionInfo, ["audio", "video", "clip_id", "epoch"])
    result.buffer = diagnosticNumbers(m.video.bufferingStatus, ["percentage", "isUnderrun", "prebufferDone", "actualStart"])
    result.segment = diagnosticNumbers(m.video.streamingSegment, ["segSequence", "segStart", "latency", "segBitrateBps", "segType"])
    if eventName = "download"
        result.download = diagnosticNumbers(m.video.downloadedSegment, ["Status", "SegSequence", "DownloadDuration", "SegSize", "SegStart", "BitrateBPS", "SegType"])
    end if
    if m.video.state = "error"
        result.error = diagnosticNumbers(m.video.errorInfo, ["clipId", "errcode", "drmerrcode", "ignored"])
        result.error.code = m.video.errorCode
        info = m.video.errorInfo
        message = m.video.errorMsg + " " + m.video.errorStr
        if info <> invalid
            if info.DoesExist("dbgmsg") then message += " " + info.dbgmsg
        end if
        result.error.hdcp = Instr(1, LCase(message), "hdcp") > 0
    end if
    return result
end function

sub playbackDiagnostic(eventName as String)
    if not m.playbackDiagnostics then return
    print "M3U diagnostic "; FormatJson(playbackDiagnosticSnapshot(eventName))
end sub

function diagnosticBackstep(previous as Dynamic, current as Object) as Boolean
    if previous = invalid then return false
    if previous.clip_id <> current.clip_id or previous.epoch <> current.epoch then return false
    if previous.video <> invalid and current.video <> invalid
        if current.video < previous.video - 0.25 then return true
    end if
    if previous.audio <> invalid and current.audio <> invalid
        if current.audio < previous.audio - 0.25 then return true
    end if
    return false
end function

sub onDiagnosticPosition()
    if not m.playing or not m.playbackDiagnostics then return
    snapshot = playbackDiagnosticSnapshot("position")
    snapshot.backstep = diagnosticBackstep(m.previousDiagnosticPosition, snapshot.rendered)
    m.previousDiagnosticPosition = snapshot.rendered
    print "M3U diagnostic "; FormatJson(snapshot)
end sub

sub onDiagnosticDownload()
    if m.playing then playbackDiagnostic("download")
end sub

sub onDiagnosticStreaming()
    if m.playing then playbackDiagnostic("segment")
end sub
