sub init()
    m.canvas = m.top.findNode("mapCanvas")
    m.bubbles = m.top.findNode("bubbleCanvas")
    m.details = m.top.findNode("detailCanvas")
    m.toolbar = m.top.findNode("toolbar")
    m.menu = m.top.findNode("menuCanvas")
    m.summary = m.top.findNode("summary")
    m.hint = m.top.findNode("hint")
    m.fonts = {}
    m.data = invalid
    m.dataRevision = 0
    m.tileLayoutKey = invalid
    m.drawnRegionId = ""
    m.tileFills = {}
    m.bubbleKey = ""
    m.toolbarKey = ""
    m.regionId = "PA"
    m.mode = "map"
    m.zone = "content"
    m.league = "All sports"
    m.leagues = ["All sports"]
    m.weighted = false
    m.toolbarIndex = 0
    m.menuIndex = 0
    m.gameIndex = 0
    m.feedIndex = 0
    m.regions = []
    m.nodes = []
    m.layoutTask = invalid
    m.layoutGeneration = 0
    m.layoutKey = ""
end sub

sub resetMap()
    m.data = invalid
    m.top.data = invalid
    m.tileLayoutKey = invalid
    sportsClear(m.bubbles)
    m.bubbleKey = ""
    m.toolbarKey = ""
    m.regionId = "PA"
    m.mode = "map"
    m.zone = "content"
    m.league = "All sports"
    m.regions = []
    m.nodes = []
    m.leagues = ["All sports"]
    m.layoutGeneration++
    m.layoutKey = ""
    if m.layoutTask <> invalid then m.layoutTask.control = "stop"
    m.layoutTask = invalid
    drawSportsMap()
end sub

sub activate()
    m.top.setFocus(true)
    if m.nodes.count() = 0 and m.regions.count() > 0 then requestSportsLayout() else drawSportsMap()
end sub

sub updateSportsData()
    ' A SceneGraph AA field is deep-copied on every read. Take one snapshot
    ' here; all navigation and drawing must use this ordinary BrightScript AA.
    data = m.top.data
    if data = invalid then return
    m.data = data
    m.dataRevision++
    oldGame = selectedSportsGame()
    oldMenuLeague = m.leagues[m.menuIndex]
    oldFeed = invalid
    if oldGame <> invalid and m.feedIndex < oldGame.feeds.count() then oldFeed = oldGame.feeds[m.feedIndex].id
    m.leagues = sportsMapLeagues(m.data)
    found = false
    for each league in m.leagues
        if league = m.league then found = true
    end for
    if not found then m.league = "All sports"
    m.regions = sportsMapRegions(m.data, m.league)
    region = sportsMapRegion(m.regions, m.regionId)
    if region = invalid and m.regions.count() > 0
        m.regionId = m.regions[0].id
        region = m.regions[0]
    end if
    m.gameIndex = 0 : m.feedIndex = 0
    gameSurvived = false
    if oldGame <> invalid and region <> invalid
        for i = 0 to region.games.count() - 1
            if region.games[i].id = oldGame.id
                m.gameIndex = i
                gameSurvived = true
            end if
        end for
    end if
    if m.mode = "feeds" and not gameSurvived then m.mode = "games"
    game = selectedSportsGame()
    if game <> invalid and oldFeed <> invalid
        for i = 0 to game.feeds.count() - 1
            if game.feeds[i].id = oldFeed then m.feedIndex = i
        end for
    end if
    if region <> invalid
        if region.games.count() = 0 then m.mode = "map"
    end if
    m.menuIndex = 0
    for i = 0 to m.leagues.count() - 1
        if m.leagues[i] = oldMenuLeague then m.menuIndex = i
    end for
    requestSportsLayout()
end sub

sub requestSportsLayout()
    key = "uniform"
    if m.weighted then key = "weighted"
    for each region in m.regions
        key += "|" + region.id + ":" + region.games.count().toStr()
    end for
    if key = m.layoutKey and m.nodes.count() > 0
        drawSportsMap()
        return
    end if
    m.pendingLayoutKey = key
    m.layoutGeneration++
    if m.layoutTask <> invalid
        m.layoutTask.unobserveField("result")
        m.layoutTask.control = "stop"
    end if
    task = CreateObject("roSGNode", "SportsMapLayoutTask")
    ' Layout needs only 66 counts, not thousands of games copied to a Task.
    layoutRegions = []
    for each region in m.regions
        layoutRegions.push({id: region.id, row: region.row, col: region.col, gameCount: region.games.count()})
    end for
    task.regions = layoutRegions
    task.weighted = m.weighted
    task.requestId = m.layoutGeneration
    task.observeField("result", "onSportsLayout")
    m.layoutTask = task
    task.control = "run"
end sub

sub onSportsLayout(event as Object)
    result = event.getData()
    if result.requestId <> m.layoutGeneration then return
    m.layoutTask = invalid
    m.nodes = result.nodes
    m.layoutKey = m.pendingLayoutKey
    if m.top.visible then drawSportsMap()
end sub

function sportsFont(size as Integer, bold = false as Boolean) as Object
    key = size.toStr()
    if bold then key += "bold"
    if not m.fonts.doesExist(key)
        holder = CreateObject("roSGNode", "Label")
        if bold then holder.font = "font:SmallBoldSystemFont" else holder.font = "font:SmallSystemFont"
        font = holder.font
        font.size = size
        m.fonts[key] = font
    end if
    return m.fonts[key]
end function

sub sportsClear(group as Object)
    if group.getChildCount() > 0 then group.removeChildrenIndex(group.getChildCount(), 0)
end sub

function sportsLabel(group as Object, text as String, x as Float, y as Float, width as Float, height as Float, size = 24 as Integer, color = "0xe9eef7ff" as String, lines = 1 as Integer) as Object
    label = group.createChild("Label")
    label.translation = [x, y] : label.width = width : label.height = height
    label.text = text : label.color = color : label.font = sportsFont(size)
    label.wrap = lines > 1 : label.maxLines = lines
    label.vertAlign = "center"
    return label
end function

sub sportsRect(group as Object, x as Float, y as Float, width as Float, height as Float, color as String)
    rect = group.createChild("Rectangle")
    rect.translation = [x, y] : rect.width = width : rect.height = height : rect.color = color
end sub

function selectedSportsGame() as Dynamic
    region = sportsMapRegion(m.regions, m.regionId)
    if region = invalid then return invalid
    if m.gameIndex < 0 or m.gameIndex >= region.games.count() then return invalid
    return region.games[m.gameIndex]
end function

sub drawSportsMap()
    if m.canvas = invalid then return
    sportsClear(m.details) : sportsClear(m.menu)
    m.summary.text = m.top.status
    if m.data <> invalid
        m.summary.text = m.data.totalGames.toStr() + " events Â· " + m.data.totalChannels.toStr() + " generated channels Â· US + Canada"
        if m.data.metadataLoading then m.summary.text += " Â· Updating locations..."
        if m.data.metadataStale then m.summary.text += " Â· Using saved venue details"
        if m.data.cacheLoading = true then m.summary.text += " Â· Refreshing cached map..."
        if m.data.cacheRefreshFailed = true then m.summary.text += " Â· Refresh failed; retrying"
        if m.top.status <> "" then m.summary.text += " Â· " + m.top.status
    end if
    drawSportsToolbar()
    hub = drawSportsTiles()
    region = sportsMapRegion(m.regions, m.regionId)
    if region <> invalid
        sportsLabel(m.details, region.name, 0, 0, 500, 96, 36, "0xffffffff", 2)
        sportsLabel(m.details, region.games.count().toStr() + " events Â· " + m.league, 0, 100, 500, 60, 24, "0xaacbffff", 2)
    end if
    if m.mode = "map"
        if m.bubbleKey <> "" then sportsClear(m.bubbles)
        m.bubbleKey = ""
        m.canvas.opacity = 1.0
        sportsLabel(m.details, "Pick a state or province. Open a game, then choose its generated feed.", 0, 190, 500, 180, 28, "0xc5d1e3ff", 5)
        if region <> invalid
            if region.games.count() = 0 then sportsLabel(m.details, "No generated games in this region for the selected sport.", 0, 400, 500, 120, 25, "0x9eafc8ff", 4)
        end if
        m.hint.text = "Arrows: states Â· OK: open Â· Up at top: filters Â· Replay / Fast forward: regions with games Â· Back: tabs"
    else
        m.canvas.opacity = 0.35
        drawSportsBubbles(hub)
        game = selectedSportsGame()
        if game <> invalid
            sportsLabel(m.details, game.title, 0, 180, 500, 150, 30, "0xffffffff", 4)
            sportsLabel(m.details, game.league + " Â· " + game.statusLabel, 0, 340, 500, 44, 24, "0x8ee3c1ff")
            sportsLabel(m.details, game.score + "   " + game.clock, 0, 385, 500, 60, 28, "0xffffffff", 2)
            sportsLabel(m.details, game.venue, 0, 460, 500, 100, 23, "0xaabbd4ff", 3)
            sportsLabel(m.details, game.feeds.count().toStr() + " generated feeds", 0, 580, 500, 44, 24)
        end if
        m.hint.text = "Left / Right: all game bubbles, wraps at ends Â· OK: feeds Â· Up: filters Â· Back: unselect region"
        if m.mode = "feeds"
            drawSportsFeeds()
            m.hint.text = "Arrows: feeds Â· OK: play Â· Back: game bubbles"
        end if
    end if
    if m.zone = "leagues" then drawSportsLeagueMenu()
end sub

sub drawSportsToolbar()
    key = m.zone + "|" + m.toolbarIndex.toStr() + "|" + m.league + "|" + m.weighted.toStr()
    if key = m.toolbarKey then return
    m.toolbarKey = key
    sportsClear(m.toolbar)
    toolbarLabels = [m.league + "  v", "Size by games: Off"]
    if m.weighted then toolbarLabels[1] = "Size by games: On"
    for i = 0 to 1
        x = i * 420 : width = 400
        if i = 1 then width = 340
        color = "0x223248ff"
        if m.zone <> "content" and i = m.toolbarIndex then color = "0x78b8ffff"
        sportsRect(m.toolbar, x, 0, width, 50, color)
        sportsRect(m.toolbar, x + 3, 3, width - 6, 44, "0x1d2d43ff")
        sportsLabel(m.toolbar, toolbarLabels[i], x + 15, 0, width - 30, 50, 23)
    end for
end sub

function drawSportsTiles() as Object
    if m.tileLayoutKey <> m.layoutKey
        sportsClear(m.canvas)
        m.tileLayoutKey = m.layoutKey
        m.tileFills = {}
        m.drawnRegionId = ""
        m.mapHighlight = m.canvas.createChild("Rectangle")
        m.mapHighlight.color = "0xb1d6ffff"
        m.mapHighlight.visible = false
        for each node in m.nodes
            region = sportsMapRegion(m.regions, node.id)
            if region <> invalid
                color = "0x2a374aff"
                if region.games.count() > 0 then color = "0x285e8fff"
                fill = m.canvas.createChild("Rectangle")
                fill.translation = [node.x - node.side / 2, node.y - node.side / 2]
                fill.width = node.side : fill.height = node.side : fill.color = color
                m.tileFills[node.id] = {fill: fill, baseColor: color}
                text = node.id
                if text = "??" then text = "?"
                label = sportsLabel(m.canvas, text, node.x - node.side / 2, node.y - 17, node.side, 34, 20)
                label.horizAlign = "center"
            end if
        end for
    end if
    hub = {x: 610, y: 340}
    for each node in m.nodes
        if node.id = m.regionId then hub = node
    end for
    if m.drawnRegionId <> m.regionId
        if m.tileFills.doesExist(m.drawnRegionId)
            old = m.tileFills[m.drawnRegionId]
            old.fill.color = old.baseColor
        end if
        if m.tileFills.doesExist(m.regionId)
            m.tileFills[m.regionId].fill.color = "0x367bb8ff"
            m.mapHighlight.translation = [hub.x - hub.side / 2 - 4, hub.y - hub.side / 2 - 4]
            m.mapHighlight.width = hub.side + 8 : m.mapHighlight.height = hub.side + 8
            m.mapHighlight.visible = true
        end if
        m.drawnRegionId = m.regionId
    end if
    return hub
end function

sub drawSportsBubbles(hub as Object)
    region = sportsMapRegion(m.regions, m.regionId)
    if region = invalid or region.games.count() = 0 then return
    page = int(m.gameIndex / 6)
    count = region.games.count() - page * 6
    if count > 6 then count = 6
    key = m.dataRevision.toStr() + "|" + m.regionId + "|" + m.league + "|" + page.toStr() + "|" + m.layoutKey
    if key <> m.bubbleKey
        m.bubbleKey = key
        sportsClear(m.bubbles)
        m.bubblePosters = []
        points = sportsMapBubblePositions(hub, count)
        for i = 0 to points.count() - 1
            gameIndex = page * 6 + i
            game = region.games[gameIndex]
            point = points[i]
            bubble = m.bubbles.createChild("Group")
            bubble.translation = [point.x - 108, point.y - 108]
            poster = bubble.createChild("Poster")
            poster.uri = "pkg:/images/sports-bubble.png"
            poster.width = 216 : poster.height = 216
            m.bubblePosters.push({index: gameIndex, poster: poster})
            label = sportsLabel(bubble, game.league, 28, 28, 160, 28, 18, "0xaacbffff")
            label.horizAlign = "center"
            label = sportsLabel(bubble, game.name, 24, 62, 168, 62, 23, "0xffffffff", 2)
            label.horizAlign = "center"
            text = game.statusLabel
            if game.score <> "" then text += " " + game.score
            label = sportsLabel(bubble, text, 18, 132, 180, 30, 18, "0x8ee3c1ff")
            label.horizAlign = "center"
            label = sportsLabel(bubble, game.clock, 28, 164, 160, 26, 17, "0xcbd5e1ff")
            label.horizAlign = "center"
        end for
        m.bubbleCaption = sportsLabel(m.bubbles, "", 500, 642, 220, 34, 22)
    end if
    for each item in m.bubblePosters
        uri = "pkg:/images/sports-bubble.png"
        if item.index = m.gameIndex and m.zone = "content" then uri = "pkg:/images/sports-bubble-focused.png"
        if item.poster.uri <> uri then item.poster.uri = uri
    end for
    m.bubbleCaption.text = (m.gameIndex + 1).toStr() + " / " + region.games.count().toStr()
end sub

sub drawSportsFeeds()
    game = selectedSportsGame()
    if game = invalid then return
    sportsClear(m.details)
    sportsLabel(m.details, "Choose a feed", 0, 0, 500, 50, 32)
    sportsLabel(m.details, game.title, 0, 60, 500, 100, 25, "0xaacbffff", 3)
    first = int(m.feedIndex / 7) * 7
    for i = first to first + 6
        if i < game.feeds.count()
            feed = game.feeds[i]
            y = 185 + (i - first) * 60
            color = "0x223248ff"
            if i = m.feedIndex then color = "0x376e9fff"
            sportsRect(m.details, 0, y, 500, 54, color)
            text = "Ch " + feed.number.toStr() + " Â· " + feed.name
            if feed.available = false then text += " (offline)"
            sportsLabel(m.details, text, 14, y, 472, 54, 23)
        end if
    end for
    sportsLabel(m.details, (m.feedIndex + 1).toStr() + " / " + game.feeds.count().toStr(), 0, 620, 500, 40, 22, "0xaabbd4ff")
end sub

sub drawSportsLeagueMenu()
    first = int(m.menuIndex / 9) * 9
    sportsRect(m.menu, 0, 0, 780, 540, "0x0a1220ff")
    for i = first to first + 8
        if i < m.leagues.count()
            y = 12 + (i - first) * 56
            if i = m.menuIndex then sportsRect(m.menu, 10, y, 760, 52, "0x376e9fff")
            sportsLabel(m.menu, m.leagues[i], 26, y, 720, 52, 26)
        end if
    end for
    m.hint.text = "Up / Down: choose league Â· OK: apply Â· Back: cancel"
end sub

function onKeyEvent(key as String, press as Boolean) as Boolean
    if not press then return false
    if key = "options" then return false
    if m.zone = "leagues"
        if key = "up" or key = "down"
            delta = 1
            if key = "up" then delta = -1
            m.menuIndex = sportsMapWrap(m.menuIndex, m.leagues.count(), delta)
        else if key = "OK"
            m.league = m.leagues[m.menuIndex]
            m.zone = "toolbar"
            m.mode = "map"
            m.gameIndex = 0 : m.feedIndex = 0
            m.regions = sportsMapRegions(m.data, m.league)
            requestSportsLayout()
        else if key = "back"
            m.zone = "toolbar"
        end if
        drawSportsMap()
        return true
    end if
    if m.zone = "toolbar"
        if key = "left" or key = "right"
            m.toolbarIndex = 1 - m.toolbarIndex
        else if key = "OK"
            if m.toolbarIndex = 0
                m.zone = "leagues"
                for i = 0 to m.leagues.count() - 1
                    if m.leagues[i] = m.league then m.menuIndex = i
                end for
            else
                m.weighted = not m.weighted
                requestSportsLayout()
            end if
        else if key = "down" or key = "back"
            m.zone = "content"
        else if key = "up"
            m.top.tabsRequested = true
        end if
        drawSportsMap()
        return true
    end if
    if key = "back"
        if m.mode = "feeds"
            m.mode = "games"
        else if m.mode = "games"
            m.mode = "map"
        else
            m.top.tabsRequested = true
        end if
    else if m.mode = "feeds"
        game = selectedSportsGame()
        if game <> invalid
            if key = "up" or key = "left" or key = "down" or key = "right"
                delta = 1
                if key = "up" or key = "left" then delta = -1
                m.feedIndex = sportsMapWrap(m.feedIndex, game.feeds.count(), delta)
            else if key = "OK" and game.feeds.count() > 0
                m.top.feedSelected = game.feeds[m.feedIndex]
            end if
        end if
    else if m.mode = "games"
        if key = "left" or key = "right"
            region = sportsMapRegion(m.regions, m.regionId)
            delta = 1
            if key = "left" then delta = -1
            m.gameIndex = sportsMapWrap(m.gameIndex, region.games.count(), delta)
        else if key = "up"
            m.zone = "toolbar"
        else if key = "OK"
            m.mode = "feeds" : m.feedIndex = 0
        end if
    else if key = "up" or key = "down" or key = "left" or key = "right"
        nextRegion = sportsMapNavigate(m.regions, m.regionId, key)
        if key = "up" and nextRegion = m.regionId then m.zone = "toolbar"
        m.regionId = nextRegion
    else if key = "OK"
        region = sportsMapRegion(m.regions, m.regionId)
        if region <> invalid
            if region.games.count() > 0 then m.mode = "games" : m.gameIndex = 0
        end if
    else if key = "replay" or key = "rewind" or key = "fastforward" or key = "channelup" or key = "channeldown"
        delta = 1
        if key = "replay" or key = "rewind" or key = "channeldown" then delta = -1
        m.regionId = sportsMapNextActive(m.regions, m.regionId, delta)
    else
        return false
    end if
    drawSportsMap()
    return true
end function
