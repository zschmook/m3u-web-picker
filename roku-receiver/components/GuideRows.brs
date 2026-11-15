' Cache a bounded set of visited rows, rather than building the whole lineup.
function guideRow(index as Integer) as Dynamic
    if index < 0 or index >= m.rawChannels.count() then return invalid
    key = index.toStr()
    if m.rowCache.doesExist(key) then return m.rowCache[key]
    if m.rowOrder.count() >= 32 then m.rowCache.delete(m.rowOrder.shift())
    row = guideBuildRow(m.rawChannels[index], m.top.serverBase, m.top.contentStartTime + 1800)
    m.rowCache[key] = row
    m.rowOrder.push(key)
    return row
end function

function guideBuildRow(channel as Object, server as String, serverTime as Integer) as Object
    row = CreateObject("roSGNode", "ContentNode")
    row.title = channel.number + "  " + channel.name
    logo = channel.logo
    if Left(logo, 1) = "/" then logo = server + logo
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
    return row
end function
