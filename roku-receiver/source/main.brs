sub Main(args as Dynamic)
    screen = CreateObject("roSGScreen")
    port = CreateObject("roMessagePort")
    screen.SetMessagePort(port)
    input = CreateObject("roInput")
    input.SetMessagePort(port)

    diagnostics = false
    config = ParseJson(ReadAsciiFile("pkg:/server.json"))
    if config <> invalid
        if config.DoesExist("playback_diagnostics") then diagnostics = config.playback_diagnostics = true
    end if
    if diagnostics
        diagnosticClock = CreateObject("roTimespan")
        diagnosticClock.mark()
        memoryMonitor = CreateObject("roAppMemoryMonitor")
        hdmiStatus = CreateObject("roHdmiStatus")
        print "M3U diagnostic "; FormatJson({event: "hdmi", link: diagnosticHdmiLink(hdmiStatus)})
        manager = CreateObject("roAppManager")
        previousExit = manager.getLastExitInfo()
        if previousExit <> invalid
            result = {event: "previous_exit", screensaver_minutes: manager.getScreensaverTimeout()}
            for each key in ["timestamp", "exit_code", "app_state", "media_player_state", "mem_limit"]
                if previousExit.DoesExist(key) then result[key] = previousExit[key]
            end for
            print "M3U diagnostic "; FormatJson(result)
        end if
    end if

    scene = screen.CreateScene("MainScene")
    screen.Show()

    if args <> invalid and args.network_scan <> invalid
        if args.network_scan = "1" then scene.callFunc("scanNetwork")
    end if

    if args <> invalid and args.contentId <> invalid
        scene.callFunc("playUrl", args.contentId)
    end if

    while true
        timeout = 0
        if diagnostics then timeout = 30000
        msg = wait(timeout, port)
        msgType = type(msg)

        if msgType = "roSGScreenEvent"
            if msg.isScreenClosed()
                if diagnostics then print "M3U diagnostic "; FormatJson({event: "screen_closed", elapsed_ms: diagnosticClock.totalMilliseconds()})
                return
            end if
        else if msg = invalid and diagnostics
            if memoryMonitor <> invalid
                print "M3U diagnostic "; FormatJson({event: "memory", elapsed_ms: diagnosticClock.totalMilliseconds(), percent: memoryMonitor.getMemoryLimitPercent(), available_kb: memoryMonitor.getChannelAvailableMemory(), hdmi: diagnosticHdmiLink(hdmiStatus)})
            end if
        else if msgType = "roInputEvent"
            if msg.IsInput()
                info = msg.GetInfo()
                if info <> invalid and info.DoesExist("network_scan")
                    if info.network_scan = "1" then scene.callFunc("scanNetwork")
                end if
                if info <> invalid and info.DoesExist("contentid")
                    scene.callFunc("playUrl", info.contentid)
                end if
            end if
        end if
    end while
end sub

function diagnosticHdmiLink(status as Dynamic) as Object
    result = {}
    if status = invalid then return result
    result.connected = status.isConnected()
    version = status.getHdcpVersion()
    if version <> "" and version <> "1.4" and version <> "2.2" then version = "unknown"
    result.hdcp_version = version
    result.hdcp_active = status.isHdcpActive("1.4")
    return result
end function
