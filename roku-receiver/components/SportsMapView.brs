' Scene integration stays separate from the guide and from the map component.
sub initSportsMapView()
    m.activeView = "guide"
    m.returnToSportsMap = false
    m.mapLoading = false
    m.mapHasData = false
    m.mapGeneration = 0
    m.appHeader = m.top.findNode("appHeader")
    m.viewTabs = m.top.findNode("viewTabs")
    m.viewTabs.labels = ["TV GUIDE", "SPORTS MAP"]
    m.sportsMap = m.top.findNode("sportsMap")
    m.viewTabs.observeField("selectedIndex", "onViewChanged")
    m.viewTabs.observeField("gridRequested", "focusActiveView")
    m.tabs.observeField("topRequested", "focusViewTabs")
    m.sportsMap.observeField("tabsRequested", "focusViewTabs")
    m.sportsMap.observeField("feedSelected", "onSportsFeedSelected")
    timer = m.top.findNode("sportsMapTimer")
    timer.observeField("fire", "refreshSportsMap")
    timer.control = "start"
end sub

sub onViewChanged()
    if m.playing then return
    if m.viewTabs.selectedIndex = 1
        m.activeView = "map"
        m.panel.visible = false
        m.grid.active = false
        m.sportsMap.visible = true
        refreshSportsMap()
    else
        m.activeView = "guide"
        m.sportsMap.visible = false
        m.panel.visible = true
        m.grid.active = true
        if m.channels.count() > 0
            now = CreateObject("roDateTime")
            renderGuide(now.asSeconds())
        end if
        refreshGuide()
    end if
end sub

sub focusViewTabs()
    m.tabs.focused = false
    m.viewTabs.focused = true
    m.viewTabs.setFocus(true)
end sub

sub focusActiveView()
    m.viewTabs.focused = false
    if m.activeView = "map"
        m.sportsMap.callFunc("activate")
    else
        focusGuideTabs()
    end if
end sub

sub refreshSportsMap()
    if m.server = "" or m.mapLoading or m.playing or m.activeView <> "map" then return
    m.mapLoading = true
    if not m.mapHasData then m.sportsMap.status = "Loading generated sports channels..."
    requestApi("/api/roku/sports-map", "GET", {}, "sports_map", m.mapGeneration)
end sub

sub onSportsMapResult(result as Object)
    if result.requestId <> m.mapGeneration then return
    m.mapLoading = false
    if m.playing or m.activeView <> "map" then return
    if result.ok and result.data.states <> invalid
        m.sportsMap.status = ""
        m.mapHasData = true
        m.sportsMap.data = result.data
    else
        m.sportsMap.status = "Sports map unavailable. It needs a Picker server with sports-map support. Retrying..."
    end if
end sub

sub onSportsFeedSelected(event as Object)
    feed = event.getData()
    if feed = invalid or m.activeView <> "map" or m.playing then return
    if feed.generated_id = invalid then return
    if feed.available = false
        m.sportsMap.status = "This feed is marked offline. Choose another feed; the list refreshes automatically."
        return
    end if
    channel = {name: feed.name, number: feed.number.toStr(), play_url: feed.play_url, is_sports: true, is_movie: false, programmes: feed.programmes, generated_id: feed.generated_id}
    playChannel(channel)
end sub

sub resetSportsMapServer()
    m.mapGeneration++
    m.mapLoading = false
    m.mapHasData = false
    m.sportsMap.callFunc("resetMap")
end sub
