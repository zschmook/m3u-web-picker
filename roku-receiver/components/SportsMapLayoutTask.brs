sub init()
    m.top.functionName = "buildLayout"
end sub

sub buildLayout()
    m.top.result = {requestId: m.top.requestId, nodes: sportsMapLayout(m.top.regions, m.top.weighted)}
end sub
