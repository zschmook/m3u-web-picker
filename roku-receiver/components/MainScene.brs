sub init()
    m.top.backgroundColor = "0x101827ff"
    m.top.backgroundURI = ""
    m.video = m.top.findNode("video")
    m.panel = m.top.findNode("guidePanel")
    m.grid = m.top.findNode("grid")
    m.status = m.top.findNode("status")
    m.playerStatus = m.top.findNode("playerStatus")
    m.tasks = []
    m.channels = []
    m.session = invalid
    m.currentChannel = invalid
    m.playing = false
    m.movie = false
    m.liveDelay = 4
    m.guideLoading = false
    m.generation = 0
    m.guideGeneration = 0
    m.server = ""
    m.registry = CreateObject("roRegistrySection", "M3UTV")
    if m.registry.exists("server") then m.server = m.registry.read("server")
    config = ParseJson(ReadAsciiFile("pkg:/server.json"))
    if m.server = "" and config <> invalid
        if config.server <> invalid then m.server = config.server
    end if
    m.video.observeField("state", "onVideoState")
    m.video.observeField("bufferingStatus", "onBufferingStatus")
    m.grid.observeField("programSelected", "onProgramSelected")
    m.grid.observeField("channelInfoSelected", "onChannelSelected")
    m.top.findNode("refreshTimer").observeField("fire", "refreshGuide")
    m.top.findNode("heartbeatTimer").observeField("fire", "keepPlaybackAlive")
    m.top.findNode("refreshTimer").control = "start"
    m.top.findNode("heartbeatTimer").control = "start"
    m.top.setFocus(true)
    if m.server = ""
        showServerDialog()
    else
        refreshGuide()
    end if
end sub

sub requestApi(path as String, method as String, body as Object, purpose as String, requestId = 0 as Integer)
    if m.server = "" then return
    task = CreateObject("roSGNode", "ApiTask")
    task.url = m.server + path
    task.method = method
    task.body = FormatJson(body)
    task.purpose = purpose
    task.requestId = requestId
    task.observeField("result", "onApiResult")
    m.tasks.push(task)
    task.control = "run"
end sub

sub refreshGuide()
    if m.server = "" or m.guideLoading then return
    m.guideLoading = true
    if not m.playing and m.channels.count() = 0
        m.status.visible = true
        m.status.text = "Loading guide..."
        m.grid.visible = false
    end if
    requestApi("/api/roku/guide", "GET", {}, "guide", m.guideGeneration)
end sub

sub onApiResult(event as Object)
    task = event.getRoSGNode()
    result = event.getData()
    for i = m.tasks.count() - 1 to 0 step -1
        if m.tasks[i].isSameNode(task) then m.tasks.delete(i)
    end for
    if result.purpose = "guide"
        if result.requestId <> m.guideGeneration then return
        m.guideLoading = false
        if result.ok
            m.channels = result.data.channels
            if m.currentChannel <> invalid
                for each channel in m.channels
                    if channel.play_url = m.currentChannel.play_url
                        m.currentChannel = channel
                        exit for
                    end if
                end for
            end if
            renderGuide(result.data.server_time)
        else
            m.status.text = result.error + " Press * to change server."
            m.status.visible = true
        end if
    else if result.purpose = "play"
        if result.requestId <> m.generation
            if result.ok then requestApi("/api/roku/playback/stop", "POST", result.data, "stop")
            return
        end if
        if result.ok
            m.session = result.data
            m.movie = result.data.kind = "movie"
            m.liveDelay = 4
            if result.data.live_delay_seconds <> invalid then m.liveDelay = result.data.live_delay_seconds
            startVideo(result.data.media_url)
        else
            m.playerStatus.text = result.error + " Press Back for the guide."
        end if
    end if
end sub

sub renderGuide(serverTime as Integer)
    ' Keep ContentNode construction and hundreds of UI mutations off the
    ' render thread while the hidden guide sits underneath full-screen video.
    if m.playing then return
    root = CreateObject("roSGNode", "ContentNode")
    for each channel in m.channels
        row = root.createChild("ContentNode")
        row.title = channel.number + "  " + channel.name
        logo = channel.logo
        if Left(logo, 1) = "/" then logo = m.server + logo
        row.addFields({number: channel.number, name: channel.name, groupName: channel.group, logoUrl: logo})
        for each programme in channel.programmes
            cell = row.createChild("ContentNode")
            cell.title = programme.title
            cell.playStart = programme.start
            cell.playDuration = programme.stop - programme.start
            cell.addFields({subtitle: programme.subtitle})
        end for
    end for
    focused = m.grid.channelFocused
    m.grid.contentStartTime = serverTime - 1800
    m.grid.content = root
    m.grid.jumpToChannel = focused
    now = CreateObject("roDateTime")
    now.fromSeconds(serverTime)
    m.grid.jumpToTime = now.toISOString()
    m.status.visible = false
    m.grid.visible = true
    if not m.playing and m.top.dialog = invalid then m.grid.setFocus(true)
end sub

function selectedChannel() as Dynamic
    index = m.grid.channelFocused
    if index < 0 or index >= m.channels.count() then return invalid
    return m.channels[index]
end function

function selectedProgramme() as Dynamic
    channel = selectedChannel()
    if channel = invalid then return invalid
    index = m.grid.programFocused
    if index < 0 or index >= channel.programmes.count() then return invalid
    return channel.programmes[index]
end function

sub onChannelSelected(event as Object)
    index = event.getData()
    if index >= 0 and index < m.channels.count() then playChannel(m.channels[index])
end sub

sub onProgramSelected()
    channel = selectedChannel()
    programme = selectedProgramme()
    if channel = invalid or programme = invalid then return
    now = CreateObject("roDateTime")
    if programme.start <= now.asSeconds() and programme.stop > now.asSeconds()
        playChannel(channel)
    else
        dialog = CreateObject("roSGNode", "Dialog")
        dialog.title = programme.title
        dialog.message = programme.description
        dialog.buttons = ["Watch channel live", "Close"]
        dialog.observeField("buttonSelected", "onFutureSelected")
        m.dialogChannel = channel
        m.top.dialog = dialog
    end if
end sub

sub onFutureSelected(event as Object)
    choice = event.getData()
    m.top.dialog = invalid
    if choice = 0 then playChannel(m.dialogChannel)
end sub

sub releasePlayback()
    if m.session <> invalid
        if m.session.token <> "" then requestApi("/api/roku/playback/stop", "POST", m.session, "stop")
    end if
    m.session = invalid
end sub

sub playChannel(channel as Object, restartUrl = "" as String)
    m.generation++
    releasePlayback()
    m.video.control = "stop"
    m.currentChannel = channel
    m.movie = restartUrl <> ""
    m.playing = true
    m.grid.active = false
    m.panel.visible = false
    m.video.visible = false
    m.playerStatus.visible = true
    m.playerStatus.text = "Loading " + channel.name + "..."
    m.top.setFocus(true)
    mode = "live"
    if m.movie then mode = "movie"
    lowLatency = false
    if channel.is_sports <> invalid then lowLatency = channel.is_sports
    requestApi("/api/roku/playback", "POST", {play_url: channel.play_url, mode: mode, restart_url: restartUrl, low_latency: lowLatency}, "play", m.generation)
end sub

sub startVideo(url as String)
    content = CreateObject("roSGNode", "ContentNode")
    content.url = url
    content.streamFormat = "hls"
    content.title = "M3U TV"
    if m.movie
        content.playStart = 0
    else
        content.live = true
        content.playStart = -m.liveDelay
    end if
    m.video.content = content
    m.video.visible = true
    m.video.control = "play"
    m.top.setFocus(true)
end sub

sub playUrl(url as String)
    if url = "" then return
    m.top.dialog = invalid
    origin = CreateObject("roRegex", "^https?://[^/]+", "i").match(url)
    if origin.count() = 0 then return
    if m.server <> origin[0]
        m.guideGeneration++
        m.guideLoading = false
        m.server = origin[0]
        saveServer()
        refreshGuide()
    end if
    m.generation++
    token = CreateObject("roRegex", "/guide/roku/([A-Za-z0-9_-]+)/", "").match(url)
    sameSession = false
    if m.session <> invalid and token.count() > 1
        sameSession = m.session.token = token[1]
    end if
    if not sameSession then releasePlayback()
    m.video.control = "stop"
    m.movie = false
    m.liveDelay = 4
    m.playing = true
    m.grid.active = false
    m.currentChannel = invalid
    m.panel.visible = false
    m.playerStatus.visible = true
    m.playerStatus.text = "Loading live TV..."
    if token.count() > 1 then m.session = {token: token[1], kind: "live"}
    startVideo(url)
end sub

sub showGuide()
    m.generation++
    m.playing = false
    m.movie = false
    m.video.control = "stop"
    m.video.visible = false
    m.playerStatus.visible = false
    releasePlayback()
    m.panel.visible = true
    m.grid.active = true
    if m.channels.count() > 0
        now = CreateObject("roDateTime")
        renderGuide(now.asSeconds())
        m.grid.setFocus(true)
    end if
    refreshGuide()
end sub

sub keepPlaybackAlive()
    if m.session <> invalid
        if m.session.token <> "" then requestApi("/api/roku/playback/heartbeat", "POST", m.session, "heartbeat")
    end if
    if m.playing and m.video.state = "playing"
        stats = m.video.decoderStats
        counters = {}
        if stats <> invalid
            for each key in stats
                valueType = type(stats[key])
                if valueType = "Integer" or valueType = "Float" or valueType = "Double" or valueType = "LongInteger" then counters[key] = stats[key]
            end for
        end if
        print "M3U playback position="; m.video.position; " decoder="; FormatJson(counters)
    end if
end sub

sub onBufferingStatus()
    status = m.video.bufferingStatus
    if not m.playing or status = invalid then return
    if status.isUnderrun = true then print "M3U playback underrun position="; m.video.position
end sub

sub onVideoState()
    if not m.playing then return
    print "M3U playback state="; m.video.state; " position="; m.video.position; " error="; m.video.errorCode
    if m.video.state = "playing"
        m.playerStatus.text = ""
    else if m.video.state = "paused"
        m.playerStatus.text = "Movie paused | Play/Pause: resume | *: options"
    else if m.video.state = "buffering"
        m.playerStatus.text = "Buffering..."
    else if m.video.state = "error"
        m.playerStatus.text = "Playback failed. Press Back to try another channel."
    else if m.video.state = "finished"
        m.playerStatus.text = "Movie finished. Press * to return to live, or Back for the guide."
    end if
end sub

sub showOptions()
    dialog = CreateObject("roSGNode", "Dialog")
    dialog.title = "M3U TV"
    m.actions = []
    buttons = []
    if m.playing
        channel = m.currentChannel
        if m.movie and channel <> invalid
            buttons.push("Back to Live") : m.actions.push("live")
        else if channel <> invalid
            now = CreateObject("roDateTime")
            for each p in channel.programmes
                if p.start <= now.asSeconds() and p.stop > now.asSeconds() and p.restart_url <> ""
                    m.restartUrl = p.restart_url
                    buttons.push("Restart Movie") : m.actions.push("restart")
                    exit for
                end if
            end for
        end if
        buttons.push("TV Guide") : m.actions.push("guide")
    end if
    buttons.push("Refresh Guide") : m.actions.push("refresh")
    buttons.push("Change Server") : m.actions.push("server")
    buttons.push("Close") : m.actions.push("close")
    dialog.buttons = buttons
    dialog.observeField("buttonSelected", "onOptionsSelected")
    m.top.dialog = dialog
end sub

sub onOptionsSelected(event as Object)
    action = m.actions[event.getData()]
    m.top.dialog = invalid
    if action = "restart"
        playChannel(m.currentChannel, m.restartUrl)
    else if action = "live"
        playChannel(m.currentChannel)
    else if action = "guide"
        showGuide()
    else if action = "refresh"
        refreshGuide()
    else if action = "server"
        showServerDialog()
    end if
end sub

sub showServerDialog()
    dialog = CreateObject("roSGNode", "KeyboardDialog")
    dialog.title = "Connect to your TV server"
    dialog.text = m.server
    dialog.buttons = ["Connect", "Cancel"]
    dialog.observeField("buttonSelected", "onServerSelected")
    m.top.dialog = dialog
end sub

sub onServerSelected(event as Object)
    dialog = m.top.dialog
    if event.getData() = 0
        address = dialog.text.trim()
        if address = "" then return
        if left(address, 7) <> "http://" and left(address, 8) <> "https://" then address = "http://" + address
        validAddress = CreateObject("roRegex", "^https?://[^/ ]+/?$", "i")
        if not validAddress.isMatch(address) then return
        while right(address, 1) = "/"
            address = left(address, len(address) - 1)
        end while
        releasePlayback()
        m.guideGeneration++
        m.guideLoading = false
        m.server = address
        saveServer()
        m.channels = []
        showGuide()
    end if
    m.top.dialog = invalid
end sub

sub saveServer()
    m.registry.write("server", m.server)
    m.registry.flush()
end sub

function onKeyEvent(key as String, press as Boolean) as Boolean
    if not press then return false
    if key = "options"
        showOptions()
        return true
    end if
    if m.playing
        if key = "back"
            showGuide()
            return true
        else if key = "play" or key = "pause"
            if m.movie
                if m.video.state = "paused"
                    m.video.control = "resume"
                else
                    m.video.control = "pause"
                end if
            else
                m.playerStatus.text = "Live TV keeps playing. Press * for options."
            end if
            return true
        else if key = "OK"
            showOptions()
            return true
        else if key = "rewind" or key = "fastforward" or key = "replay" or key = "left" or key = "right"
            return true
        end if
    end if
    return false
end function
