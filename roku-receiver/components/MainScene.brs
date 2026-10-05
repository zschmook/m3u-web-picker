sub init()
    m.top.backgroundColor = "0x101827ff"
    m.top.backgroundURI = ""
    m.video = m.top.findNode("video")
    m.panel = m.top.findNode("guidePanel")
    m.grid = m.top.findNode("grid")
    m.tabs = m.top.findNode("tabs")
    m.tabs.labels = ["ALL"]
    m.selectedCategory = "ALL"
    m.status = m.top.findNode("status")
    m.playerStatus = m.top.findNode("playerStatus")
    m.tasks = []
    m.liveBufferTask = invalid
    m.playbackLeaseTask = invalid
    m.networkScanTask = invalid
    m.leaseDiagnostics = false
    m.channels = []
    m.guideChannels = []
    m.guideGroups = invalid
    m.session = invalid
    m.currentChannel = invalid
    m.playing = false
    m.movie = false
    m.canPause = false
    m.behindLive = false
    m.startedProgramme = invalid
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
    if config <> invalid
        if config.doesExist("lease_diagnostics") then m.leaseDiagnostics = config.lease_diagnostics = true
    end if
    initPlaybackDiagnostics(config)
    initMovieLighting(config)
    m.video.observeField("state", "onVideoState")
    m.video.observeField("bufferingStatus", "onBufferingStatus")
    m.grid.observeField("programSelected", "onProgramSelected")
    m.grid.observeField("channelInfoSelected", "onChannelSelected")
    m.grid.observeField("tabsRequested", "focusGuideTabs")
    m.tabs.observeField("gridRequested", "focusGuideGrid")
    m.tabs.observeField("selectedIndex", "onCategoryChanged")
    m.top.findNode("refreshTimer").observeField("fire", "refreshGuide")
    m.top.findNode("refreshTimer").control = "start"
    m.top.setFocus(true)
    if m.server = ""
        showServerDialog()
    else
        refreshGuide()
        refreshMovieLighting()
    end if
end sub

sub scanNetwork()
    if m.networkScanTask <> invalid then return
    task = CreateObject("roSGNode", "NetworkScanTask")
    m.networkScanTask = task
    task.observeField("result", "onNetworkScanDone")
    task.control = "run"
end sub

sub onNetworkScanDone()
    m.networkScanTask = invalid
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
            m.guideGroups = invalid
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
    else if result.purpose = "movie_lighting"
        onMovieLightingConfig(result)
    else if result.purpose = "play"
        if result.requestId <> m.generation
            if result.ok then requestApi("/api/roku/playback/stop", "POST", result.data, "stop")
            return
        end if
        if result.ok
            m.session = result.data
            startPlaybackLease()
            m.movie = result.data.kind = "movie" and result.data.is_live <> true
            m.canPause = result.data.can_pause = true or m.movie
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
    focusedPath = ""
    focusedChannel = selectedChannel()
    if focusedChannel <> invalid then focusedPath = focusedChannel.play_url
    if m.guideGroups = invalid
        m.guideGroups = {ALL: m.channels}
        for each channel in m.channels
            for each category in guideChannelCategories(channel)
                if not m.guideGroups.doesExist(category) then m.guideGroups[category] = []
                m.guideGroups[category].push(channel)
            end for
        end for
    end if
    if not m.guideGroups.doesExist(m.selectedCategory) then m.selectedCategory = "ALL"
    names = guideCategoryNames(m.guideGroups)
    selectedIndex = 0
    for i = 0 to names.count() - 1
        if names[i] = m.selectedCategory then selectedIndex = i
    end for
    m.tabs.labels = names
    m.tabs.selectedIndex = selectedIndex
    m.guideChannels = m.guideGroups[m.selectedCategory]
    focused = 0
    for i = 0 to m.guideChannels.count() - 1
        if m.guideChannels[i].play_url = focusedPath then focused = i
    end for
    root = CreateObject("roSGNode", "ContentNode")
    for each channel in m.guideChannels
        row = root.createChild("ContentNode")
        row.title = channel.number + "  " + channel.name
        logo = channel.logo
        if Left(logo, 1) = "/" then logo = m.server + logo
        row.addFields({number: channel.number, name: channel.name, groupName: channel.group, logoUrl: logo})
        for each programme in channel.programmes
            details = guideProgrammeDetails(channel, programme, serverTime)
            cell = row.createChild("ContentNode")
            cell.title = programme.title
            cell.description = details.description
            cell.playStart = programme.start
            cell.playDuration = programme.stop - programme.start
            cell.addFields({subtitle: programme.subtitle, detailsKind: details.kind, detailsFacts: details.facts})
        end for
    end for
    firstLoad = m.grid.content = invalid
    m.grid.contentStartTime = serverTime - 1800
    m.grid.content = root
    m.grid.jumpToChannel = focused
    if firstLoad
        now = CreateObject("roDateTime")
        now.fromSeconds(serverTime)
        m.grid.jumpToTime = now.toISOString()
    end if
    m.status.visible = m.guideChannels.count() = 0
    m.grid.visible = not m.status.visible
    if m.status.visible then m.status.text = "No channels in " + m.tabs.labels[m.tabs.selectedIndex] + ". Choose another category."
    if m.top.dialog = invalid and not m.tabs.hasFocus()
        if m.grid.visible then focusGuideGrid() else focusGuideTabs()
    end if
end sub

sub focusGuideTabs()
    m.tabs.focused = true
    m.tabs.setFocus(true)
end sub

sub focusGuideGrid()
    if m.guideChannels.count() = 0 then return
    m.tabs.focused = false
    m.grid.setFocus(true)
end sub

sub onCategoryChanged()
    category = m.tabs.labels[m.tabs.selectedIndex]
    if category = m.selectedCategory then return
    m.selectedCategory = category
    m.guideChannels = []
    now = CreateObject("roDateTime")
    renderGuide(now.asSeconds())
end sub

function selectedChannel() as Dynamic
    index = m.grid.channelFocused
    if index < 0 or index >= m.guideChannels.count() then return invalid
    return m.guideChannels[index]
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
    if index >= 0 and index < m.guideChannels.count() then playChannel(m.guideChannels[index])
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
    setMovieLighting(false)
    cancelLiveBuffer()
    if m.playbackLeaseTask <> invalid
        m.playbackLeaseTask.control = "stop"
        m.playbackLeaseTask = invalid
    end if
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
    m.canPause = false
    m.behindLive = false
    m.startedProgramme = invalid
    now = CreateObject("roDateTime")
    for each programme in channel.programmes
        if programme.start <= now.asSeconds() and programme.stop > now.asSeconds()
            m.startedProgramme = programme
            exit for
        end if
    end for
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
    cancelLiveBuffer()
    ' Keep dedicated sports feeds at their existing low latency. Normal live
    ' channels request up to forty-five seconds, limited by available media;
    ' the server permits read-ahead to grow after this short startup.
    if not m.movie and not m.canPause
        if m.liveDelay >= 30 then m.liveDelay = 45
        m.playerStatus.visible = true
        m.playerStatus.text = "Building playback buffer..."
        task = CreateObject("roSGNode", "LiveBufferTask")
        task.url = url
        ' Start once a few completed segments exist. Available read-ahead can
        ' grow after playback begins; never wait for the full target at startup.
        task.seconds = 4
        task.requestId = m.generation
        task.observeField("result", "onLiveBufferReady")
        m.liveBufferTask = task
        task.control = "run"
        return
    end if
    beginVideo(url)
end sub

sub cancelLiveBuffer()
    if m.liveBufferTask <> invalid
        m.liveBufferTask.unobserveField("result")
        m.liveBufferTask.control = "stop"
        m.liveBufferTask = invalid
    end if
end sub

sub onLiveBufferReady(event as Object)
    task = event.getRoSGNode()
    result = event.getData()
    if result.requestId <> m.generation or not m.playing then return
    if m.liveBufferTask = invalid then return
    if not m.liveBufferTask.isSameNode(task) then return
    m.liveBufferTask = invalid
    if result.ok
        availableDelay = result.seconds - 2
        if availableDelay < 4 then availableDelay = 4
        if m.liveDelay > availableDelay then m.liveDelay = availableDelay
        print "M3U live cushion seconds="; m.liveDelay; " available="; result.seconds
        beginVideo(result.playbackUrl)
    else
        m.playerStatus.text = "Stream could not build a playback buffer. Press Back for the guide."
    end if
end sub

sub beginVideo(url as String)
    resetPlaybackDiagnostics()
    content = CreateObject("roSGNode", "ContentNode")
    content.url = url
    content.streamFormat = "hls"
    content.title = "M3U TV"
    if m.movie or m.canPause
        content.playStart = 0
        if not m.movie then content.live = true
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
    m.canPause = false
    m.behindLive = false
    m.startedProgramme = invalid
    m.liveDelay = 4
    m.playing = true
    delay = CreateObject("roRegex", "[?&]live_delay_seconds=(4|30)(&|$)", "").match(url)
    if delay.count() > 1 then m.liveDelay = delay[1].toInt()
    m.grid.active = false
    m.currentChannel = invalid
    m.panel.visible = false
    m.playerStatus.visible = true
    m.playerStatus.text = "Loading live TV..."
    if token.count() > 1
        m.session = {token: token[1], kind: "live"}
        lease = CreateObject("roRegex", "[?&]lease=([A-Za-z0-9_-]{24})(&|$)", "").match(url)
        if lease.count() > 1 then m.session.lease = lease[1]
    end if
    startPlaybackLease()
    startVideo(url)
end sub

sub showGuide()
    cancelLiveBuffer()
    m.generation++
    m.playing = false
    m.movie = false
    m.canPause = false
    m.behindLive = false
    m.startedProgramme = invalid
    m.video.control = "stop"
    m.video.visible = false
    m.playerStatus.visible = false
    releasePlayback()
    m.panel.visible = true
    m.grid.active = true
    if m.channels.count() > 0
        now = CreateObject("roDateTime")
        renderGuide(now.asSeconds())
    end if
    refreshGuide()
end sub

sub startPlaybackLease()
    if m.playbackLeaseTask <> invalid
        m.playbackLeaseTask.control = "stop"
        m.playbackLeaseTask = invalid
    end if
    if m.session = invalid or m.server = "" then return
    if m.session.token = "" then return
    ' One worker owns the lease for this session. No recurring render-thread
    ' timer, decoder-stat reads, or task creation while the video is playing.
    task = CreateObject("roSGNode", "PlaybackLeaseTask")
    task.url = m.server + "/api/roku/playback/heartbeat"
    body = {token: m.session.token, kind: m.session.kind}
    if m.session.lease <> invalid then body.lease = m.session.lease
    task.body = FormatJson(body)
    task.diagnostics = m.leaseDiagnostics
    m.playbackLeaseTask = task
    task.control = "run"
end sub

sub onBufferingStatus()
    if m.playing then playbackDiagnostic("buffer")
    status = m.video.bufferingStatus
    if not m.playing or status = invalid then return
    if status.isUnderrun = true then print "M3U playback underrun position="; m.video.position
end sub

sub onVideoState()
    if not m.playing then return
    playbackDiagnostic("state")
    print "M3U playback state="; m.video.state; " position="; m.video.position; " error="; m.video.errorCode
    if m.video.state = "playing"
        m.playerStatus.text = ""
        if movieLightingPlayback() then setMovieLighting(true)
    else if m.video.state = "paused"
        m.playerStatus.text = "Paused"
        if movieLightingPlayback() then setMovieLightMode("pause")
    else if m.video.state = "buffering"
        m.playerStatus.text = "Buffering..."
    else if m.video.state = "error"
        setMovieLighting(false)
        m.playerStatus.text = "Playback failed. Press Back to try another channel."
    else if m.video.state = "finished"
        setMovieLighting(false)
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
        if (m.movie or m.behindLive) and channel <> invalid
            buttons.push("Back to Live") : m.actions.push("live")
        end if
        if not m.movie and channel <> invalid
            if m.canPause and m.startedProgramme <> invalid
                if m.startedProgramme.restart_url <> ""
                    m.restartUrl = m.startedProgramme.restart_url
                    buttons.push("Restart Movie") : m.actions.push("restart")
                end if
            else
                now = CreateObject("roDateTime")
                for each p in channel.programmes
                    if p.start <= now.asSeconds() and p.stop > now.asSeconds() and p.restart_url <> ""
                        m.restartUrl = p.restart_url
                        buttons.push("Restart Movie") : m.actions.push("restart")
                        exit for
                    end if
                end for
            end if
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
        m.guideChannels = []
        m.guideGroups = invalid
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
            if m.canPause
                if m.video.state = "paused"
                    m.video.control = "resume"
                else
                    if not m.movie then m.behindLive = true
                    m.video.control = "pause"
                end if
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
