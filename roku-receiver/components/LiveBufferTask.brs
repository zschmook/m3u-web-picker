sub init()
    m.top.functionName = "waitForLiveBuffer"
end sub

sub waitForLiveBuffer()
    transfer = CreateObject("roUrlTransfer")
    port = CreateObject("roMessagePort")
    transfer.setPort(port)
    transfer.setCertificatesFile("common:/certs/ca-bundle.crt")
    transfer.initClientCertificates()
    transfer.setUrl(m.top.url)
    transfer.enableEncodings(true)
    clock = CreateObject("roTimespan")
    clock.mark()
    duration = 0.0
    while clock.totalMilliseconds() < 90000
        if transfer.asyncGetToString()
            event = wait(5000, port)
            if type(event) = "roUrlEvent"
                if event.getResponseCode() = 200
                    duration = livePlaylistDuration(event.getString())
                    if duration >= m.top.seconds + 2
                        playlist = event.getString()
                        manifest = prepareLiveMaster(playlist, transfer, port)
                        m.top.result = {ok: true, requestId: m.top.requestId, seconds: duration, playbackUrl: manifest}
                        return
                    end if
                end if
            else
                transfer.asyncCancel()
            end if
        end if
        sleep(2000)
    end while
    m.top.result = {ok: false, requestId: m.top.requestId, seconds: duration}
end sub

function livePlaylistDuration(playlist as String) as Float
    duration = 0.0
    for each line in CreateObject("roRegex", chr(10), "").split(playlist)
        if left(line, 8) = "#EXTINF:"
            duration += mid(line, 9).toFloat()
        end if
    end for
    return duration
end function

function livePlaylistSegments(playlist as String) as Object
    segments = []
    duration = 0.0
    safeName = CreateObject("roRegex", "^segment_[0-9]+[.]ts$", "")
    for each raw in CreateObject("roRegex", chr(10), "").split(playlist)
        line = raw.trim()
        if left(line, 8) = "#EXTINF:"
            duration = mid(line, 9).toFloat()
        else if safeName.isMatch(line) and duration > 0
            segments.push({name: line, seconds: duration})
            duration = 0.0
        end if
    end for
    return segments
end function

function liveMasterPlaylist(url as String, bandwidth as Integer) as String
    return "#EXTM3U" + chr(10) + "#EXT-X-VERSION:3" + chr(10) + "#EXT-X-STREAM-INF:BANDWIDTH=" + bandwidth.toStr().trim() + chr(10) + url + chr(10)
end function

function prepareLiveMaster(playlist as String, transfer as Object, port as Object) as String
    ' A bare transport-stream playlist can make Roku infer the AAC bitrate as
    ' the whole video's bandwidth. Declare measured media bandwidth explicitly.
    segments = livePlaylistSegments(playlist)
    path = CreateObject("roRegex", "[^/]+$", "").replace(m.top.url, "")
    peak = 0.0
    start = segments.count() - 6
    if start < 0 then start = 0
    for i = start to segments.count() - 1
        transfer.setUrl(path + segments[i].name)
        if transfer.asyncHead()
            event = wait(1500, port)
            if type(event) = "roUrlEvent"
                if event.getResponseCode() = 200
                    headers = event.getResponseHeaders()
                    if headers <> invalid
                        for each key in headers
                            if lcase(key) = "content-length"
                                rate = headers[key].toFloat() * 8 / segments[i].seconds
                                if rate > peak then peak = rate
                            end if
                        end for
                    end if
                end if
            else
                transfer.asyncCancel()
                exit for
            end if
        end if
    end for
    if peak <= 0 then return m.top.url
    bandwidth = int(peak * 1.25)
    localUrl = "tmp:/m3u-live-master.m3u8"
    if WriteAsciiFile(localUrl, liveMasterPlaylist(m.top.url, bandwidth))
        print "M3U declared video bandwidth="; bandwidth
        return localUrl
    end if
    return m.top.url
end function
