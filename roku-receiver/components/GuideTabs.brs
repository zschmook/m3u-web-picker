sub init()
    m.canvas = m.top.findNode("canvas")
    holder = CreateObject("roSGNode", "Label")
    holder.font = "font:SmallBoldSystemFont"
    m.font = holder.font
    m.font.size = 23
    m.scrollOffset = 0
end sub

sub drawTabs()
    if m.canvas = invalid or m.top.labels = invalid then return
    if m.canvas.getChildCount() > 0 then m.canvas.removeChildrenIndex(m.canvas.getChildCount(), 0)
    widths = []
    totalWidth = 0
    selectedLeft = 0
    selectedRight = 0
    for i = 0 to m.top.labels.count() - 1
        width = Len(m.top.labels[i]) * 15 + 56
        if width < 120 then width = 120
        widths.push(width)
        if i = m.top.selectedIndex
            selectedLeft = totalWidth
            selectedRight = totalWidth + width
        end if
        totalWidth += width + 12
    end for
    if selectedLeft < m.scrollOffset then m.scrollOffset = selectedLeft
    if selectedRight > m.scrollOffset + 1760 then m.scrollOffset = selectedRight - 1760
    if totalWidth <= 1760 then m.scrollOffset = 0
    x = -m.scrollOffset
    for i = 0 to m.top.labels.count() - 1
        color = "0x1f2937ff"
        textColor = "0xcbd5e1ff"
        if i = m.top.selectedIndex
            color = "0x1d4ed8ff"
            textColor = "0xffffffff"
        end if
        background = m.canvas.createChild("Rectangle")
        background.translation = [x, 0]
        background.width = widths[i]
        background.height = 54
        background.color = color
        if i = m.top.selectedIndex and m.top.focused
            background.color = "0x93c5fdff"
            fill = m.canvas.createChild("Rectangle")
            fill.translation = [x + 3, 3]
            fill.width = widths[i] - 6
            fill.height = 48
            fill.color = color
        end if
        label = m.canvas.createChild("Label")
        label.translation = [x, 0]
        label.width = widths[i]
        label.height = 54
        label.text = m.top.labels[i]
        label.font = m.font
        label.color = textColor
        label.horizAlign = "center"
        label.vertAlign = "center"
        x += widths[i] + 12
    end for
end sub

function onKeyEvent(key as String, press as Boolean) as Boolean
    if not press then return false
    if key = "left" or key = "right"
        index = m.top.selectedIndex
        if key = "left" then index-- else index++
        if index < 0 then index = 0
        if index >= m.top.labels.count() then index = m.top.labels.count() - 1
        m.top.selectedIndex = index
        return true
    else if key = "down" or key = "OK"
        m.top.gridRequested = true
        return true
    else if key = "up"
        return true
    end if
    return false
end function
