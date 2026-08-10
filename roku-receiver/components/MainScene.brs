sub init()
    m.video = m.top.findNode("video")
    m.status = m.top.findNode("status")
    m.video.observeField("state", "onVideoState")
    m.top.setFocus(true)
end sub

sub playUrl(url as String)
    if url = invalid or url = "" then return

    m.status.text = "Loading live TV..."
    content = CreateObject("roSGNode", "ContentNode")
    content.url = url
    content.streamFormat = "hls"
    content.title = "M3U Web Picker"

    m.video.content = content
    m.video.control = "play"
    m.video.setFocus(true)
end sub

sub onVideoState()
    state = m.video.state
    if state = "playing"
        m.status.text = ""
    else if state = "buffering"
        m.status.text = "Buffering..."
    else if state = "error"
        m.status.text = "Playback failed. Check M3U Web Picker logs."
    end if
end sub
