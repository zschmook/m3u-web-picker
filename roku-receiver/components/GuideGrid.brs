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
    m.top.findNode("clock").observeField("fire", "drawGuide")
    m.top.findNode("clock").control = "start"
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
    if m.top.content = invalid then return
    count = m.top.content.getChildCount()
    if count = 0 then return
    rows = m.top.numRows
    if m.channel < m.firstRow then m.firstRow = m.channel
    if m.channel >= m.firstRow + rows then m.firstRow = m.channel - rows + 1
    if m.canvas.getChildCount() > 0 then m.canvas.removeChildrenIndex(m.canvas.getChildCount(), 0)
    height = m.headerHeight + rows * m.rowHeight
    timelineWidth = m.width - m.stationWidth
    scale = timelineWidth / m.top.duration
    now = CreateObject("roDateTime")
    currentTime = now.asSeconds()
    drawBox(m.canvas, 0, 0, m.width, height, "0x111827ff")
    drawBox(m.canvas, 0, 0, m.width, m.headerHeight, "0x1f2937ff")
    label(m.canvas, "Channel", 16, 0, m.stationWidth - 32, m.headerHeight, m.titleFont)
    for tick = 0 to 6
        x = m.stationWidth + tick * 1800 * scale
        if x < m.width
            drawBox(m.canvas, x, 0, 1, height, "0x334155ff")
            label(m.canvas, timeText(m.windowStart + tick * 1800), x + 12, 0, 1800 * scale - 16, m.headerHeight, m.timeFont)
        end if
    end for
    for visibleRow = 0 to rows - 1
        index = m.firstRow + visibleRow
        if index >= count then exit for
        row = m.top.content.getChild(index)
        y = m.headerHeight + visibleRow * m.rowHeight
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
                        borderWidth = 1
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
        drawBox(m.canvas, 0, y + m.rowHeight - 1, m.width, 1, "0x334155ff")
    end for
    drawBox(m.canvas, m.stationWidth, 0, 1, height, "0x475569ff")
    nowX = m.stationWidth + (currentTime - m.windowStart) * scale
    if nowX >= m.stationWidth and nowX < m.width
        drawBox(m.canvas, nowX, 0, 2, height, "0xfb7185ff")
        drawBox(m.canvas, nowX - 4, 0, 10, 7, "0xfb7185ff")
    end if
    drawBox(m.canvas, 0, 0, m.width, 1, "0x475569ff")
    drawBox(m.canvas, 0, height - 1, m.width, 1, "0x475569ff")
    m.top.channelFocused = m.channel
    m.top.programFocused = m.programme
end sub

function onKeyEvent(key as String, press as Boolean) as Boolean
    if not press or m.top.content = invalid then return false
    count = m.top.content.getChildCount()
    if count = 0 then return false
    if key = "OK"
        if m.channelFocus
            m.top.channelInfoSelected = m.channel
        else
            m.top.programSelected = m.programme
        end if
        return true
    else if key = "up" or key = "down" or key = "rewind" or key = "fastforward"
        amount = 1
        if key = "rewind" or key = "fastforward" then amount = m.top.numRows
        if key = "up" or key = "rewind" then amount = -amount
        m.channel += amount
        if m.channel < 0 then m.channel = 0
        if m.channel >= count then m.channel = count - 1
        chooseProgramme()
    else if key = "left" or key = "right"
        row = rowContent()
        if key = "left"
            if m.programme > 0 and not m.channelFocus
                m.programme--
            else
                m.channelFocus = true
            end if
        else
            if m.channelFocus
                m.channelFocus = false
            else if m.programme < row.getChildCount() - 1
                m.programme++
            end if
        end if
        programme = row.getChild(m.programme)
        if programme <> invalid and not m.channelFocus
            m.focusTime = programme.playStart + 1
            if m.focusTime < m.windowStart then m.windowStart = Int(m.focusTime / 1800) * 1800
            if m.focusTime >= m.windowStart + m.top.duration
                m.windowStart = Int(m.focusTime / 1800) * 1800 - m.top.duration + 1800
            end if
        end if
    else if key = "replay"
        now = CreateObject("roDateTime")
        m.focusTime = now.asSeconds()
        m.windowStart = Int(m.focusTime / 1800) * 1800
        m.channelFocus = false
        chooseProgramme()
    else
        return false
    end if
    drawGuide()
    return true
end function
