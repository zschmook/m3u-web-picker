function guideProgrammeDetails(channel as Object, programme as Object, currentTime as Integer) as Object
    if m.detailPatterns = invalid
        m.detailPatterns = {
            sports: CreateObject("roRegex", "\b(sports?|football|baseball|basketball|hockey|soccer|tennis|golf|mlb|nfl|nba|wnba|nhl|ncaa|pga|ufc|presidents cup)\b", "i")
            episode: CreateObject("roRegex", "\bS([0-9]{1,2})E([0-9]{1,3})\b", "i")
            year: CreateObject("roRegex", "^(19|20)[0-9]{2}$", "")
        }
    end if
    path = channel.play_url
    kind = "network"
    if programme.media_type = "movie" or Left(path, 19) = "/guide/play/movies/"
        kind = "movie"
    else if Left(path, 19) = "/guide/play/custom/"
        kind = "tv"
    else if Left(path, 19) = "/guide/play/sports/" or m.detailPatterns.sports.isMatch(programme.title)
        kind = "sports"
    else if channel.is_sports = true and programme.start <= currentTime and programme.stop > currentTime
        kind = "sports"
    end if
    facts = []
    subtitle = ""
    if programme.subtitle <> invalid then subtitle = programme.subtitle.trim()
    if kind = "movie"
        year = ""
        if programme.release_date <> invalid then year = Left(programme.release_date, 4)
        if m.detailPatterns.year.isMatch(subtitle)
            if year = "" then year = subtitle
            if subtitle = year then subtitle = ""
        end if
        if year <> "" then facts.push(year)
    else if kind = "tv" or kind = "network"
        season = programme.season
        episode = programme.episode
        if season = invalid or episode = invalid
            match = m.detailPatterns.episode.match(programme.title)
            if match.count() > 2
                season = match[1].toInt()
                episode = match[2].toInt()
            end if
        end if
        if season <> invalid then facts.push("Season " + season.toStr())
        if episode <> invalid then facts.push("Episode " + episode.toStr())
    end if
    if subtitle <> "" and LCase(subtitle) <> "provider event stream" then facts.push(subtitle)
    if programme.categories <> invalid
        for each category in programme.categories
            if category <> "" and category <> "TV" and category <> "Sports" then facts.push(category)
        end for
    end if
    details = ""
    for each fact in facts
        if details <> "" then details += " • "
        details += fact
    end for
    description = ""
    if programme.description <> invalid then description = programme.description.trim()
    if LCase(description) = "custom plex channel" then description = ""
    if description = ""
        if kind = "movie" or kind = "tv"
            description = "Synopsis unavailable."
        else if kind = "sports"
            description = "Event details unavailable."
        else
            description = "No description available."
        end if
    end if
    return {kind: kind, facts: details, description: description}
end function
