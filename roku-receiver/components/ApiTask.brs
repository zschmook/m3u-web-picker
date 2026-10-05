sub init()
    m.top.functionName = "runRequest"
end sub

sub runRequest()
    transfer = CreateObject("roUrlTransfer")
    port = CreateObject("roMessagePort")
    transfer.setPort(port)
    transfer.setCertificatesFile("common:/certs/ca-bundle.crt")
    transfer.initClientCertificates()
    transfer.setUrl(m.top.url)
    transfer.addHeader("Accept", "application/json")
    transfer.enableEncodings(true)
    if m.top.method = "POST"
        transfer.addHeader("Content-Type", "application/json")
        sent = transfer.asyncPostFromString(m.top.body)
    else
        sent = transfer.asyncGetToString()
    end if
    result = {ok: false, error: "Server did not respond. Check its address and try again.", purpose: m.top.purpose, requestId: m.top.requestId}
    if sent
        timeout = 45000
        if m.top.purpose = "movie_lighting" then timeout = 5000
        if m.top.purpose = "play" then timeout = 65000
        event = wait(timeout, port)
        if type(event) = "roUrlEvent"
            code = event.getResponseCode()
            data = invalid
            if event.getString() <> "" then data = ParseJson(event.getString())
            if data <> invalid
                result.data = data
                if code >= 200 and code < 300
                    result.ok = true
                else if data.error <> invalid
                    result.error = data.error
                end if
            end if
        else
            transfer.asyncCancel()
        end if
    end if
    m.top.result = result
end sub
