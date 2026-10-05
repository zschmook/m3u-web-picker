sub init()
    m.top.functionName = "renewPlaybackLease"
end sub

sub renewPlaybackLease()
    ' Cache the input once: the repeating loop never accesses scene/video nodes.
    url = m.top.url
    body = m.top.body
    diagnostics = m.top.diagnostics
    port = CreateObject("roMessagePort")
    transfer = CreateObject("roUrlTransfer")
    transfer.setPort(port)
    transfer.setCertificatesFile("common:/certs/ca-bundle.crt")
    transfer.initClientCertificates()
    transfer.setUrl(url)
    transfer.addHeader("Accept", "application/json")
    transfer.addHeader("Content-Type", "application/json")
    clock = CreateObject("roTimespan")
    lifetime = CreateObject("roTimespan")
    lifetime.mark()
    failed = false
    while true
        clock.mark()
        ok = false
        if transfer.asyncPostFromString(body)
            event = wait(5000, port)
            if type(event) = "roUrlEvent"
                code = event.getResponseCode()
                if code >= 200 and code < 300
                    data = ParseJson(event.getString())
                    if data <> invalid then ok = data.active = true
                end if
            else
                transfer.asyncCancel()
            end if
        end if
        if not ok and not failed then print "M3U playback lease renewal failed"
        if ok and failed then print "M3U playback lease renewal restored"
        failed = not ok
        if diagnostics then print "M3U lease "; FormatJson({active: ok, elapsed_ms: lifetime.totalMilliseconds(), request_ms: clock.totalMilliseconds()})
        retryMs = 30000
        if failed then retryMs = 5000
        remaining = retryMs - clock.totalMilliseconds()
        if remaining > 0 then sleep(remaining)
    end while
end sub
