sub Main(args as Dynamic)
    screen = CreateObject("roSGScreen")
    port = CreateObject("roMessagePort")
    screen.SetMessagePort(port)

    scene = screen.CreateScene("MainScene")
    screen.Show()

    if args <> invalid and args.contentId <> invalid
        scene.callFunc("playUrl", args.contentId)
    end if

    while true
        msg = wait(0, port)
        msgType = type(msg)

        if msgType = "roSGScreenEvent"
            if msg.isScreenClosed() then return
        else if msgType = "roInputEvent"
            if msg.IsInput()
                info = msg.GetInfo()
                if info <> invalid and info.DoesExist("contentid")
                    scene.callFunc("playUrl", info.contentid)
                end if
            end if
        end if
    end while
end sub
