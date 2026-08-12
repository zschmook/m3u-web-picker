from __future__ import annotations

import re
from datetime import timedelta

DEFAULT_SETTINGS = {
    "enabled": False,
    "auto_update": True,
    # Deliberately separate from sports_rules. Turning this on temporarily
    # matches every detected event while preserving the user's curated list.
    "everything_mode": False,
    "start_channel": 1000,
    "channels_per_event": 10,
    "group_title": "Sports Today",
    "timezone": "America/New_York",
    # Persist one canonical 24-hour value. Display formatting belongs in the UI.
    "refresh_time": "03:00",
    # Daily preserves the original once-per-day behavior. Interval mode runs
    # relative to the most recently completed scan attempt.
    "schedule_mode": "daily",
    "interval_hours": 2,
    "event_window": "today",
    "include_replays": False,
    "include_pregame": False,
    "use_backup_feeds": True,
    "exclude_sd": False,
    # Optional authoritative schedule source. API-SPORTS is currently the
    # only supported schedule provider. The API key itself is stored in an
    # internal sports_settings row and is never returned to the browser.
    "schedule_api_enabled": False,
    # Retained only so existing RC databases load cleanly. RC5 no longer asks
    # users for a base URL; product hosts are owned by the adapters below.
    "schedule_api_url": "",
}

SCHEDULE_API_KEY_SETTING = "__schedule_api_key"
SCHEDULE_API_PROVIDER_NAME = "API-SPORTS"
SCHEDULE_API_PROVIDER_URL = "https://api-sports.io"
SCHEDULE_API_SOURCE = "api-sports-baseball"  # backward-compatible MLB constant
SCHEDULE_API_LEAGUE_ID = "mlb"               # backward-compatible MLB constant
SCHEDULE_API_REMOTE_LEAGUE_ID = 1             # backward-compatible MLB constant

# Schedule API support is deliberately explicit. Sports/leagues not listed
# here continue through the existing provider + XMLTV matcher with no API call.
# One API-SPORTS key works across these products, but each product has its own
# host and response shape.
SCHEDULE_API_DATASETS = {
    "mlb": {
        "id": "mlb",
        "label": "MLB",
        "product": "baseball",
        "source": "api-sports-baseball",
        "base_url": "https://v1.baseball.api-sports.io",
        "league_id": "mlb",
        "remote_league_id": 1,
        "sport_id": "baseball",
        "season_mode": "calendar",
        "request_mode": "baseball_day",
    },
    "nfl": {
        "id": "nfl",
        "label": "NFL",
        "product": "american_football",
        "source": "api-sports-american-football",
        "base_url": "https://v1.american-football.api-sports.io",
        "league_id": "nfl",
        "remote_league_id": 1,
        "sport_id": "football",
        "season_mode": "start_year",
        "request_mode": "american_football",
    },
    "ncaa": {
        "id": "ncaa",
        "label": "NCAA Football",
        "product": "american_football",
        "source": "api-sports-american-football",
        "base_url": "https://v1.american-football.api-sports.io",
        "league_id": "ncaaf-fbs",
        "remote_league_id": 2,
        "sport_id": "football",
        "season_mode": "start_year",
        "request_mode": "american_football",
    },
}

SCHEDULE_API_DATASET_BY_LEAGUE = {
    value["league_id"]: key for key, value in SCHEDULE_API_DATASETS.items()
}
SCHEDULE_API_DATASETS_BY_SPORT = {
    "baseball": ("mlb",),
    "football": ("nfl", "ncaa"),
}

SCOPE_TYPES = {"league", "team", "conference", "sport"}
RULE_PRIORITY = {"team": 0, "conference": 1, "league": 2, "sport": 3}

# Sports taxonomy is intentionally data-driven. A "sport" is the broad
# umbrella users can select, while a "league" also covers tours, series,
# promotions, divisions, and competition families. Each league/series receives
# its own 1,000-channel primary block.
SPORT_DEFINITIONS = [
    ("baseball", "Baseball", [r"\bbaseball\b", r"world baseball classic"]),
    ("basketball", "Basketball", [r"\bbasketball\b"]),
    ("football", "Football", [r"\bfootball\b", r"\bnfl\b", r"\bncaaf\b"]),
    ("hockey", "Hockey", [r"\bhockey\b"]),
    ("soccer", "Soccer", [r"\bsoccer\b", r"association football", r"premier league", r"champions league"]),
    ("cricket", "Cricket", [r"\bcricket\b", r"\bicc\b", r"\bipl\b", r"\bt20\b", r"\bodi\b"]),
    ("rugby-union", "Rugby Union", [r"rugby union", r"six nations", r"rugby world cup", r"super rugby"]),
    ("rugby-league", "Rugby League", [r"rugby league", r"\bnrl\b", r"state of origin"]),
    ("curling", "Curling", [r"\bcurling\b"]),
    ("golf", "Golf", [r"\bgolf\b", r"\bpga\b", r"\blpga\b"]),
    ("track-field", "Track & Field", [r"track\s*(?:&|and)\s*field", r"\bathletics\b", r"diamond league"]),
    ("swimming", "Swimming", [r"\bswimming\b", r"world aquatics"]),
    ("gymnastics", "Gymnastics", [r"\bgymnastics\b"]),
    ("figure-skating", "Figure Skating", [r"figure skating"]),
    ("speed-skating", "Speed Skating", [r"speed skating", r"short track skating", r"long track skating"]),
    ("skiing", "Skiing", [r"\bskiing\b", r"ski jumping", r"nordic combined", r"cross[- ]country ski"]),
    ("snowboarding", "Snowboarding", [r"\bsnowboard(?:ing)?\b", r"boardercross"]),
    ("sliding-sports", "Sliding Sports", [r"\bbobsle(?:d|igh)\b", r"\bskeleton\b", r"\bluge\b"]),
    ("olympics", "Olympics", [r"\bolympic(?:s)?\b", r"\bparalympic(?:s)?\b"]),
    ("motorsports", "Motorsports / Racing", [r"\bmotorsports?\b", r"auto racing", r"motor racing", r"monster jam"]),
    ("mma", "MMA", [r"mixed martial arts", r"\bmma\b", r"\bufc\b", r"fight night"]),
    ("pro-wrestling", "Pro Wrestling", [r"pro(?:fessional)? wrestling", r"\bwwe\b", r"\baew\b", r"\btna\b", r"\bnjpw\b", r"\broh\b"]),
    ("wrestling", "Amateur / Olympic Wrestling", [r"collegiate wrestling", r"amateur wrestling", r"freestyle wrestling", r"greco[- ]roman"]),
    ("darts", "Darts", [r"\bdarts?\b", r"\bpdc\b", r"\bwdf\b"]),
    ("poker", "Poker", [r"\bpoker\b", r"\bwsop\b", r"\bwpt\b", r"\bept\b"]),
    ("cornhole", "Cornhole", [r"\bcornhole\b", r"american cornhole", r"bag toss"]),
    ("cycling", "Cycling", [r"\bcycling\b", r"tour de france", r"giro d['’]?italia", r"vuelta a españa", r"cyclocross", r"\bbmx\b"]),
    ("tennis", "Tennis", [r"\btennis\b", r"\batp\b", r"\bwta\b"]),
    ("volleyball", "Volleyball", [r"\bvolleyball\b", r"beach volleyball"]),
    ("boxing", "Boxing", [r"\bboxing\b", r"fight card"]),
    ("softball", "Softball", [r"\bsoftball\b"]),
    ("lacrosse", "Lacrosse", [r"\blacrosse\b"]),
    ("horse-racing", "Horse Racing", [r"horse racing", r"kentucky derby", r"breeders'? cup"]),
    ("biathlon", "Biathlon", [r"\bbiathlon\b"]),
    ("diving", "Diving", [r"\bdiving\b", r"springboard", r"platform diving"]),
    ("water-polo", "Water Polo", [r"water polo"]),
    ("artistic-swimming", "Artistic Swimming", [r"artistic swimming", r"synchronized swimming"]),
    ("rowing", "Rowing", [r"\browing\b", r"world rowing"]),
    ("canoe-kayak", "Canoe / Kayak", [r"\bcanoe(?:ing)?\b", r"\bkayak(?:ing)?\b"]),
    ("sailing", "Sailing", [r"\bsailing\b", r"world sailing"]),
    ("triathlon", "Triathlon", [r"\btriathlon\b", r"ironman"]),
    ("archery", "Archery", [r"\barchery\b"]),
    ("shooting", "Shooting Sports", [r"sport shooting", r"shooting championship", r"\bissf\b"]),
    ("weightlifting", "Weightlifting", [r"weightlifting", r"weight lifting"]),
    ("equestrian", "Equestrian", [r"equestrian", r"show jumping", r"dressage", r"eventing"]),
    ("handball", "Handball", [r"\bhandball\b"]),
    ("field-hockey", "Field Hockey", [r"field hockey"]),
    ("badminton", "Badminton", [r"\bbadminton\b"]),
    ("table-tennis", "Table Tennis", [r"table tennis", r"ping pong"]),
    ("fencing", "Fencing", [r"\bfencing\b"]),
    ("judo", "Judo", [r"\bjudo\b"]),
    ("taekwondo", "Taekwondo", [r"tae kwon do", r"taekwondo"]),
    ("sport-climbing", "Sport Climbing", [r"sport climbing", r"competition climbing"]),
    ("surfing", "Surfing", [r"\bsurfing\b", r"world surf league"]),
    ("skateboarding", "Skateboarding", [r"skateboarding", r"skateboard street", r"skateboard park"]),
    ("modern-pentathlon", "Modern Pentathlon", [r"modern pentathlon"]),
    ("bowling", "Bowling", [r"\bbowling\b", r"\bpba\b", r"\bpwba\b"]),
    ("billiards", "Billiards / Cue Sports", [r"\bbilliards?\b", r"\bsnooker\b", r"pool championship"]),
]

# Ordered for channel numbering, not detection. The first four preserve the
# simple mental model discussed in the UI: MLB 1000s, NHL 2000s, NBA 3000s,
# NFL 4000s. Everything else follows in stable 1,000-channel blocks.
LEAGUE_DEFINITIONS = [
    # id, display name, sport id, subtitle, aliases, detection patterns
    ("mlb", "MLB", "baseball", "Every Major League Baseball game", ["Major League Baseball"], [r"\bmlb\b", r"major league baseball"]),
    ("nhl", "NHL", "hockey", "Every National Hockey League game", ["National Hockey League"], [r"\bnhl\b", r"national hockey league"]),
    ("nba", "NBA", "basketball", "Every NBA game", ["National Basketball Association"], [r"\bnba\b", r"national basketball association"]),
    ("nfl", "NFL", "football", "Every NFL game", ["National Football League"], [r"\bnfl\b", r"national football league"]),
    ("milb", "MiLB", "baseball", "Every Minor League Baseball game", ["Minor League Baseball"], [r"\bmilb\b", r"minor league baseball"]),
    ("ncaaf-fbs", "NCAA Football — Division I FBS", "football", "Football Bowl Subdivision games", ["FBS", "College Football", "NCAAF"], [r"\bfbs\b", r"football bowl subdivision", r"division i fbs", r"\bncaaf\b", r"college football", r"ncaa football"]),
    ("ncaaf-fcs", "NCAA Football — Division I FCS", "football", "Football Championship Subdivision games", ["FCS"], [r"\bfcs\b", r"football championship subdivision", r"division i fcs"]),
    ("ncaaf-d2", "NCAA Football — Division II", "football", "NCAA Division II football games", ["NCAA D2 Football"], [r"ncaa (?:division )?(?:ii|2) football", r"division (?:ii|2) football", r"d2 football"]),
    ("ncaaf-d3", "NCAA Football — Division III", "football", "NCAA Division III football games", ["NCAA D3 Football"], [r"ncaa (?:division )?(?:iii|3) football", r"division (?:iii|3) football", r"d3 football"]),
    ("naia-football", "NAIA Football", "football", "NAIA football games", ["National Association of Intercollegiate Athletics"], [r"\bnaia\b.*football", r"football.*\bnaia\b"]),
    ("njcaa-football", "NJCAA Football", "football", "Junior-college football games", ["JUCO Football", "Junior College Football"], [r"\bnjcaa\b.*football", r"junior college football", r"juco football"]),
    ("high-school-football", "High School Football", "football", "Showcases, championships, and all-star games", ["Prep Football", "HS Football"], [r"high school football", r"prep football", r"\bhs football\b", r"all[- ]american (?:football|bowl)", r"under armour all[- ]america"]),

    ("wnba", "WNBA", "basketball", "Every WNBA game", [], [r"\bwnba\b"]),
    ("nba-g-league", "NBA G League", "basketball", "NBA G League games", ["G League"], [r"nba g league", r"\bg league\b"]),
    ("ncaab-men", "NCAA Men’s Basketball", "basketball", "NCAA men’s basketball games", ["NCAAB", "College Basketball"], [r"ncaa men'?s basketball", r"men'?s college basketball", r"\bncaab\b", r"college basketball", r"ncaa basketball"]),
    ("ncaab-women", "NCAA Women’s Basketball", "basketball", "NCAA women’s basketball games", ["NCAAW", "Women’s College Basketball"], [r"ncaa women'?s basketball", r"women'?s college basketball", r"\bncaaw\b", r"\bwbb\b"]),
    ("international-basketball", "International Basketball", "basketball", "International leagues and tournaments", ["FIBA"], [r"\bfiba\b", r"international basketball"]),

    ("ncaa-baseball", "NCAA Baseball", "baseball", "College baseball games", ["College Baseball"], [r"ncaa baseball", r"college baseball"]),
    ("international-baseball", "International Baseball", "baseball", "International baseball and tournaments", ["World Baseball Classic", "WBC"], [r"world baseball classic", r"international baseball"]),

    ("ahl", "AHL", "hockey", "American Hockey League games", ["American Hockey League"], [r"\bahl\b", r"american hockey league"]),
    ("ncaa-hockey", "NCAA Hockey", "hockey", "College hockey games", ["College Hockey"], [r"ncaa hockey", r"college hockey"]),
    ("international-hockey", "International / Olympic Hockey", "hockey", "International and Olympic hockey", ["IIHF"], [r"\biihf\b", r"international hockey", r"olympic hockey"]),

    ("mls", "MLS", "soccer", "Major League Soccer matches", ["Major League Soccer"], [r"\bmls\b", r"major league soccer"]),
    ("nwsl", "NWSL", "soccer", "National Women’s Soccer League matches", [], [r"\bnwsl\b", r"national women'?s soccer league"]),
    ("premier-league", "Premier League", "soccer", "English Premier League matches", ["EPL"], [r"premier league", r"\bepl\b"]),
    ("la-liga", "La Liga", "soccer", "Spanish La Liga matches", [], [r"la liga"]),
    ("uefa-champions-league", "UEFA Champions League", "soccer", "UEFA Champions League matches", ["UCL"], [r"uefa champions league", r"\bucl\b"]),
    ("international-soccer", "International Soccer", "soccer", "National-team competitions and friendlies", ["FIFA", "World Cup"], [r"\bfifa\b", r"fifa world cup", r"(?:soccer|football) world cup", r"international (?:soccer|football)"]),

    ("cricket-test", "Test Cricket", "cricket", "International Test matches", ["Test Match"], [r"test cricket", r"test match", r"ashes series"]),
    ("cricket-odi", "ODI Cricket", "cricket", "One Day International matches", ["ODI"], [r"one day international", r"\bodi\b"]),
    ("cricket-t20", "T20 Cricket", "cricket", "T20 matches and competitions", ["Twenty20"], [r"\bt20\b", r"twenty20"]),
    ("cricket-ipl", "Indian Premier League", "cricket", "IPL cricket", ["IPL"], [r"indian premier league", r"\bipl\b"]),
    ("cricket-domestic", "Domestic Cricket", "cricket", "Domestic and franchise competitions", ["County Championship"], [r"county championship", r"domestic cricket"]),

    ("rugby-union-international", "International Rugby Union", "rugby-union", "International rugby union", ["Six Nations", "Rugby World Cup"], [r"six nations", r"rugby world cup", r"international rugby union"]),
    ("rugby-union-club", "Club Rugby Union", "rugby-union", "Club rugby union competitions", ["URC", "Super Rugby", "Premiership Rugby", "Top 14"], [r"united rugby championship", r"\burc\b", r"super rugby", r"premiership rugby", r"top 14"]),
    ("rugby-league-nrl", "NRL", "rugby-league", "National Rugby League", [], [r"national rugby league", r"\bnrl\b"]),
    ("rugby-league-super", "Super League Rugby", "rugby-league", "Super League rugby", [], [r"super league rugby"]),
    ("rugby-league-origin", "State of Origin", "rugby-league", "State of Origin series", [], [r"state of origin"]),

    ("world-curling", "World Curling", "curling", "World Curling events", [], [r"world curling"]),
    ("grand-slam-curling", "Grand Slam of Curling", "curling", "Grand Slam of Curling events", [], [r"grand slam of curling"]),
    ("national-curling", "National Curling Championships", "curling", "National curling championships", [], [r"national curling", r"curling championship"]),
    ("olympic-curling", "Olympic Curling", "curling", "Olympic curling events", [], [r"olympic curling"]),

    ("pga-tour", "PGA Tour", "golf", "PGA Tour events", [], [r"pga tour"]),
    ("lpga-tour", "LPGA Tour", "golf", "LPGA Tour events", [], [r"lpga tour", r"\blpga\b"]),
    ("liv-golf", "LIV Golf", "golf", "LIV Golf events", [], [r"liv golf"]),
    ("dp-world-tour", "DP World Tour", "golf", "DP World Tour events", ["European Tour"], [r"dp world tour", r"european tour golf"]),
    ("golf-majors", "Golf Majors", "golf", "Major golf championships", ["The Masters", "U.S. Open", "The Open", "PGA Championship"], [r"the masters", r"u\.?s\.? open golf", r"the open championship", r"pga championship"]),
    ("ncaa-golf", "NCAA Golf", "golf", "College golf", [], [r"ncaa golf", r"college golf"]),
    ("olympic-golf", "Olympic Golf", "golf", "Olympic golf", [], [r"olympic golf"]),

    ("world-athletics", "World Athletics", "track-field", "World Athletics events", [], [r"world athletics"]),
    ("diamond-league", "Diamond League", "track-field", "Diamond League meets", [], [r"diamond league"]),
    ("ncaa-track-field", "NCAA Track & Field", "track-field", "College track and field", [], [r"ncaa track", r"college track"]),
    ("national-track-field", "National Track & Field Championships", "track-field", "National championships", [], [r"national track.*championship", r"track.*national championship"]),
    ("olympic-track-field", "Olympic Track & Field", "track-field", "Olympic athletics", ["Olympic Athletics"], [r"olympic (?:track|athletics)"]),

    ("world-aquatics-swimming", "World Aquatics Swimming", "swimming", "World Aquatics swimming", [], [r"world aquatics.*swimming", r"world swimming championship"]),
    ("ncaa-swimming", "NCAA Swimming", "swimming", "College swimming", [], [r"ncaa swimming", r"college swimming"]),
    ("national-swimming", "National Swimming Championships", "swimming", "National championships", [], [r"national swimming championship"]),
    ("olympic-swimming", "Olympic Swimming", "swimming", "Olympic swimming", [], [r"olympic swimming"]),

    ("artistic-gymnastics", "Artistic Gymnastics", "gymnastics", "Artistic gymnastics", [], [r"artistic gymnastics"]),
    ("rhythmic-gymnastics", "Rhythmic Gymnastics", "gymnastics", "Rhythmic gymnastics", [], [r"rhythmic gymnastics"]),
    ("trampoline-gymnastics", "Trampoline Gymnastics", "gymnastics", "Trampoline gymnastics", [], [r"trampoline gymnastics"]),
    ("ncaa-gymnastics", "NCAA Gymnastics", "gymnastics", "College gymnastics", [], [r"ncaa gymnastics", r"college gymnastics"]),
    ("olympic-gymnastics", "Olympic Gymnastics", "gymnastics", "Olympic gymnastics", [], [r"olympic gymnastics"]),

    ("isu-figure-skating", "ISU Figure Skating", "figure-skating", "ISU competitions", [], [r"isu.*figure skating", r"figure skating grand prix"]),
    ("national-figure-skating", "National Figure Skating Championships", "figure-skating", "National championships", [], [r"national figure skating championship"]),
    ("olympic-figure-skating", "Olympic Figure Skating", "figure-skating", "Olympic figure skating", [], [r"olympic figure skating"]),
    ("long-track-speed-skating", "Long Track Speed Skating", "speed-skating", "Long-track speed skating", [], [r"long track speed skating"]),
    ("short-track-speed-skating", "Short Track Speed Skating", "speed-skating", "Short-track speed skating", [], [r"short track speed skating"]),
    ("olympic-speed-skating", "Olympic Speed Skating", "speed-skating", "Olympic speed skating", [], [r"olympic speed skating"]),

    ("alpine-skiing", "Alpine Skiing", "skiing", "Alpine skiing", [], [r"alpine skiing"]),
    ("cross-country-skiing", "Cross-Country Skiing", "skiing", "Cross-country skiing", [], [r"cross[- ]country skiing"]),
    ("freestyle-skiing", "Freestyle Skiing", "skiing", "Freestyle skiing", [], [r"freestyle skiing"]),
    ("ski-jumping", "Ski Jumping", "skiing", "Ski jumping", [], [r"ski jumping"]),
    ("nordic-combined", "Nordic Combined", "skiing", "Nordic combined", [], [r"nordic combined"]),
    ("olympic-skiing", "Olympic Skiing", "skiing", "Olympic skiing", [], [r"olympic skiing"]),
    ("snowboard-slopestyle", "Snowboard Slopestyle / Big Air", "snowboarding", "Slopestyle and big air", [], [r"snowboard.*(?:slopestyle|big air)"]),
    ("snowboard-halfpipe", "Snowboard Halfpipe", "snowboarding", "Halfpipe", [], [r"snowboard.*halfpipe"]),
    ("snowboard-cross", "Snowboard Cross", "snowboarding", "Boardercross", [], [r"snowboard cross", r"boardercross"]),
    ("olympic-snowboarding", "Olympic Snowboarding", "snowboarding", "Olympic snowboarding", [], [r"olympic snowboarding"]),
    ("bobsleigh", "Bobsleigh", "sliding-sports", "Bobsleigh", ["Bobsled"], [r"\bbobsle(?:d|igh)\b"]),
    ("skeleton", "Skeleton", "sliding-sports", "Skeleton", [], [r"\bskeleton\b"]),
    ("luge", "Luge", "sliding-sports", "Luge", [], [r"\bluge\b"]),

    ("formula-1", "Formula 1", "motorsports", "Formula 1 sessions and races", ["F1"], [r"formula\s*(?:1|one)", r"\bf1\b"]),
    ("formula-2", "Formula 2", "motorsports", "Formula 2 sessions and races", ["F2"], [r"formula\s*2", r"\bf2\b"]),
    ("formula-3", "Formula 3", "motorsports", "Formula 3 sessions and races", ["F3"], [r"formula\s*3", r"\bf3\b"]),
    ("formula-e", "Formula E", "motorsports", "Formula E sessions and races", [], [r"formula e"]),
    ("nascar-cup", "NASCAR Cup Series", "motorsports", "NASCAR Cup events", [], [r"nascar.*cup"]),
    ("nascar-xfinity", "NASCAR Xfinity Series", "motorsports", "NASCAR Xfinity events", [], [r"nascar.*xfinity"]),
    ("nascar-trucks", "NASCAR Truck Series", "motorsports", "NASCAR Truck events", ["Craftsman Truck Series"], [r"nascar.*(?:truck|craftsman)"]),
    ("indycar", "IndyCar", "motorsports", "IndyCar sessions and races", [], [r"indycar"]),
    ("imsa", "IMSA", "motorsports", "IMSA endurance racing", [], [r"\bimsa\b"]),
    ("wec", "FIA World Endurance Championship", "motorsports", "WEC endurance racing", ["WEC"], [r"world endurance championship", r"\bwec\b"]),
    ("motogp", "MotoGP", "motorsports", "MotoGP sessions and races", [], [r"motogp"]),
    ("superbike", "Superbike", "motorsports", "Superbike racing", ["WorldSBK"], [r"superbike", r"worldsbk"]),
    ("motocross", "Motocross", "motorsports", "Motocross events", [], [r"motocross"]),
    ("supercross", "Supercross", "motorsports", "Supercross events", [], [r"supercross"]),
    ("dirt-bike-racing", "Dirt Bike Racing", "motorsports", "Dirt-bike racing", [], [r"dirt bike racing", r"dirtbikes?"]),
    ("wrc", "WRC / Rally", "motorsports", "World Rally Championship", ["Rally"], [r"world rally championship", r"\bwrc\b"]),
    ("off-road-racing", "Off-Road Racing", "motorsports", "Off-road racing", [], [r"off[- ]road racing"]),
    ("nhra", "NHRA / Drag Racing", "motorsports", "Drag racing", [], [r"\bnhra\b", r"drag racing"]),
    ("monster-jam", "Monster Jam / Monster Trucks", "motorsports", "Monster-truck events", [], [r"monster jam", r"monster trucks?"]),

    ("ufc", "UFC", "mma", "UFC cards and related coverage", ["Fight Night"], [r"\bufc\b", r"ultimate fighting", r"fight night"]),
    ("pfl", "PFL / Bellator", "mma", "PFL and legacy Bellator listings", ["Professional Fighters League", "Bellator"], [r"\bpfl\b", r"professional fighters league", r"bellator"]),
    ("one-championship", "ONE Championship", "mma", "ONE Championship cards", [], [r"one championship"]),
    ("regional-mma", "Regional MMA", "mma", "Other regional MMA promotions", [], [r"regional mma", r"cage fighting"]),

    ("wwe", "WWE", "pro-wrestling", "WWE events", [], [r"\bwwe\b", r"wrestlemania", r"smackdown", r"monday night raw"]),
    ("aew", "AEW", "pro-wrestling", "AEW events", [], [r"\baew\b", r"all elite wrestling"]),
    ("tna", "TNA Wrestling", "pro-wrestling", "TNA events", ["Impact Wrestling"], [r"\btna\b", r"impact wrestling"]),
    ("njpw", "NJPW", "pro-wrestling", "New Japan Pro-Wrestling", [], [r"\bnjpw\b", r"new japan pro"]),
    ("roh", "ROH", "pro-wrestling", "Ring of Honor", [], [r"\broh\b", r"ring of honor"]),
    ("other-pro-wrestling", "Other Pro Wrestling", "pro-wrestling", "Other promotions", [], [r"professional wrestling", r"pro wrestling"]),
    ("ncaa-wrestling", "NCAA Wrestling", "wrestling", "College wrestling", [], [r"ncaa wrestling", r"college wrestling"]),
    ("freestyle-wrestling", "Freestyle Wrestling", "wrestling", "Freestyle wrestling", [], [r"freestyle wrestling"]),
    ("greco-roman-wrestling", "Greco-Roman Wrestling", "wrestling", "Greco-Roman wrestling", [], [r"greco[- ]roman"]),
    ("olympic-wrestling", "Olympic Wrestling", "wrestling", "Olympic wrestling", [], [r"olympic wrestling"]),

    ("pdc-darts", "PDC Darts", "darts", "Professional Darts Corporation", ["PDC"], [r"professional darts corporation", r"\bpdc\b"]),
    ("wdf-darts", "WDF Darts", "darts", "World Darts Federation", ["WDF"], [r"world darts federation", r"\bwdf\b"]),
    ("darts-events", "Darts Tours / Events", "darts", "World Matchplay, Premier League, and other events", [], [r"world matchplay", r"premier league darts"]),
    ("wsop", "World Series of Poker", "poker", "WSOP coverage", ["WSOP"], [r"world series of poker", r"\bwsop\b"]),
    ("wpt", "World Poker Tour", "poker", "WPT coverage", ["WPT"], [r"world poker tour", r"\bwpt\b"]),
    ("ept", "European Poker Tour", "poker", "EPT coverage", ["EPT"], [r"european poker tour", r"\bept\b"]),
    ("acl-cornhole", "American Cornhole League", "cornhole", "ACL events", ["ACL"], [r"american cornhole league", r"\bacl\b.*(?:cornhole|pro|open|teams|shootout|championship)"]),
    ("aco-cornhole", "American Cornhole Organization", "cornhole", "ACO events", ["ACO"], [r"american cornhole organization", r"\baco\b.*cornhole"]),
    ("college-cornhole", "College Cornhole", "cornhole", "College cornhole", [], [r"college cornhole"]),
    ("international-cornhole", "International Cornhole", "cornhole", "International and championship events", [], [r"international cornhole", r"cornhole championship"]),
    ("celebrity-cornhole", "Celebrity / TV Cornhole", "cornhole", "Made-for-TV and celebrity tournaments", [], [r"celebrity cornhole", r"cornhole.*(?:challenge|showdown)"]),

    ("tour-de-france", "Tour de France", "cycling", "Tour de France stages", [], [r"tour de france"]),
    ("giro-ditalia", "Giro d’Italia", "cycling", "Giro d’Italia stages", [], [r"giro d['’]?italia"]),
    ("vuelta-espana", "Vuelta a España", "cycling", "Vuelta a España stages", [], [r"vuelta a españa", r"vuelta a espana"]),
    ("tour-california", "Tour of California", "cycling", "Tour of California stages and legacy listings", [], [r"tour of california"]),
    ("road-cycling-classics", "Road Cycling Classics", "cycling", "One-day road classics", ["Paris-Roubaix", "Tour of Flanders"], [r"paris[- ]roubaix", r"tour of flanders", r"road cycling classic"]),
    ("cycling-world-championships", "Cycling World Championships", "cycling", "UCI world championships", ["UCI Worlds"], [r"uci.*world championship", r"cycling world championship"]),
    ("track-cycling", "Track Cycling", "cycling", "Track cycling", [], [r"track cycling"]),
    ("mountain-biking", "Mountain Biking", "cycling", "Mountain-bike racing", ["MTB"], [r"mountain bik(?:e|ing)", r"\bmtb\b"]),
    ("cyclocross", "Cyclocross", "cycling", "Cyclocross", [], [r"cyclocross"]),
    ("bmx-racing", "BMX Racing", "cycling", "BMX racing", [], [r"bmx racing"]),
    ("bmx-freestyle", "BMX Freestyle", "cycling", "BMX freestyle", [], [r"bmx freestyle"]),
    ("olympic-cycling", "Olympic Cycling", "cycling", "Olympic cycling disciplines", [], [r"olympic cycling"]),

    ("atp-tennis", "ATP Tour", "tennis", "ATP men’s tennis", [], [r"\batp\b", r"atp tour"]),
    ("wta-tennis", "WTA Tour", "tennis", "WTA women’s tennis", [], [r"\bwta\b", r"wta tour"]),
    ("tennis-grand-slams", "Tennis Grand Slams", "tennis", "Australian Open, Roland-Garros, Wimbledon, and US Open", ["Wimbledon", "US Open", "Australian Open", "French Open"], [r"wimbledon", r"australian open", r"roland[- ]garros", r"french open tennis", r"us open tennis"]),
    ("team-tennis", "International Team Tennis", "tennis", "Davis Cup and Billie Jean King Cup", ["Davis Cup", "BJK Cup"], [r"davis cup", r"billie jean king cup"]),
    ("ncaa-tennis", "NCAA Tennis", "tennis", "College tennis", [], [r"ncaa tennis", r"college tennis"]),
    ("olympic-tennis", "Olympic Tennis", "tennis", "Olympic tennis", [], [r"olympic tennis"]),

    ("international-volleyball", "International Volleyball", "volleyball", "International indoor volleyball", ["FIVB", "Volleyball Nations League"], [r"\bfivb\b", r"volleyball nations league", r"international volleyball"]),
    ("ncaa-volleyball", "NCAA Volleyball", "volleyball", "College volleyball", [], [r"ncaa volleyball", r"college volleyball"]),
    ("beach-volleyball", "Beach Volleyball", "volleyball", "Beach volleyball tours and events", [], [r"beach volleyball"]),
    ("olympic-volleyball", "Olympic Volleyball", "volleyball", "Olympic indoor and beach volleyball", [], [r"olympic.*volleyball"]),

    ("professional-boxing", "Professional Boxing", "boxing", "Professional boxing cards", [], [r"professional boxing", r"boxing.*(?:title|fight|card|championship)"]),
    ("olympic-boxing", "Olympic Boxing", "boxing", "Olympic boxing", [], [r"olympic boxing"]),
    ("ncaa-softball", "NCAA Softball", "softball", "College softball", [], [r"ncaa softball", r"college softball"]),
    ("professional-softball", "Professional Softball", "softball", "Professional softball", [], [r"professional softball", r"pro softball"]),
    ("international-softball", "International Softball", "softball", "International softball", [], [r"international softball", r"world baseball softball confederation", r"\bwbsc\b"]),
    ("nll", "National Lacrosse League", "lacrosse", "Indoor professional lacrosse", ["NLL"], [r"national lacrosse league", r"\bnll\b"]),
    ("pll", "Premier Lacrosse League", "lacrosse", "Outdoor professional lacrosse", ["PLL"], [r"premier lacrosse league", r"\bpll\b"]),
    ("ncaa-lacrosse", "NCAA Lacrosse", "lacrosse", "College lacrosse", [], [r"ncaa lacrosse", r"college lacrosse"]),
    ("triple-crown-racing", "U.S. Triple Crown", "horse-racing", "Kentucky Derby, Preakness, and Belmont Stakes", [], [r"kentucky derby", r"preakness stakes", r"belmont stakes"]),
    ("breeders-cup", "Breeders’ Cup", "horse-racing", "Breeders’ Cup racing", [], [r"breeders['’]? cup"]),
    ("international-horse-racing", "International Horse Racing", "horse-racing", "International racing meets", [], [r"international horse racing", r"royal ascot", r"melbourne cup"]),
    ("biathlon-world-cup", "Biathlon World Cup", "biathlon", "IBU World Cup events", ["IBU"], [r"biathlon world cup", r"\bibu\b"]),
    ("olympic-biathlon", "Olympic Biathlon", "biathlon", "Olympic biathlon", [], [r"olympic biathlon"]),

    ("world-aquatics-diving", "World Aquatics Diving", "diving", "International diving", [], [r"world aquatics.*diving", r"world diving"]),
    ("ncaa-diving", "NCAA Diving", "diving", "College diving", [], [r"ncaa diving", r"college diving"]),
    ("olympic-diving", "Olympic Diving", "diving", "Olympic diving", [], [r"olympic diving"]),
    ("world-aquatics-water-polo", "World Aquatics Water Polo", "water-polo", "International water polo", [], [r"world aquatics.*water polo", r"water polo world"]),
    ("ncaa-water-polo", "NCAA Water Polo", "water-polo", "College water polo", [], [r"ncaa water polo", r"college water polo"]),
    ("olympic-water-polo", "Olympic Water Polo", "water-polo", "Olympic water polo", [], [r"olympic water polo"]),
    ("world-artistic-swimming", "World Artistic Swimming", "artistic-swimming", "World Aquatics artistic swimming", [], [r"world aquatics.*artistic swimming", r"world artistic swimming"]),
    ("olympic-artistic-swimming", "Olympic Artistic Swimming", "artistic-swimming", "Olympic artistic swimming", [], [r"olympic artistic swimming"]),
    ("world-rowing", "World Rowing", "rowing", "World Rowing events", [], [r"world rowing"]),
    ("ncaa-rowing", "NCAA Rowing", "rowing", "College rowing", [], [r"ncaa rowing", r"college rowing"]),
    ("olympic-rowing", "Olympic Rowing", "rowing", "Olympic rowing", [], [r"olympic rowing"]),
    ("canoe-sprint", "Canoe Sprint", "canoe-kayak", "Canoe and kayak sprint", [], [r"canoe sprint", r"kayak sprint"]),
    ("canoe-slalom", "Canoe Slalom", "canoe-kayak", "Canoe and kayak slalom", [], [r"canoe slalom", r"kayak slalom"]),
    ("olympic-canoe-kayak", "Olympic Canoe / Kayak", "canoe-kayak", "Olympic paddle sports", [], [r"olympic.*(?:canoe|kayak)"]),
    ("world-sailing", "World Sailing", "sailing", "World Sailing events", [], [r"world sailing"]),
    ("olympic-sailing", "Olympic Sailing", "sailing", "Olympic sailing", [], [r"olympic sailing"]),
    ("world-triathlon", "World Triathlon", "triathlon", "World Triathlon series", [], [r"world triathlon"]),
    ("ironman-triathlon", "IRONMAN", "triathlon", "IRONMAN triathlon events", [], [r"ironman.*triathlon", r"ironman world championship"]),
    ("olympic-triathlon", "Olympic Triathlon", "triathlon", "Olympic triathlon", [], [r"olympic triathlon"]),
    ("world-archery", "World Archery", "archery", "World Archery events", [], [r"world archery"]),
    ("olympic-archery", "Olympic Archery", "archery", "Olympic archery", [], [r"olympic archery"]),
    ("issf-shooting", "ISSF Shooting", "shooting", "International sport shooting", ["ISSF"], [r"\bissf\b", r"world shooting championship"]),
    ("olympic-shooting", "Olympic Shooting", "shooting", "Olympic shooting", [], [r"olympic shooting"]),
    ("iwf-weightlifting", "IWF Weightlifting", "weightlifting", "International weightlifting", ["IWF"], [r"\biwf\b", r"world weightlifting"]),
    ("olympic-weightlifting", "Olympic Weightlifting", "weightlifting", "Olympic weightlifting", [], [r"olympic weightlifting"]),
    ("fei-equestrian", "FEI Equestrian", "equestrian", "International equestrian events", ["FEI"], [r"\bfei\b", r"equestrian.*(?:world|championship)"]),
    ("olympic-equestrian", "Olympic Equestrian", "equestrian", "Olympic equestrian", [], [r"olympic equestrian"]),
    ("international-handball", "International Handball", "handball", "IHF and EHF competitions", ["IHF", "EHF"], [r"\bihf\b", r"\behf\b", r"international handball"]),
    ("olympic-handball", "Olympic Handball", "handball", "Olympic handball", [], [r"olympic handball"]),
    ("international-field-hockey", "International Field Hockey", "field-hockey", "FIH competitions", ["FIH"], [r"\bfih\b", r"international field hockey"]),
    ("ncaa-field-hockey", "NCAA Field Hockey", "field-hockey", "College field hockey", [], [r"ncaa field hockey", r"college field hockey"]),
    ("olympic-field-hockey", "Olympic Field Hockey", "field-hockey", "Olympic field hockey", [], [r"olympic field hockey"]),
    ("bwf-badminton", "BWF Badminton", "badminton", "International badminton", ["BWF"], [r"\bbwf\b", r"world badminton"]),
    ("olympic-badminton", "Olympic Badminton", "badminton", "Olympic badminton", [], [r"olympic badminton"]),
    ("ittf-table-tennis", "ITTF / WTT Table Tennis", "table-tennis", "International table tennis", ["ITTF", "WTT"], [r"\bittf\b", r"world table tennis", r"\bwtt\b"]),
    ("olympic-table-tennis", "Olympic Table Tennis", "table-tennis", "Olympic table tennis", [], [r"olympic table tennis"]),
    ("fie-fencing", "FIE Fencing", "fencing", "International fencing", ["FIE"], [r"\bfie\b", r"world fencing"]),
    ("ncaa-fencing", "NCAA Fencing", "fencing", "College fencing", [], [r"ncaa fencing", r"college fencing"]),
    ("olympic-fencing", "Olympic Fencing", "fencing", "Olympic fencing", [], [r"olympic fencing"]),
    ("ijf-judo", "IJF Judo", "judo", "International judo", ["IJF"], [r"\bijf\b", r"world judo"]),
    ("olympic-judo", "Olympic Judo", "judo", "Olympic judo", [], [r"olympic judo"]),
    ("world-taekwondo", "World Taekwondo", "taekwondo", "International taekwondo", ["WT"], [r"world taekwondo"]),
    ("olympic-taekwondo", "Olympic Taekwondo", "taekwondo", "Olympic taekwondo", [], [r"olympic taekwondo"]),
    ("ifsc-climbing", "IFSC Sport Climbing", "sport-climbing", "International sport climbing", ["IFSC"], [r"\bifsc\b", r"world climbing"]),
    ("olympic-sport-climbing", "Olympic Sport Climbing", "sport-climbing", "Olympic sport climbing", [], [r"olympic sport climbing"]),
    ("wsl-surfing", "World Surf League", "surfing", "WSL surfing", ["WSL"], [r"world surf league", r"\bwsl\b.*surf"]),
    ("olympic-surfing", "Olympic Surfing", "surfing", "Olympic surfing", [], [r"olympic surfing"]),
    ("skateboard-street", "Skateboarding — Street", "skateboarding", "Street skateboarding", [], [r"skateboard.*street"]),
    ("skateboard-park", "Skateboarding — Park", "skateboarding", "Park skateboarding", [], [r"skateboard.*park"]),
    ("olympic-skateboarding", "Olympic Skateboarding", "skateboarding", "Olympic skateboarding", [], [r"olympic skateboarding"]),
    ("uipm-modern-pentathlon", "UIPM Modern Pentathlon", "modern-pentathlon", "International modern pentathlon", ["UIPM"], [r"\buipm\b", r"world modern pentathlon"]),
    ("olympic-modern-pentathlon", "Olympic Modern Pentathlon", "modern-pentathlon", "Olympic modern pentathlon", [], [r"olympic modern pentathlon"]),
    ("pba-bowling", "PBA Bowling", "bowling", "Professional Bowlers Association", ["PBA"], [r"professional bowlers association", r"\bpba\b.*bowling"]),
    ("pwba-bowling", "PWBA Bowling", "bowling", "Professional Women’s Bowling Association", ["PWBA"], [r"\bpwba\b"]),
    ("ncaa-bowling", "NCAA Bowling", "bowling", "College bowling", [], [r"ncaa bowling", r"college bowling"]),
    ("professional-pool", "Professional Pool", "billiards", "Professional pool tournaments", [], [r"professional pool", r"pool championship", r"nine[- ]ball"]),
    ("snooker", "Snooker", "billiards", "Professional snooker", [], [r"\bsnooker\b"]),
]

SPORT_NAMES = {sport_id: name for sport_id, name, _patterns in SPORT_DEFINITIONS}
SPORT_PATTERNS = {sport_id: patterns for sport_id, _name, patterns in SPORT_DEFINITIONS}
LEAGUE_NAMES = {league_id: name for league_id, name, _sport, _subtitle, _aliases, _patterns in LEAGUE_DEFINITIONS}
LEAGUE_SPORTS = {league_id: sport_id for league_id, _name, sport_id, _subtitle, _aliases, _patterns in LEAGUE_DEFINITIONS}
LEAGUE_PATTERNS = {league_id: patterns for league_id, _name, _sport, _subtitle, _aliases, patterns in LEAGUE_DEFINITIONS}
LEAGUE_BLOCK_ORDER = [league_id for league_id, *_rest in LEAGUE_DEFINITIONS]
LEAGUE_BLOCK_INDEX = {league_id: index for index, league_id in enumerate(LEAGUE_BLOCK_ORDER)}
LEAGUE_BLOCK_SIZE = 1000
OVERFLOW_BLOCK_OFFSET = 1_000_000

COLLEGE_FOOTBALL_LEAGUES = {
    "ncaaf-fbs", "ncaaf-fcs", "ncaaf-d2", "ncaaf-d3",
    "naia-football", "njcaa-football", "high-school-football",
}
TEAM_MATCHUP_LEAGUES = {
    "nfl", "mlb", "milb", "nba", "wnba", "nba-g-league",
    "nhl", "ahl", "ncaaf-fbs", "ncaaf-fcs", "ncaaf-d2", "ncaaf-d3",
    "naia-football", "njcaa-football", "high-school-football",
    "ncaab-men", "ncaab-women", "mls", "nwsl", "premier-league",
    "la-liga", "uefa-champions-league", "international-soccer",
    "ncaa-baseball", "international-baseball", "ncaa-hockey",
    "international-hockey", "international-basketball",
}

REPLAY_RE = re.compile(r"\b(replay|encore|classic|rewind|repeat)\b", re.I)
PREGAME_RE = re.compile(r"\b(pre[- ]?game|post[- ]?game|pregame|postgame)\b", re.I)
# When a schedule API supplies a canonical game ID/time, provider rows become
# candidate airings of that game.  A fairly generous window allows normal TV
# clock differences and delayed starts without letting a same-matchup replay
# several hours later become a second logical game.
SCHEDULE_API_LIVE_CANDIDATE_WINDOW = timedelta(hours=3)
SCHEDULE_API_MATCH_WINDOW = timedelta(hours=18)
SCHEDULE_API_SUPPORT_RE = re.compile(
    r"\b(?:pre[- ]?game|post[- ]?game|gameday|in-?game|squeeze[ -]?play|"
    r"bet(?:ting)?|wager(?:ing)?|odds|player props?|preview|recap|studio show)\b",
    re.I,
)
PLACEHOLDER_RE = re.compile(
    r"(?:^|\s)(?:zzz|tba|placeholder)(?:$|\s)|2098-12-31|^\s*$", re.I
)
# Deliberately narrow off-air filtering. These phrases are provider filler, not
# sporting events. Keep real adjacent programming (podcasts, studio shows,
# pregame coverage when enabled, etc.) eligible for normal sports matching.
CLEAR_OFF_AIR_RE = re.compile(
    r"(?:^|[\s|:—–-])(?:no[\W_]+(?:events?|game)[\W_]+today|signing[\W_]+off)[\s.!?]*$",
    re.I,
)
DATE_RE = re.compile(
    r"\((?P<date>\d{4}-\d{2}-\d{2})(?:\s+(?P<time>\d{2}:\d{2}(?::\d{2})?))?\)\s*$"
)
MATCHUP_RE = re.compile(
    r"(?P<left>[A-Za-z0-9À-ÿ .&'’/\-]+?)\s+(?P<op>@|at|vs\.?|versus)\s+(?P<right>[A-Za-z0-9À-ÿ .&'’/\-]+)$",
    re.I,
)
EVENT_VARIANT_RE = re.compile(
    r"\b(?:game|gm)\s*(?P<number>[12])\b|\b(?P<word>first|second)\s+game\b",
    re.I,
)
LEADING_TIME_RE = re.compile(r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)\b", re.I)

MLB_TEAMS = [
    ("arizona-diamondbacks", "Arizona Diamondbacks", ["ARI", "Diamondbacks", "D-backs", "Dbacks"]),
    ("atlanta-braves", "Atlanta Braves", ["ATL", "Braves"]),
    ("baltimore-orioles", "Baltimore Orioles", ["BAL", "Orioles", "O's"]),
    ("boston-red-sox", "Boston Red Sox", ["BOS", "Red Sox"]),
    ("chicago-cubs", "Chicago Cubs", ["CHC", "Cubs"]),
    ("chicago-white-sox", "Chicago White Sox", ["CHW", "CWS", "White Sox"]),
    ("cincinnati-reds", "Cincinnati Reds", ["CIN", "Reds"]),
    ("cleveland-guardians", "Cleveland Guardians", ["CLE", "Guardians"]),
    ("colorado-rockies", "Colorado Rockies", ["COL", "Rockies"]),
    ("detroit-tigers", "Detroit Tigers", ["DET", "Tigers"]),
    ("houston-astros", "Houston Astros", ["HOU", "Astros"]),
    ("kansas-city-royals", "Kansas City Royals", ["KC", "KCR", "Royals"]),
    ("los-angeles-angels", "Los Angeles Angels", ["LAA", "Angels"]),
    ("los-angeles-dodgers", "Los Angeles Dodgers", ["LAD", "Dodgers"]),
    ("miami-marlins", "Miami Marlins", ["MIA", "Marlins"]),
    ("milwaukee-brewers", "Milwaukee Brewers", ["MIL", "Brewers"]),
    ("minnesota-twins", "Minnesota Twins", ["MIN", "Twins"]),
    ("new-york-mets", "New York Mets", ["NYM", "Mets"]),
    ("new-york-yankees", "New York Yankees", ["NYY", "Yankees"]),
    ("oakland-athletics", "Oakland Athletics", ["ATH", "OAK", "Athletics", "A's"]),
    ("philadelphia-phillies", "Philadelphia Phillies", ["PHI", "Phillies"]),
    ("pittsburgh-pirates", "Pittsburgh Pirates", ["PIT", "Pirates"]),
    ("san-diego-padres", "San Diego Padres", ["SD", "SDP", "Padres"]),
    ("san-francisco-giants", "San Francisco Giants", ["SF", "SFG", "Giants"]),
    ("seattle-mariners", "Seattle Mariners", ["SEA", "Mariners"]),
    ("st-louis-cardinals", "St. Louis Cardinals", ["STL", "Cardinals"]),
    ("tampa-bay-rays", "Tampa Bay Rays", ["TB", "TBR", "Rays"]),
    ("texas-rangers", "Texas Rangers", ["TEX", "Rangers"]),
    ("toronto-blue-jays", "Toronto Blue Jays", ["TOR", "Blue Jays", "Jays"]),
    ("washington-nationals", "Washington Nationals", ["WSH", "WSN", "Nationals", "Nats"]),
]

MLB_ALIASES_BY_NAME = {
    _normalize_name: aliases
    for _slug_name, display_name, aliases in MLB_TEAMS
    for _normalize_name in [re.sub(r"[^a-z0-9]+", " ", display_name.lower()).strip()]
}


CONFERENCE_TEAMS = {
    "ncaaf:big-ten": [
        "Illinois", "Indiana", "Iowa", "Maryland", "Michigan", "Michigan State",
        "Minnesota", "Nebraska", "Northwestern", "Ohio State", "Oregon", "Penn State",
        "Purdue", "Rutgers", "UCLA", "USC", "Washington", "Wisconsin",
    ],
    "ncaaf:acc": [
        "Boston College", "California", "Clemson", "Duke", "Florida State",
        "Georgia Tech", "Louisville", "Miami", "North Carolina", "NC State",
        "Pittsburgh", "SMU", "Stanford", "Syracuse", "Virginia", "Virginia Tech",
        "Wake Forest",
    ],
    "ncaaf:sec": [
        "Alabama", "Arkansas", "Auburn", "Florida", "Georgia", "Kentucky", "LSU",
        "Mississippi", "Mississippi State", "Missouri", "Oklahoma", "South Carolina",
        "Tennessee", "Texas", "Texas A&M", "Vanderbilt",
    ],
}

SEED_CATALOG = []

for sport_id, sport_name, _patterns in SPORT_DEFINITIONS:
    SEED_CATALOG.append(
        (
            "sport",
            sport_id,
            sport_name,
            f"All {sport_name.lower()} events across child leagues, series, tours, and promotions",
            "",
            [],
            "",
            {"sport_id": sport_id, "family": sport_name, "kind": "sport"},
        )
    )

for league_id, name, sport_id, subtitle, aliases, _patterns in LEAGUE_DEFINITIONS:
    SEED_CATALOG.append(
        (
            "league",
            league_id,
            name,
            subtitle,
            league_id,
            aliases,
            "",
            {
                "sport_id": sport_id,
                "family": SPORT_NAMES.get(sport_id, sport_id),
                "kind": "league",
                "block_index": LEAGUE_BLOCK_INDEX[league_id],
            },
        )
    )

SEED_CATALOG.extend(
    [
        (
            "conference",
            "ncaaf-fbs:big-ten",
            "Big Ten Football",
            "FBS games with at least one Big Ten team",
            "ncaaf-fbs",
            ["Big Ten", "B1G"],
            "",
            {"teams": CONFERENCE_TEAMS["ncaaf:big-ten"], "sport_id": "football", "family": "Football"},
        ),
        (
            "conference",
            "ncaaf-fbs:acc",
            "ACC Football",
            "FBS games with at least one ACC team",
            "ncaaf-fbs",
            ["ACC", "Atlantic Coast Conference"],
            "",
            {"teams": CONFERENCE_TEAMS["ncaaf:acc"], "sport_id": "football", "family": "Football"},
        ),
        (
            "conference",
            "ncaaf-fbs:sec",
            "SEC Football",
            "FBS games with at least one SEC team",
            "ncaaf-fbs",
            ["SEC", "Southeastern Conference"],
            "",
            {"teams": CONFERENCE_TEAMS["ncaaf:sec"], "sport_id": "football", "family": "Football"},
        ),
    ]
)

SEED_CATALOG.extend(
    (
        "team",
        f"mlb:{slug}",
        display_name,
        "MLB team • home and away games",
        "mlb",
        [display_name, *aliases],
        "",
        {"sport_id": "baseball", "family": "Baseball"},
    )
    for slug, display_name, aliases in MLB_TEAMS
)


LEGACY_DEMO_RULES = {
    ("league", "nfl"),
    ("team", "mlb:philadelphia-phillies"),
    ("conference", "ncaaf:big-ten"),
    ("sport", "cornhole"),
}

TEAM_FEED_PATTERNS = [
    ("mlb", re.compile(r"^MLB\s+(?!NETWORK\b|STRIKE\b)(?P<team>.+?)\s*$", re.I)),
    ("nfl", re.compile(r"^NFL\s*\|\s*(?P<team>.+?)(?:\s+(?:HD|SD|FHD))?\s*$", re.I)),
    ("nba", re.compile(r"^NBA\s*\|\s*(?P<team>.+?)(?:\s+(?:HD|SD|FHD))?\s*$", re.I)),
    ("nhl", re.compile(r"^NHL\s*:\s*(?P<team>.+?)(?:\s+(?:HD|SD|FHD))?\s*$", re.I)),
    ("wnba", re.compile(r"^WNBA\s*\|\s*(?P<team>.+?)(?:\s+(?:HD|SD|FHD))?\s*$", re.I)),
]

NETWORK_WORDS = {
    "espn", "espn2", "espnu", "fox", "fs1", "fs2", "cbs", "cbssn", "nbc",
    "tnt", "tbs", "abc", "apple", "prime", "network", "redzone", "strike zone",
}



__all__ = [name for name in globals() if name.isupper()]
