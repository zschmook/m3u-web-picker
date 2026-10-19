function sportsMapRegions(data as Object, league as String) as Object
    regions = []
    if data = invalid or data.states = invalid then return regions
    for each region in data.states
        games = []
        for each game in region.games
            if league = "All sports" or game.league = league then games.push(game)
        end for
        regions.push({id: region.id, name: region.name, row: region.row, col: region.col, games: games})
    end for
    return regions
end function

function sportsMapLeagues(data as Object) as Object
    names = []
    seen = {}
    if data <> invalid and data.states <> invalid
        for each region in data.states
            for each game in region.games
                if not seen.doesExist(game.league)
                    seen[game.league] = true
                    names.push(game.league)
                end if
            end for
        end for
    end if
    names.sort("i")
    names.unshift("All sports")
    return names
end function

function sportsMapRegion(regions as Object, id as String) as Dynamic
    for each region in regions
        if region.id = id then return region
    end for
    return invalid
end function

function sportsMapNavigate(regions as Object, current as String, direction as String) as String
    origin = sportsMapRegion(regions, current)
    if origin = invalid then return current
    horizontal = direction = "left" or direction = "right"
    sign = 1
    if direction = "left" or direction = "up" then sign = -1
    best = current
    bestScore = 1000000
    for each region in regions
        if horizontal
            along = (region.col - origin.col) * sign
            across = abs(region.row - origin.row)
        else
            along = (region.row - origin.row) * sign
            across = abs(region.col - origin.col)
        end if
        score = sqr(along * along + across * across) + across * 1.8
        if along > 0 and score < bestScore
            bestScore = score
            best = region.id
        end if
    end for
    return best
end function

function sportsMapWrap(index as Integer, count as Integer, delta as Integer) as Integer
    if count <= 0 then return 0
    return (index + delta + count) mod count
end function

function sportsMapNextActive(regions as Object, current as String, delta as Integer) as String
    active = []
    index = -1
    for each region in regions
        if region.games.count() > 0
            if region.id = current then index = active.count()
            active.push(region.id)
        end if
    end for
    if active.count() = 0 then return current
    if index < 0
        if delta > 0 then return active[0]
        return active[active.count() - 1]
    end if
    return active[sportsMapWrap(index, active.count(), delta)]
end function

function sportsMapLayout(regions as Object, weighted as Boolean) as Object
    nodes = []
    for each region in regions
        side = 52.0
        if weighted
            side = 30.0
            count = region.gameCount
            if count = invalid then count = region.games.count()
            if count > 0 then side = 52.0 * sqr(count)
        end if
        nodes.push({id: region.id, ax: region.col * 60.0 + 30, ay: region.row * 60.0 + 30, x: region.col * 60.0 + 30, y: region.row * 60.0 + 30, side: side})
    end for
    if nodes.count() = 0 then return nodes
    ' Work runs on SportsMapLayoutTask, never the SceneGraph render thread.
    if weighted
        for iteration = 0 to 189
            if iteration < 150
                for each node in nodes
                    node.x += (node.ax - node.x) * 0.035
                    node.y += (node.ay - node.y) * 0.035
                end for
            end if
            for i = 0 to nodes.count() - 2
                for j = i + 1 to nodes.count() - 1
                    a = nodes[i] : b = nodes[j]
                    dx = b.x - a.x : dy = b.y - a.y
                    gap = (a.side + b.side) / 2 + 7
                    overlapX = gap - abs(dx) : overlapY = gap - abs(dy)
                    if overlapX > 0 and overlapY > 0
                        if overlapX < overlapY
                            sign = 1
                            if dx < 0 then sign = -1
                            move = overlapX / 2 + 0.01
                            a.x -= move * sign : b.x += move * sign
                        else
                            sign = 1
                            if dy < 0 then sign = -1
                            move = overlapY / 2 + 0.01
                            a.y -= move * sign : b.y += move * sign
                        end if
                    end if
                end for
            end for
        end for
    end if
    minX = 1000000.0 : minY = 1000000.0 : maxX = -1000000.0 : maxY = -1000000.0
    for each node in nodes
        if node.x - node.side / 2 < minX then minX = node.x - node.side / 2
        if node.y - node.side / 2 < minY then minY = node.y - node.side / 2
        if node.x + node.side / 2 > maxX then maxX = node.x + node.side / 2
        if node.y + node.side / 2 > maxY then maxY = node.y + node.side / 2
    end for
    scale = 1160 / (maxX - minX)
    if (maxY - minY) * scale > 650 then scale = 650 / (maxY - minY)
    offsetX = (1220 - (maxX - minX) * scale) / 2
    offsetY = (680 - (maxY - minY) * scale) / 2
    for each node in nodes
        node.x = offsetX + (node.x - minX) * scale
        node.y = offsetY + (node.y - minY) * scale
        node.side *= scale
    end for
    return nodes
end function

function sportsMapBubblePositions(hub as Object, count as Integer) as Object
    candidates = []
    for col = 0 to 4
        for row = 0 to 2
            x = 130 + col * 240 : y = 120 + row * 220
            dx = x - hub.x : dy = y - hub.y
            distance = sqr(dx * dx + dy * dy)
            if distance > 155 then candidates.push({x: x, y: y, distance: distance})
        end for
    end for
    candidates.sortBy("distance")
    selected = []
    for i = 0 to count - 1
        if i < candidates.count() then selected.push(candidates[i])
    end for
    ' This order is also the remote order: left to right, then top to bottom.
    for each point in selected
        point.order = point.x * 1000 + point.y
    end for
    selected.sortBy("order")
    return selected
end function
