function guideChannelCategories(channel as Object) as Object
    path = ""
    if channel.play_url <> invalid then path = channel.play_url
    if Left(path, 19) = "/guide/play/movies/" then return ["MOVIES"]
    if channel.is_movie = true then return ["MOVIES"]
    if Left(path, 19) = "/guide/play/custom/" then return ["TV SHOWS"]
    labels = ""
    if channel.name <> invalid then labels += " " + channel.name
    if channel.group <> invalid then labels += " " + channel.group
    movie = false
    shows = false
    if CreateObject("roRegex", "\b(movies?|cinema|films?)\b", "i").isMatch(labels) then movie = true
    if CreateObject("roRegex", "\b(tv shows?|series|24/7|24x7)\b", "i").isMatch(labels) then shows = true
    if movie then return ["MOVIES"]
    if shows then return ["TV SHOWS"]
    categories = []
    if Left(path, 19) = "/guide/play/sports/"
        ' Generated feed names begin with their league, so newly added sports
        ' get a tab without requiring another app release.
        league = channel.name.split("•")[0].trim()
        if CreateObject("roRegex", "\b(ncaa|ncaaf|ncaab|college)\b", "i").isMatch(league) then return ["NCAA"]
        if CreateObject("roRegex", "high school", "i").isMatch(league) then return ["HIGH SCHOOL"]
        if CreateObject("roRegex", "\b(pga|golf)\b", "i").isMatch(league) then return ["PGA"]
        if league = "" then league = "SPORTS"
        return [UCase(league)]
    end if
    categories.push("NETWORK TV")
    if CreateObject("roRegex", "\b(mlb|major league baseball)\b", "i").isMatch(labels) then categories.push("MLB")
    if CreateObject("roRegex", "\b(nfl|red ?zone|sunday ticket)\b", "i").isMatch(labels) then categories.push("NFL")
    if CreateObject("roRegex", "\b(ncaa|ncaaf|ncaab|college|big ten|btn|sec network|acc network)\b", "i").isMatch(labels) then categories.push("NCAA")
    for each league in ["NBA", "WNBA", "NHL", "MLS", "UFC", "PGA"]
        if CreateObject("roRegex", "\b" + league + "\b", "i").isMatch(labels) then categories.push(league)
    end for
    return categories
end function

function guideCategoryNames(groups as Object) as Object
    names = ["ALL"]
    for each category in ["NETWORK TV", "MLB", "NFL", "NCAA"]
        if groups.doesExist(category) then names.push(category)
    end for
    extras = []
    for each category in groups
        category = UCase(category)
        if category <> "ALL" and category <> "NETWORK TV" and category <> "MLB" and category <> "NFL" and category <> "NCAA" and category <> "TV SHOWS" and category <> "MOVIES"
            extras.push(category)
        end if
    end for
    extras.sort()
    for each category in extras
        names.push(category)
    end for
    for each category in ["TV SHOWS", "MOVIES"]
        if groups.doesExist(category) then names.push(category)
    end for
    return names
end function
