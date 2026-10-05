' Match the browser guide while drawing only the visible channel rows.
sub init()
    m.canvas = m.top.findNode("canvas")
    m.width = 1760
    m.stationWidth = 360
    m.headerHeight = 46
    m.rowHeight = 88
    m.firstRow = 0
    m.channel = 0
    m.programme = 0
    m.channelFocus = false
    m.windowStart = 0
    m.focusTime = 0
    m.titleFont = makeFont(23)
    m.timeFont = makeFont(19)
    m.smallFont = makeFont(17)
    m.clock = m.top.findNode("clock")
    m.clock.observeField("fire", "drawGuide")
    m.keyRepeat = m.top.findNode("keyRepeat")
    m.keyRepeat.observeField("fire", "repeatNavigation")
    m.heldKey = ""
    m.holdTime = CreateObject("roTimespan")
    onActiveChanged()
end sub

sub onActiveChanged()
    if m.clock = invalid then return
    if m.top.active
        m.clock.control = "start"
        drawGuide()
    else
        m.clock.control = "stop"
        stopNavigationRepeat()
    end if
end sub

sub stopNavigationRepeat()
    m.heldKey = ""
    if m.keyRepeat <> invalid then m.keyRepeat.control = "stop"
end sub

sub repeatNavigation()
    if not m.top.active or not m.top.hasFocus() or m.heldKey = ""
        stopNavigationRepeat()
        return
    end if
    if m.holdTime.totalMilliseconds() < 350 then return
    moveChannel(m.heldKey, true)
end sub

sub moveChannel(key as String, repeated = false as Boolean)
    if m.top.content = invalid then return
    count = m.top.content.getChildCount()
    if count = 0 then return
    if key = "up" and m.channel = 0
        stopNavigationRepeat()
        if not repeated then m.top.tabsRequested = true
        return
    end if
    amount = 1
    if key = "rewind" or key = "fastforward" then amount = visibleChannelRows()
    if key = "up" or key = "rewind" then amount = -amount
    previous = m.channel
    m.channel += amount
    if m.channel < 0 then m.channel = 0
    if m.channel >= count then m.channel = count - 1
    if m.channel = previous
        stopNavigationRepeat()
        return
    end if
    chooseProgramme()
    drawGuide()
end sub

function makeFont(size as Integer) as Object
    ' Resolve a system face before resizing it. A Font with an empty URI does
    ' not render glyphs on Roku, even when its size has been configured.
    holder = CreateObject("roSGNode", "Label")
    holder.font = "font:SmallSystemFont"
    if size >= 23 then holder.font = "font:SmallBoldSystemFont"
    font = holder.font
    font.size = size
    return font
end function

function visibleChannelRows() as Integer
    rows = m.top.numRows - 2
    if rows < 1 then rows = 1
    return rows
end function

sub drawProgrammeDetails(row as Object, y as Float, height as Float, currentTime as Integer)
    drawBox(m.canvas, 0, y, m.width, height, "0x0b1628ff")
    drawBox(m.canvas, 0, y, m.width, 2, "0x475569ff")
    drawBox(m.canvas, 0, y + height - 2, m.width, 2, "0x475569ff")
    drawBox(m.canvas, m.width - 2, y, 2, height, "0x475569ff")
    drawBox(m.canvas, 0, y, 4, height, "0x93c5fdff")
    drawBox(m.canvas, m.stationWidth, y, 1, height, "0x334155ff")
    if row.logoUrl <> ""
        poster = m.canvas.createChild("Poster")
        poster.translation = [24, y + 16]
        poster.width = 112
        poster.height = 112
        poster.loadWidth = 112
        poster.loadHeight = 112
        poster.loadDisplayMode = "scaleToFit"
        poster.uri = row.logoUrl
    else
        initial = label(m.canvas, Left(row.name, 1), 24, y + 16, 112, 112, m.titleFont)
        initial.horizAlign = "center"
    end if
    channelName = label(m.canvas, row.name, 152, y + 18, m.stationWidth - 168, 72, m.timeFont)
    channelName.wrap = true
    channelName.maxLines = 3
    channelName.vertAlign = "top"
    channelName.ellipsizeOnBoundary = true
    label(m.canvas, row.groupName, 152, y + 94, m.stationWidth - 168, 44, m.smallFont, "0x94a3b8ff")
    programme = row.getChild(m.programme)
    if programme = invalid then return
    x = m.stationWidth + 20
    width = m.width - x - 20
    title = label(m.canvas, programme.title, x, y + 10, width, 30, m.titleFont, "0xf8fafcff")
    kind = programme.detailsKind
    durationMinutes = Int((programme.playDuration + 30) / 60)
    facts = timeText(programme.playStart) + "–" + timeText(programme.playStart + programme.playDuration)
    if kind <> "sports" then facts += " • " + durationMinutes.toStr() + " min"
    if programme.detailsFacts <> invalid and programme.detailsFacts <> ""
        facts += " • " + programme.detailsFacts
    else if programme.subtitle <> invalid and programme.subtitle <> "" and kind = invalid
        facts += " • " + programme.subtitle
    end if
    if kind = "sports" then facts = "SPORTS • " + facts
    if kind = "movie" then facts = "MOVIE • " + facts
    if kind = "tv" then facts = "TV EPISODE • " + facts
    if programme.playStart <= currentTime and programme.playStart + programme.playDuration > currentTime
        facts = "ON NOW • " + facts
    else if programme.playStart > currentTime
        facts = "UPCOMING • " + facts
    end if
    label(m.canvas, facts, x, y + 45, width, 24, m.timeFont, "0x93c5fdff")
    description = ""
    if programme.description <> invalid then description = programme.description.trim()
    if description = "" then description = "No description available."
    synopsis = label(m.canvas, description, x, y + 78, width, height - 92, m.timeFont, "0xcbd5e1ff")
    synopsis.wrap = true
    synopsis.maxLines = 3
    synopsis.vertAlign = "top"
    synopsis.ellipsizeOnBoundary = true
end sub

sub onContentChanged()
    if m.top.content = invalid then return
    if m.top.content.getChildCount() = 0 then return
    if m.channel >= m.top.content.getChildCount() then m.channel = 0
    if m.windowStart = 0 then m.windowStart = Int(m.top.contentStartTime / 1800) * 1800
    now = CreateObject("roDateTime")
    if m.focusTime = 0 then m.focusTime = now.asSeconds()
    chooseProgramme()
    drawGuide()
end sub

sub onJumpChannel()
    if m.top.content = invalid then return
    count = m.top.content.getChildCount()
    if count = 0 then return
    m.channel = m.top.jumpToChannel
    if m.channel < 0 or m.channel >= count then m.channel = 0
    chooseProgramme()
    drawGuide()
end sub

sub onJumpTime()
    date = CreateObject("roDateTime")
    date.fromISO8601String(m.top.jumpToTime)
    m.focusTime = date.asSeconds()
    m.windowStart = Int(m.focusTime / 1800) * 1800
    chooseProgramme()
    drawGuide()
end sub

function rowContent() as Dynamic
    if m.top.content = invalid then return invalid
    return m.top.content.getChild(m.channel)
end function

sub chooseProgramme()
    row = rowContent()
    if row = invalid then return
    m.programme = 0
    for i = 0 to row.getChildCount() - 1
        programme = row.getChild(i)
        if programme.playStart <= m.focusTime
            m.programme = i
            if programme.playStart + programme.playDuration > m.focusTime then exit for
        end if
    end for
end sub

sub moveTime(key as String)
    now = CreateObject("roDateTime")
    currentTime = now.asSeconds()
    currentWindow = Int(currentTime / 1800) * 1800
    if m.windowStart < currentWindow then m.windowStart = currentWindow
    if key = "left"
        if m.windowStart <= currentWindow then return
        m.windowStart -= 1800
    else
        m.windowStart += 1800
    end if
    m.focusTime = m.windowStart
    if m.focusTime < currentTime then m.focusTime = currentTime
    m.channelFocus = false
    chooseProgramme()
end sub

function drawBox(parent as Object, x as Float, y as Float, width as Float, height as Float, color as String) as Object
    node = parent.createChild("Rectangle")
    node.translation = [x, y]
    node.width = width
    node.height = height
    node.color = color
    return node
end function

function label(parent as Object, text as String, x as Float, y as Float, width as Float, height as Float, font as Object, color = "0xe5e7ebff" as String) as Object
    node = parent.createChild("Label")
    node.text = text
    node.translation = [x, y]
    node.width = width
    node.height = height
    node.font = font
    node.color = color
    node.wrap = false
    node.vertAlign = "center"
    return node
end function

function timeText(seconds as Integer) as String
    date = CreateObject("roDateTime")
    date.fromSeconds(seconds)
    date.toLocalTime()
    hour = date.getHours()
    suffix = " AM"
    if hour >= 12 then suffix = " PM"
    hour = hour MOD 12
    if hour = 0 then hour = 12
    return hour.toStr() + ":" + Right("0" + date.getMinutes().toStr(), 2) + suffix
end function

sub drawGuide()
    if not m.top.active then return
    if m.top.content = invalid then return
    count = m.top.content.getChildCount()
    if count = 0 then return
    rows = visibleChannelRows()
    if m.channel < m.firstRow then m.firstRow = m.channel
    if m.channel >= m.firstRow + rows then m.firstRow = m.channel - rows + 1
    if m.canvas.getChildCount() > 0 then m.canvas.removeChildrenIndex(m.canvas.getChildCount(), 0)
    height = m.headerHeight + m.top.numRows * m.rowHeight
    detailsHeight = m.rowHeight * 2
    detailsY = m.headerHeight + (m.channel - m.firstRow + 1) * m.rowHeight
    timelineWidth = m.width - m.stationWidth
    scale = timelineWidth / m.top.duration
    now = CreateObject("roDateTime")
    currentTime = now.asSeconds()
    currentWindow = Int(currentTime / 1800) * 1800
    if m.windowStart <= currentWindow
        m.windowStart = currentWindow
        m.focusTime = currentTime
        chooseProgramme()
    end if
    drawBox(m.canvas, 0, 0, m.width, height, "0x111827ff")
    drawBox(m.canvas, 0, 0, m.width, m.headerHeight, "0x1f2937ff")
    label(m.canvas, "Channel", 16, 0, m.stationWidth - 32, m.headerHeight, m.titleFont)
    for tick = 0 to 6
        x = m.stationWidth + tick * 1800 * scale
        if x < m.width
            drawBox(m.canvas, x, 0, 1, height, "0x334155ff")
            label(m.canvas, timeText(m.windowStart + tick * 1800), x + 12, 20, 1800 * scale - 16, m.headerHeight - 20, m.timeFont)
        end if
    end for
    for visibleRow = 0 to rows - 1
        index = m.firstRow + visibleRow
        if index >= count then exit for
        row = m.top.content.getChild(index)
        y = m.headerHeight + visibleRow * m.rowHeight
        if index > m.channel then y += detailsHeight
        stationColor = "0x1f2937ff"
        if index = m.channel then stationColor = "0x172554ff"
        drawBox(m.canvas, 0, y, m.stationWidth, m.rowHeight, stationColor)
        if index = m.channel and m.channelFocus
            drawBox(m.canvas, 0, y, m.stationWidth, m.rowHeight, "0x93c5fdff")
            drawBox(m.canvas, 3, y + 3, m.stationWidth - 6, m.rowHeight - 6, stationColor)
        end if
        number = label(m.canvas, row.number, 8, y, 72, m.rowHeight, m.timeFont)
        number.horizAlign = "right"
        drawBox(m.canvas, 92, y + 22, 44, 44, "0x0b1220ff")
        if row.logoUrl <> ""
            poster = m.canvas.createChild("Poster")
            poster.translation = [92, y + 22]
            poster.width = 44
            poster.height = 44
            poster.loadWidth = 44
            poster.loadHeight = 44
            poster.loadDisplayMode = "scaleToFit"
            poster.uri = row.logoUrl
        else
            initial = label(m.canvas, Left(row.name, 1), 92, y + 22, 44, 44, m.titleFont)
            initial.horizAlign = "center"
        end if
        label(m.canvas, row.name, 148, y + 16, m.stationWidth - 164, 32, m.titleFont)
        label(m.canvas, row.groupName, 148, y + 48, m.stationWidth - 164, 23, m.smallFont, "0x94a3b8ff")
        for p = 0 to row.getChildCount() - 1
            programme = row.getChild(p)
            startTime = programme.playStart
            stopTime = startTime + programme.playDuration
            if stopTime > m.windowStart and startTime < m.windowStart + m.top.duration
                x = m.stationWidth + (startTime - m.windowStart) * scale
                rightEdge = m.stationWidth + (stopTime - m.windowStart) * scale
                if x < m.stationWidth then x = m.stationWidth
                if rightEdge > m.width then rightEdge = m.width
                width = rightEdge - x - 2
                if width > 4
                    if programme.title = "No guide data"
                        label(m.canvas, "No guide data", x + 16, y, width - 20, m.rowHeight, m.timeFont, "0x64748bff")
                    else
                        border = "0x475569ff"
                        fill = "0x1f2937ff"
                        if startTime <= currentTime and stopTime > currentTime
                            border = "0x22c55eff"
                            fill = "0x123525ff"
                        end if
                        ' Keep edges visible when the FHD canvas scales to a smaller display.
                        borderWidth = 2
                        if index = m.channel and p = m.programme and not m.channelFocus
                            border = "0x93c5fdff"
                            borderWidth = 3
                        end if
                        drawBox(m.canvas, x, y + 7, width, m.rowHeight - 14, border)
                        drawBox(m.canvas, x + borderWidth, y + 7 + borderWidth, width - 2 * borderWidth, m.rowHeight - 14 - 2 * borderWidth, fill)
                        if width > 32
                            label(m.canvas, programme.title, x + 12, y + 12, width - 24, 27, m.titleFont, "0xf8fafcff")
                            label(m.canvas, timeText(startTime) + "–" + timeText(stopTime), x + 12, y + 39, width - 24, 21, m.timeFont, "0xcbd5e1ff")
                            label(m.canvas, programme.subtitle, x + 12, y + 60, width - 24, 19, m.smallFont, "0x94a3b8ff")
                        end if
                    end if
                end if
            end if
        end for
        drawBox(m.canvas, 0, y + m.rowHeight - 2, m.width, 2, "0x334155ff")
        if index = m.channel then drawProgrammeDetails(row, detailsY, detailsHeight, currentTime)
    end for
    drawBox(m.canvas, m.stationWidth, 0, 1, height, "0x475569ff")
    nowX = m.stationWidth + (currentTime - m.windowStart) * scale
    if nowX >= m.stationWidth and nowX < m.width
        drawBox(m.canvas, nowX, 0, 2, detailsY, "0xfb7185ff")
        if detailsY + detailsHeight < height then drawBox(m.canvas, nowX, detailsY + detailsHeight, 2, height - detailsY - detailsHeight, "0xfb7185ff")
        drawBox(m.canvas, nowX - 4, 0, 10, 7, "0xfb7185ff")
    end if
    drawBox(m.canvas, 0, 0, m.width, 2, "0x475569ff")
    drawBox(m.canvas, 0, height - 2, m.width, 2, "0x475569ff")
    m.top.channelFocused = m.channel
    m.top.programFocused = m.programme
end sub

function onKeyEvent(key as String, press as Boolean) as Boolean
    if not press
        if key = m.heldKey
            stopNavigationRepeat()
            return true
        end if
        return false
    end if
    if not m.top.active or m.top.content = invalid then return false
    count = m.top.content.getChildCount()
    if count = 0 then return false
    if key = "OK"
        stopNavigationRepeat()
        if m.channelFocus
            m.top.channelInfoSelected = m.channel
        else
            m.top.programSelected = m.programme
        end if
        return true
    else if key = "up" or key = "down" or key = "rewind" or key = "fastforward"
        if key = m.heldKey then return true
        stopNavigationRepeat()
        m.heldKey = key
        m.holdTime.mark()
        m.keyRepeat.control = "start"
        moveChannel(key)
        return true
    else if key = "left" or key = "right"
        stopNavigationRepeat()
        moveTime(key)
    else if key = "replay"
        stopNavigationRepeat()
        now = CreateObject("roDateTime")
        m.focusTime = now.asSeconds()
        m.windowStart = Int(m.focusTime / 1800) * 1800
        m.channelFocus = false
        chooseProgramme()
    else
        stopNavigationRepeat()
        return false
    end if
    drawGuide()
    return true
end function
