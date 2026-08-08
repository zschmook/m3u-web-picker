from __future__ import annotations

import gzip
import io
import json
import re
import sqlite3
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from time import perf_counter
from typing import Callable, Iterable
from xml.etree import ElementTree
from zoneinfo import ZoneInfo


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
    # Optional authoritative schedule source. The API key itself is stored in
    # an internal sports_settings row and is never returned to the browser.
    "schedule_api_enabled": False,
    "schedule_api_url": "",
}

SCHEDULE_API_KEY_SETTING = "__schedule_api_key"
SCHEDULE_API_SOURCE = "api-sports-baseball"
SCHEDULE_API_LEAGUE_ID = "mlb"
SCHEDULE_API_REMOTE_LEAGUE_ID = 1
SCHEDULE_API_PROVIDER_NAME = "API-BASEBALL"

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
    r"\b(?:pre[- ]?game|post[- ]?game|gameday|in[- ]game|squeeze[ -]?play|"
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

MAX_MALFORMED_SAMPLES = 10

XMLTV_GENERATOR_NAME = "M3U Web Picker Sports Automation"
GUIDE_PREGAME_HOURS = 24
GUIDE_POSTGAME_HOURS = 2
SPORTS_DISABLED_CACHE_HOURS = 24
SPORTS_DISABLED_AT_KEY = "__sports_disabled_at"
SPORTS_INTERVAL_ANCHOR_KEY = "__sports_interval_anchor_at"
SCHEDULE_MODES = {"daily", "interval"}
MIN_INTERVAL_HOURS = 1
MAX_INTERVAL_HOURS = 24
ESTIMATED_EVENT_HOURS = {
    "mlb": 4, "milb": 4, "ncaa-baseball": 4, "international-baseball": 4,
    "nfl": 4, "ncaaf-fbs": 4, "ncaaf-fcs": 4, "ncaaf-d2": 4,
    "ncaaf-d3": 4, "naia-football": 4, "njcaa-football": 4,
    "high-school-football": 4,
    "nba": 3, "wnba": 3, "nba-g-league": 3, "ncaab-men": 3,
    "ncaab-women": 3, "international-basketball": 3,
    "nhl": 3, "ahl": 3, "ncaa-hockey": 3, "international-hockey": 3,
    "mls": 3, "nwsl": 3, "premier-league": 3, "la-liga": 3,
    "uefa-champions-league": 3, "international-soccer": 3,
    "cricket-test": 8, "cricket-odi": 8, "cricket-t20": 5,
    "cricket-ipl": 5, "cricket-domestic": 8,
    "rugby-union-international": 3, "rugby-union-club": 3,
    "rugby-league-nrl": 3, "rugby-league-super": 3, "rugby-league-origin": 3,
    "poker": 8, "wsop": 8, "wpt": 8, "ept": 8,
    "golf": 8, "pga-tour": 8, "lpga-tour": 8, "liv-golf": 8,
    "dp-world-tour": 8, "golf-majors": 8,
    "cycling": 6, "tour-de-france": 6, "giro-ditalia": 6,
    "vuelta-espana": 6, "tour-california": 6,
}



class MalformedSportsEntry(ValueError):
    """A provider entry contains bad event data and may be skipped safely."""


class ScanCancelled(RuntimeError):
    """A manual sports scan was cancelled at a safe checkpoint."""


CancelCheck = Callable[[], bool] | None
EVENT_END_GRACE = timedelta(minutes=90)
EVENT_MERGE_TOLERANCE = timedelta(minutes=90)
REPLAY_ATTACH_WINDOW = timedelta(hours=24)
# Provider guides often schedule the live game in the evening and then repeat
# it after midnight. Treat noon as the broadcast-day boundary so those
# overnight airings remain attached to the prior evening's logical game,
# while the next evening's actual game lands in a new bucket.
LOGICAL_EVENT_DAY_ROLLOVER_HOUR = 12
MAX_ESTIMATED_EVENT_DURATION = timedelta(
    hours=max(ESTIMATED_EVENT_HOURS.values(), default=8)
)


def _raise_if_cancelled(cancel_check: CancelCheck) -> None:
    if cancel_check and cancel_check():
        raise ScanCancelled("Sports update cancelled. Existing sports channels were kept.")


def _new_scan_diagnostics() -> dict:
    return {
        "malformed_m3u": 0,
        "malformed_epg": 0,
        "samples": [],
    }


def _record_malformed_entry(
    diagnostics: dict,
    *,
    source: str,
    label: str,
    exc: Exception,
) -> None:
    key = f"malformed_{source}"
    diagnostics[key] = int(diagnostics.get(key, 0)) + 1
    samples = diagnostics.setdefault("samples", [])
    if len(samples) < MAX_MALFORMED_SAMPLES:
        clean_label = re.sub(r"\s+", " ", str(label or "unnamed entry")).strip()
        samples.append(
            {
                "source": source.upper(),
                "label": clean_label[:180],
                "error": f"{type(exc).__name__}: {exc}"[:240],
            }
        )


def _malformed_count(diagnostics: dict) -> int:
    return int(diagnostics.get("malformed_m3u", 0)) + int(
        diagnostics.get("malformed_epg", 0)
    )


def _log_malformed_summary(diagnostics: dict) -> None:
    count = _malformed_count(diagnostics)
    if not count:
        return
    print(f"Sports scan skipped {count} malformed provider entr{'y' if count == 1 else 'ies'}.")
    for sample in diagnostics.get("samples", []):
        print(
            "  - "
            f"{sample.get('source', 'SOURCE')} entry "
            f"{sample.get('label', 'unnamed entry')!r}: "
            f"{sample.get('error', 'invalid data')}"
        )


def _connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _normalize(value: str) -> str:
    value = value.replace("&", " and ").replace("’", "'")
    value = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def _smart_team_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value.strip(" |:-"))
    if not value:
        return value
    if value.isupper():
        value = value.title()
    replacements = {
        "76Ers": "76ers",
        "Fc": "FC",
        "Sc": "SC",
        "Ucla": "UCLA",
        "Usc": "USC",
        "Lsu": "LSU",
        "Smu": "SMU",
    }
    for before, after in replacements.items():
        value = re.sub(rf"\b{re.escape(before)}\b", after, value)
    return value


def _json_load(value: str, fallback):
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _canonical_refresh_time(value, *, fallback: str | None = None) -> str:
    """Return HH:MM or raise a user-facing configuration error.

    The AM/PM parser exists only to safely migrate values produced by an older
    build. New writes are always stored as 24-hour HH:MM strings.
    """
    text = str(value or "").strip().upper()
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p"):
        try:
            parsed = datetime.strptime(text, fmt)
            return f"{parsed.hour:02d}:{parsed.minute:02d}"
        except ValueError:
            pass
    if fallback is not None:
        return fallback
    raise ValueError("Refresh time must be a valid time such as 03:00.")


def _refresh_time_parts(settings: dict) -> tuple[int, int]:
    value = settings.get("refresh_time")
    if value not in (None, ""):
        canonical = _canonical_refresh_time(value, fallback="03:00")
        hour, minute = canonical.split(":", 1)
        return int(hour), int(minute)

    # Backward-compatible read of databases created before refresh_time existed.
    try:
        hour = int(settings.get("refresh_hour", 3))
        minute = int(settings.get("refresh_minute", 0))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (TypeError, ValueError):
        pass
    return 3, 0


def init_db(db_path: Path | str) -> None:
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sports_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sports_schedule_api_cache (
                source TEXT NOT NULL,
                league_id TEXT NOT NULL,
                season INTEGER NOT NULL,
                schedule_date TEXT NOT NULL,
                fetched_on TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                result_count INTEGER NOT NULL DEFAULT 0,
                remaining_quota INTEGER,
                PRIMARY KEY (source, league_id, season, schedule_date)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sports_schedule_events (
                source TEXT NOT NULL,
                api_event_id TEXT NOT NULL,
                league_id TEXT NOT NULL,
                season INTEGER NOT NULL,
                schedule_date TEXT NOT NULL,
                scheduled_start TEXT NOT NULL,
                status_short TEXT NOT NULL DEFAULT '',
                status_long TEXT NOT NULL DEFAULT '',
                home_api_id TEXT NOT NULL DEFAULT '',
                home_name TEXT NOT NULL DEFAULT '',
                home_logo TEXT NOT NULL DEFAULT '',
                away_api_id TEXT NOT NULL DEFAULT '',
                away_name TEXT NOT NULL DEFAULT '',
                away_logo TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL DEFAULT '{}',
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (source, api_event_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sports_catalog (
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                subtitle TEXT NOT NULL DEFAULT '',
                league_id TEXT NOT NULL DEFAULT '',
                aliases_json TEXT NOT NULL DEFAULT '[]',
                logo_url TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                source TEXT NOT NULL DEFAULT 'seed',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (scope_type, scope_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sports_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                feed_preference TEXT NOT NULL DEFAULT 'best',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(scope_type, scope_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sports_generated (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_key TEXT NOT NULL UNIQUE,
                source_channel_key TEXT NOT NULL,
                event_key TEXT NOT NULL,
                league_id TEXT NOT NULL DEFAULT '',
                display_name TEXT NOT NULL,
                subtitle TEXT NOT NULL DEFAULT '',
                feed_type TEXT NOT NULL DEFAULT 'event',
                assigned_number INTEGER NOT NULL,
                group_title TEXT NOT NULL,
                url TEXT NOT NULL,
                tvg_id TEXT NOT NULL DEFAULT '',
                source_tvg_id TEXT NOT NULL DEFAULT '',
                tvg_logo TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL,
                event_title TEXT NOT NULL DEFAULT '',
                event_start TEXT,
                event_end TEXT,
                is_replay INTEGER NOT NULL DEFAULT 0,
                epg_programme_json TEXT NOT NULL DEFAULT '{}',
                generated_at TEXT NOT NULL
            )
            """
        )
        # Add guide-related columns when upgrading an existing sports database.
        for column_name, column_sql in (
            ("source_tvg_id", "TEXT NOT NULL DEFAULT ''"),
            ("event_title", "TEXT NOT NULL DEFAULT ''"),
            ("event_end", "TEXT"),
            ("is_replay", "INTEGER NOT NULL DEFAULT 0"),
            ("epg_programme_json", "TEXT NOT NULL DEFAULT '{}'"),
        ):
            try:
                conn.execute(
                    f"ALTER TABLE sports_generated ADD COLUMN {column_name} {column_sql}"
                )
            except sqlite3.OperationalError:
                pass

        # Existing v20.3 rows inherited provider tvg-id values. Upgrade them
        # once so every generated feed has its own stable XMLTV identity.
        guide_migration_key = "migration_generated_xmltv_ids_v20_4"
        guide_migrated = conn.execute(
            "SELECT 1 FROM sports_settings WHERE key = ?",
            (guide_migration_key,),
        ).fetchone()
        if not guide_migrated:
            legacy_rows = conn.execute(
                """
                SELECT id, event_key, feed_type, source_channel_key, display_name,
                       assigned_number, tvg_id, source_tvg_id, raw_json, league_id,
                       event_start, event_title, event_end
                FROM sports_generated
                """
            ).fetchall()
            for legacy_row in legacy_rows:
                old_tvg_id = str(legacy_row["tvg_id"] or "")
                new_tvg_id = old_tvg_id
                source_tvg_id = str(legacy_row["source_tvg_id"] or "")
                if not old_tvg_id.startswith("m3u-picker.sports."):
                    source_tvg_id = source_tvg_id or old_tvg_id
                    new_tvg_id = _generated_tvg_id(int(legacy_row["assigned_number"]))
                raw = _json_load(legacy_row["raw_json"], [])
                if raw and new_tvg_id != old_tvg_id:
                    raw[0] = _rewrite_extinf(
                        raw[0],
                        {"tvg-id": new_tvg_id},
                        str(legacy_row["display_name"] or "Sports event"),
                    )
                event_title = str(legacy_row["event_title"] or "").strip()
                if not event_title:
                    event_title = re.sub(
                        r"\s+—\s+[^—]+$",
                        "",
                        re.sub(
                            r"^[^•]+•\s*",
                            "",
                            str(legacy_row["display_name"] or "Sports event"),
                        ),
                    ).strip()
                event_end = legacy_row["event_end"]
                if not event_end and legacy_row["event_start"]:
                    try:
                        event_end = (
                            datetime.fromisoformat(legacy_row["event_start"])
                            + _event_duration(str(legacy_row["league_id"] or ""))
                        ).isoformat()
                    except (TypeError, ValueError, OverflowError):
                        event_end = None
                conn.execute(
                    """
                    UPDATE sports_generated
                    SET tvg_id = ?, source_tvg_id = ?, raw_json = ?,
                        event_title = ?, event_end = ?
                    WHERE id = ?
                    """,
                    (
                        new_tvg_id,
                        source_tvg_id,
                        json.dumps(raw),
                        event_title,
                        event_end,
                        legacy_row["id"],
                    ),
                )
            conn.execute(
                "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                (guide_migration_key, json.dumps(True)),
            )

        # Temporary sports channels reuse fixed numbered slots. Keep the XMLTV
        # identity tied to that slot rather than to a changing event or stream
        # URL so Jellyfin does not lose its guide mapping after each daily scan.
        slot_guide_migration_key = "migration_generated_xmltv_slot_ids_v20_7"
        slot_guide_migrated = conn.execute(
            "SELECT 1 FROM sports_settings WHERE key = ?",
            (slot_guide_migration_key,),
        ).fetchone()
        if not slot_guide_migrated:
            slot_rows = conn.execute(
                """
                SELECT id, assigned_number, display_name, tvg_id, raw_json
                FROM sports_generated
                """
            ).fetchall()
            for slot_row in slot_rows:
                new_tvg_id = _generated_tvg_id(int(slot_row["assigned_number"]))
                raw = _json_load(slot_row["raw_json"], [])
                if raw:
                    raw[0] = _rewrite_extinf(
                        raw[0],
                        {"tvg-id": new_tvg_id},
                        str(slot_row["display_name"] or "Sports event"),
                    )
                conn.execute(
                    """
                    UPDATE sports_generated
                    SET tvg_id = ?, raw_json = ?
                    WHERE id = ?
                    """,
                    (new_tvg_id, json.dumps(raw), slot_row["id"]),
                )
            conn.execute(
                "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                (slot_guide_migration_key, json.dumps(True)),
            )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sports_scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                event_count INTEGER NOT NULL DEFAULT 0,
                channel_count INTEGER NOT NULL DEFAULT 0,
                target_date TEXT,
                trigger TEXT NOT NULL DEFAULT 'manual'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sports_scan_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                running INTEGER NOT NULL DEFAULT 0,
                started_at TEXT,
                updated_at TEXT,
                stage TEXT NOT NULL DEFAULT '',
                trigger TEXT NOT NULL DEFAULT 'manual'
            )
            """
        )

        # Migrate the old split hour/minute settings before inserting defaults.
        refresh_row = conn.execute(
            "SELECT value FROM sports_settings WHERE key = 'refresh_time'"
        ).fetchone()
        if refresh_row is None:
            legacy = {
                row["key"]: _json_load(row["value"], row["value"])
                for row in conn.execute(
                    "SELECT key, value FROM sports_settings "
                    "WHERE key IN ('refresh_hour', 'refresh_minute')"
                )
            }
            hour, minute = _refresh_time_parts(legacy)
            conn.execute(
                "INSERT INTO sports_settings(key, value) VALUES ('refresh_time', ?)",
                (json.dumps(f"{hour:02d}:{minute:02d}"),),
            )

        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO sports_settings(key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )

        # An upgrade may already contain generated rows while the master switch
        # is off. Start the same 24-hour recovery window instead of retaining
        # that hidden cache forever.
        enabled_row = conn.execute(
            "SELECT value FROM sports_settings WHERE key = 'enabled'"
        ).fetchone()
        disabled_at_row = conn.execute(
            "SELECT 1 FROM sports_settings WHERE key = ?",
            (SPORTS_DISABLED_AT_KEY,),
        ).fetchone()
        generated_count = int(
            conn.execute("SELECT COUNT(*) FROM sports_generated").fetchone()[0]
        )
        enabled_value = bool(
            _json_load(enabled_row["value"], False) if enabled_row else False
        )
        if not enabled_value and generated_count and not disabled_at_row:
            conn.execute(
                "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                (SPORTS_DISABLED_AT_KEY, json.dumps(_now_iso())),
            )

        now = _now_iso()
        for scope_type, scope_id, name, subtitle, league_id, aliases, logo, metadata in SEED_CATALOG:
            conn.execute(
                """
                INSERT INTO sports_catalog
                    (scope_type, scope_id, display_name, subtitle, league_id,
                     aliases_json, logo_url, metadata_json, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'seed', ?)
                ON CONFLICT(scope_type, scope_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    subtitle = excluded.subtitle,
                    league_id = excluded.league_id,
                    aliases_json = excluded.aliases_json,
                    logo_url = CASE
                        WHEN sports_catalog.logo_url = '' THEN excluded.logo_url
                        ELSE sports_catalog.logo_url
                    END,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                WHERE sports_catalog.source = 'seed'
                """,
                (
                    scope_type,
                    scope_id,
                    name,
                    subtitle,
                    league_id,
                    json.dumps(aliases),
                    logo,
                    json.dumps(metadata),
                    now,
                ),
            )

        # Remove seed choices retired or renamed by the v21 taxonomy while
        # leaving provider-discovered catalog entries untouched.
        valid_seed_keys = {(row[0], row[1]) for row in SEED_CATALOG}
        stale_seed_rows = conn.execute(
            "SELECT scope_type, scope_id FROM sports_catalog WHERE source = 'seed'"
        ).fetchall()
        for stale_row in stale_seed_rows:
            key = (stale_row["scope_type"], stale_row["scope_id"])
            if key not in valid_seed_keys:
                conn.execute(
                    "DELETE FROM sports_catalog WHERE scope_type = ? AND scope_id = ? AND source = 'seed'",
                    key,
                )

        # Preserve older user selections while moving them onto the expanded
        # sport/league model. This migration is idempotent and never adds a
        # selection the user did not already have.
        taxonomy_migration_key = "migration_sports_taxonomy_v21"
        taxonomy_migrated = conn.execute(
            "SELECT 1 FROM sports_settings WHERE key = ?",
            (taxonomy_migration_key,),
        ).fetchone()
        if not taxonomy_migrated:
            legacy_rule_moves = {
                ("sport", "formula-1"): ("league", "formula-1"),
                ("sport", "ufc"): ("league", "ufc"),
                ("league", "ncaaf"): ("league", "ncaaf-fbs"),
                ("league", "ncaab"): ("league", "ncaab-men"),
                ("conference", "ncaaf:big-ten"): ("conference", "ncaaf-fbs:big-ten"),
                ("conference", "ncaaf:acc"): ("conference", "ncaaf-fbs:acc"),
                ("conference", "ncaaf:sec"): ("conference", "ncaaf-fbs:sec"),
            }
            for old_key, new_key in legacy_rule_moves.items():
                row = conn.execute(
                    """
                    SELECT display_name, feed_preference, enabled, created_at, updated_at
                    FROM sports_rules WHERE scope_type = ? AND scope_id = ?
                    """,
                    old_key,
                ).fetchone()
                if not row:
                    continue
                catalog_row = conn.execute(
                    "SELECT display_name FROM sports_catalog WHERE scope_type = ? AND scope_id = ?",
                    new_key,
                ).fetchone()
                display_name = catalog_row["display_name"] if catalog_row else row["display_name"]
                conn.execute(
                    """
                    INSERT OR IGNORE INTO sports_rules
                        (scope_type, scope_id, display_name, feed_preference, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*new_key, display_name, row["feed_preference"], row["enabled"], row["created_at"], row["updated_at"]),
                )
                conn.execute(
                    "DELETE FROM sports_rules WHERE scope_type = ? AND scope_id = ?",
                    old_key,
                )
            conn.execute(
                "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                (taxonomy_migration_key, json.dumps(True)),
            )

        # v20.1 inserted four demonstration rules. Remove that exact untouched
        # set once during migration, while preserving any customized or expanded
        # rule list. Fresh installs intentionally start with zero selections.
        migration_key = "migration_removed_v20_1_demo_rules"
        migrated = conn.execute(
            "SELECT 1 FROM sports_settings WHERE key = ?", (migration_key,)
        ).fetchone()
        if not migrated:
            rows = conn.execute(
                """
                SELECT scope_type, scope_id, created_at, updated_at
                FROM sports_rules
                """
            ).fetchall()
            signature = {(row["scope_type"], row["scope_id"]) for row in rows}
            untouched = all(row["created_at"] == row["updated_at"] for row in rows)
            if len(rows) == len(LEGACY_DEMO_RULES) and signature == LEGACY_DEMO_RULES and untouched:
                conn.execute("DELETE FROM sports_rules")
            conn.execute(
                "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                (migration_key, json.dumps(True)),
            )

        # Catalog entries are choices, not assumptions about what the user follows.
        conn.commit()


def get_settings(db_path: Path | str) -> dict:
    init_db(db_path)
    output = dict(DEFAULT_SETTINGS)
    with closing(_connect(db_path)) as conn:
        for row in conn.execute("SELECT key, value FROM sports_settings"):
            if str(row["key"]).startswith("__"):
                continue
            output[row["key"]] = _json_load(row["value"], row["value"])

    hour, minute = _refresh_time_parts(output)
    output["refresh_time"] = f"{hour:02d}:{minute:02d}"
    mode = str(output.get("schedule_mode", "daily") or "daily").strip().lower()
    output["schedule_mode"] = mode if mode in SCHEDULE_MODES else "daily"
    try:
        interval_hours = int(output.get("interval_hours", 2))
    except (TypeError, ValueError):
        interval_hours = 2
    output["interval_hours"] = min(MAX_INTERVAL_HOURS, max(MIN_INTERVAL_HOURS, interval_hours))
    # Do not expose obsolete or internal keys back to the browser.
    output.pop("refresh_hour", None)
    output.pop("refresh_minute", None)
    return output


def _disabled_at_from_conn(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute(
        "SELECT value FROM sports_settings WHERE key = ?",
        (SPORTS_DISABLED_AT_KEY,),
    ).fetchone()
    if not row:
        return None
    value = _json_load(row["value"], row["value"])
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


def disabled_cache_status(db_path: Path | str, now: datetime | None = None) -> dict:
    """Return credential-free state for the 24-hour disabled sports cache."""
    init_db(db_path)
    current = (now or datetime.now().astimezone()).astimezone()
    with closing(_connect(db_path)) as conn:
        count = int(conn.execute("SELECT COUNT(*) FROM sports_generated").fetchone()[0])
        disabled_at = _disabled_at_from_conn(conn)
    settings = get_settings(db_path)
    if settings.get("enabled") or not disabled_at:
        return {
            "count": count,
            "disabled_at": None,
            "expires_at": None,
            "expired": False,
        }
    expires_at = disabled_at + timedelta(hours=SPORTS_DISABLED_CACHE_HOURS)
    return {
        "count": count,
        "disabled_at": disabled_at.isoformat(timespec="seconds"),
        "expires_at": expires_at.isoformat(timespec="seconds"),
        "expired": current >= expires_at,
    }


def purge_expired_disabled_cache(db_path: Path | str, now: datetime | None = None) -> bool:
    """Purge cached generated rows after sports has been disabled for 24 hours."""
    init_db(db_path)
    current = (now or datetime.now().astimezone()).astimezone()
    settings = get_settings(db_path)
    if settings.get("enabled"):
        return False
    with closing(_connect(db_path)) as conn:
        disabled_at = _disabled_at_from_conn(conn)
        if not disabled_at or current < disabled_at + timedelta(hours=SPORTS_DISABLED_CACHE_HOURS):
            return False
        deleted = conn.execute("DELETE FROM sports_generated").rowcount
        conn.commit()
    return bool(deleted)


def clear_generated_channels(db_path: Path | str) -> int:
    """Remove all currently generated sports channels for a source reset."""
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        deleted = conn.execute("DELETE FROM sports_generated").rowcount
        conn.commit()
    return int(deleted or 0)


def update_settings(db_path: Path | str, changes: dict) -> dict:
    previous_settings = get_settings(db_path)
    previous_enabled = bool(previous_settings.get("enabled"))
    allowed = set(DEFAULT_SETTINGS)
    clean = {key: value for key, value in changes.items() if key in allowed}

    for key in (
        "enabled",
        "auto_update",
        "everything_mode",
        "include_replays",
        "include_pregame",
        "use_backup_feeds",
        "exclude_sd",
        "schedule_api_enabled",
    ):
        if key in clean:
            clean[key] = bool(clean[key])
    if "start_channel" in clean:
        try:
            clean["start_channel"] = max(1, int(clean["start_channel"]))
        except (TypeError, ValueError) as exc:
            raise ValueError("Starting channel must be a positive whole number.") from exc
    if "channels_per_event" in clean:
        try:
            clean["channels_per_event"] = min(50, max(1, int(clean["channels_per_event"])))
        except (TypeError, ValueError) as exc:
            raise ValueError("Channels per event must be a whole number from 1 to 50.") from exc
    if "refresh_time" in clean:
        clean["refresh_time"] = _canonical_refresh_time(clean["refresh_time"])
    if "schedule_mode" in clean:
        clean["schedule_mode"] = str(clean["schedule_mode"] or "").strip().lower()
        if clean["schedule_mode"] not in SCHEDULE_MODES:
            raise ValueError("Update schedule must be Daily or Every X hours.")
    if "interval_hours" in clean:
        try:
            clean["interval_hours"] = int(clean["interval_hours"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Update interval must be a whole number from 1 to 24 hours.") from exc
        if not MIN_INTERVAL_HOURS <= clean["interval_hours"] <= MAX_INTERVAL_HOURS:
            raise ValueError("Update interval must be a whole number from 1 to 24 hours.")

    # Accept the old split API fields for one release so an older open browser
    # tab cannot corrupt the scheduler. They are immediately converted to HH:MM.
    if "refresh_time" not in clean and ("refresh_hour" in changes or "refresh_minute" in changes):
        current_hour, current_minute = _refresh_time_parts(get_settings(db_path))
        try:
            hour = int(changes.get("refresh_hour", current_hour))
            minute = int(changes.get("refresh_minute", current_minute))
        except (TypeError, ValueError) as exc:
            raise ValueError("Refresh time must be a valid time such as 03:00.") from exc
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("Refresh time must be a valid time such as 03:00.")
        clean["refresh_time"] = f"{hour:02d}:{minute:02d}"

    if "group_title" in clean:
        clean["group_title"] = str(clean["group_title"]).strip()[:120] or "Sports Today"
    if "timezone" in clean:
        clean["timezone"] = str(clean["timezone"]).strip()
        try:
            ZoneInfo(clean["timezone"])
        except Exception as exc:
            raise ValueError("Choose a valid timezone.") from exc
    if "event_window" in clean and clean["event_window"] not in {"today", "today_tomorrow", "next_24_hours"}:
        clean["event_window"] = "today"

    effective_settings = dict(previous_settings)
    effective_settings.update(clean)
    schedule_changed = any(
        key in clean and clean[key] != previous_settings.get(key)
        for key in ("schedule_mode", "interval_hours")
    )

    with closing(_connect(db_path)) as conn:
        for key, value in clean.items():
            conn.execute(
                "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )

        # When interval mode is selected before any scan has completed, this
        # stable anchor prevents every status poll from moving the next run.
        # Once a scan finishes, its finished_at timestamp becomes the anchor.
        if schedule_changed and effective_settings.get("schedule_mode") == "interval":
            conn.execute(
                "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                (SPORTS_INTERVAL_ANCHOR_KEY, json.dumps(_now_iso())),
            )
        elif effective_settings.get("schedule_mode") != "interval":
            conn.execute(
                "DELETE FROM sports_settings WHERE key = ?",
                (SPORTS_INTERVAL_ANCHOR_KEY,),
            )

        if "enabled" in clean and bool(clean["enabled"]) != previous_enabled:
            if clean["enabled"]:
                disabled_at = _disabled_at_from_conn(conn)
                if disabled_at and datetime.now().astimezone() >= disabled_at + timedelta(hours=SPORTS_DISABLED_CACHE_HOURS):
                    conn.execute("DELETE FROM sports_generated")
                conn.execute(
                    "DELETE FROM sports_settings WHERE key = ?",
                    (SPORTS_DISABLED_AT_KEY,),
                )
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                    (SPORTS_DISABLED_AT_KEY, json.dumps(_now_iso())),
                )

        # Retire the legacy split values after any successful write.
        conn.execute("DELETE FROM sports_settings WHERE key IN ('refresh_hour', 'refresh_minute')")
        conn.commit()
    return get_settings(db_path)


def _catalog_rows(db_path: Path | str, scope_type: str = "") -> list[dict]:
    init_db(db_path)
    sql = """
        SELECT scope_type, scope_id, display_name, subtitle, league_id,
               aliases_json, logo_url, metadata_json, source, updated_at
        FROM sports_catalog
    """
    params: tuple = ()
    if scope_type in SCOPE_TYPES:
        sql += " WHERE scope_type = ?"
        params = (scope_type,)
    sql += " ORDER BY scope_type, display_name COLLATE NOCASE"
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(sql, params).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        item["id"] = item.pop("scope_id")
        item["name"] = item.pop("display_name")
        item["aliases"] = _json_load(item.pop("aliases_json"), [])
        item["metadata"] = _json_load(item.pop("metadata_json"), {})
        league_id = str(item.get("league_id", "") or "")
        sport_id = LEAGUE_SPORTS.get(league_id, "")
        if sport_id:
            item["metadata"].setdefault("sport_id", sport_id)
            item["metadata"].setdefault("family", SPORT_NAMES.get(sport_id, sport_id))
        if league_id in LEAGUE_BLOCK_INDEX:
            item["metadata"].setdefault("block_index", LEAGUE_BLOCK_INDEX[league_id])
        output.append(item)
    return output


def catalog_payload(db_path: Path | str, query: str = "", scope_type: str = "") -> list[dict]:
    query_norm = _normalize(query)
    output = []
    for item in _catalog_rows(db_path, scope_type):
        haystack = _normalize(
            " ".join(
                [
                    item["name"],
                    item["subtitle"],
                    item["id"],
                    " ".join(item["aliases"]),
                ]
            )
        )
        if not query_norm or query_norm in haystack:
            output.append(item)
    return output


def _upsert_catalog_item(
    conn: sqlite3.Connection,
    *,
    scope_type: str,
    scope_id: str,
    display_name: str,
    subtitle: str,
    league_id: str,
    aliases: list[str],
    logo_url: str,
    metadata: dict,
    source: str,
) -> None:
    conn.execute(
        """
        INSERT INTO sports_catalog
            (scope_type, scope_id, display_name, subtitle, league_id,
             aliases_json, logo_url, metadata_json, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scope_type, scope_id) DO UPDATE SET
            display_name = excluded.display_name,
            subtitle = excluded.subtitle,
            league_id = excluded.league_id,
            aliases_json = excluded.aliases_json,
            logo_url = CASE
                WHEN excluded.logo_url <> '' THEN excluded.logo_url
                ELSE sports_catalog.logo_url
            END,
            metadata_json = excluded.metadata_json,
            source = excluded.source,
            updated_at = excluded.updated_at
        """,
        (
            scope_type,
            scope_id,
            display_name,
            subtitle,
            league_id,
            json.dumps(sorted(set(alias for alias in aliases if alias))),
            logo_url,
            json.dumps(metadata),
            source,
            _now_iso(),
        ),
    )


def _team_feed_identity(channel: dict) -> tuple[str, str, str] | None:
    name = str(channel.get("name", "")).strip()
    for league_id, pattern in TEAM_FEED_PATTERNS:
        match = pattern.match(name)
        if not match:
            continue
        team = _smart_team_name(match.group("team"))
        normalized = _normalize(team)
        if not normalized or any(word in normalized for word in NETWORK_WORDS):
            continue
        if re.fullmatch(r"\d+|\d+\s*(am|pm)?", normalized):
            continue
        return league_id, f"{league_id}:{_slug(team)}", team
    return None


def _known_mlb_aliases(team_name: str) -> list[str]:
    return list(MLB_ALIASES_BY_NAME.get(_normalize(team_name), []))


def discover_catalog_from_channels(db_path: Path | str, channels: Iterable[dict]) -> int:
    """Cache provider-discovered team names and logos in SQLite."""
    init_db(db_path)
    discovered: dict[tuple[str, str], tuple[str, list[str], str]] = {}
    for channel in channels:
        identity = _team_feed_identity(channel)
        if not identity:
            continue
        league_id, team_id, team_name = identity
        aliases = [team_name]
        if league_id == "mlb":
            aliases.extend(_known_mlb_aliases(team_name))
        words = team_name.split()
        if len(words) >= 2:
            aliases.append(words[-1])
            aliases.append(" ".join(words[-2:]))
        logo = str(channel.get("tvg_logo", "") or "")
        discovered[(league_id, team_id)] = (team_name, aliases, logo)

    with closing(_connect(db_path)) as conn:
        for (league_id, team_id), (name, aliases, logo) in discovered.items():
            _upsert_catalog_item(
                conn,
                scope_type="team",
                scope_id=team_id,
                display_name=name,
                subtitle=f"{LEAGUE_NAMES.get(league_id, league_id.upper())} team • home and away games",
                league_id=league_id,
                aliases=aliases,
                logo_url=logo,
                metadata={
                    "sport_id": LEAGUE_SPORTS.get(league_id, ""),
                    "family": SPORT_NAMES.get(LEAGUE_SPORTS.get(league_id, ""), league_id.upper()),
                },
                source="provider",
            )
        conn.commit()
    return len(discovered)


def get_rules(db_path: Path | str) -> list[dict]:
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT id, scope_type, scope_id, display_name,
                   feed_preference, enabled, created_at, updated_at
            FROM sports_rules
            ORDER BY id
            """
        ).fetchall()
    return [dict(row) | {"enabled": bool(row["enabled"])} for row in rows]


def add_rules(db_path: Path | str, payloads: list[dict]) -> list[dict]:
    if not isinstance(payloads, list) or not payloads:
        raise ValueError("Choose at least one sports selection.")
    if len(payloads) > 100:
        raise ValueError("Add no more than 100 sports selections at once.")

    catalog = {
        (item["scope_type"], item["id"]): item
        for item in catalog_payload(db_path)
    }
    prepared = []
    for payload in payloads:
        scope_type = str(payload.get("scope_type", "")).strip().lower()
        scope_id = str(payload.get("scope_id", "")).strip().lower()
        preference = str(payload.get("feed_preference", "best")).strip().lower() or "best"
        if scope_type not in SCOPE_TYPES or not scope_id:
            raise ValueError("Choose a valid sports selection.")
        item = catalog.get((scope_type, scope_id))
        if not item:
            raise ValueError("One of the selected items is not in the cached sports catalog.")
        if preference not in {"best", "all", "favorite", "home", "away", "national"}:
            preference = "best"
        prepared.append((scope_type, scope_id, item["name"], preference))

    now = _now_iso()
    with closing(_connect(db_path)) as conn:
        for scope_type, scope_id, display_name, preference in prepared:
            conn.execute(
                """
                INSERT INTO sports_rules
                    (scope_type, scope_id, display_name, feed_preference,
                     enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(scope_type, scope_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    feed_preference = excluded.feed_preference,
                    enabled = 1,
                    updated_at = excluded.updated_at
                """,
                (scope_type, scope_id, display_name, preference, now, now),
            )
        conn.commit()
    return get_rules(db_path)


def add_rule(db_path: Path | str, payload: dict) -> dict:
    rules = add_rules(db_path, [payload])
    scope_type = str(payload.get("scope_type", "")).strip().lower()
    scope_id = str(payload.get("scope_id", "")).strip().lower()
    return next(
        rule for rule in rules
        if rule["scope_type"] == scope_type and rule["scope_id"] == scope_id
    )


def update_rule(db_path: Path | str, rule_id: int, payload: dict) -> dict:
    fields = []
    values = []
    if "feed_preference" in payload:
        preference = str(payload["feed_preference"]).strip().lower()
        if preference not in {"best", "all", "favorite", "home", "away", "national"}:
            preference = "best"
        fields.append("feed_preference = ?")
        values.append(preference)
    if "enabled" in payload:
        fields.append("enabled = ?")
        values.append(1 if payload["enabled"] else 0)
    if not fields:
        raise ValueError("Nothing to update.")
    fields.append("updated_at = ?")
    values.append(_now_iso())
    values.append(int(rule_id))
    with closing(_connect(db_path)) as conn:
        conn.execute(
            f"UPDATE sports_rules SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        conn.commit()
    return next((rule for rule in get_rules(db_path) if rule["id"] == int(rule_id)), {})


def delete_rule(db_path: Path | str, rule_id: int) -> bool:
    with closing(_connect(db_path)) as conn:
        cursor = conn.execute("DELETE FROM sports_rules WHERE id = ?", (int(rule_id),))
        conn.commit()
        return cursor.rowcount > 0


def _sports_day(now: datetime, settings: dict) -> date:
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    local_now = now.astimezone(timezone)
    refresh_hour, refresh_minute = _refresh_time_parts(settings)
    refresh = dt_time(refresh_hour, refresh_minute)
    if local_now.time().replace(tzinfo=None) < refresh:
        return local_now.date() - timedelta(days=1)
    return local_now.date()


def _target_window(now: datetime, settings: dict) -> tuple[datetime, datetime, date]:
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    local_now = now.astimezone(timezone)
    sports_day = _sports_day(now, settings)
    boundary = datetime.combine(
        sports_day,
        dt_time(*_refresh_time_parts(settings)),
        tzinfo=timezone,
    )
    mode = settings.get("event_window", "today")
    if mode == "next_24_hours":
        return local_now, local_now + timedelta(hours=24), sports_day
    if mode == "today_tomorrow":
        return boundary, boundary + timedelta(days=2), sports_day
    return boundary, boundary + timedelta(days=1), sports_day


def _parse_scheduled_datetime(value, timezone: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def _interval_anchor_at(
    db_path: Path | str,
    *,
    timezone: ZoneInfo,
    fallback: datetime,
) -> datetime:
    # Every completed attempt resets the interval, including manual runs,
    # failures, and cancellations. This prevents failed scans from retrying on
    # every 30-second scheduler wake-up.
    last = last_scan(db_path)
    finished = _parse_scheduled_datetime(last.get("finished_at") if last else None, timezone)
    if finished is not None:
        return finished

    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT value FROM sports_settings WHERE key = ?",
            (SPORTS_INTERVAL_ANCHOR_KEY,),
        ).fetchone()
        stored = _json_load(row["value"], row["value"]) if row else None
        anchor = _parse_scheduled_datetime(stored, timezone)
        if anchor is None:
            anchor = fallback
            conn.execute(
                "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                (SPORTS_INTERVAL_ANCHOR_KEY, json.dumps(anchor.isoformat(timespec="seconds"))),
            )
            conn.commit()
    return anchor


def next_update_at(db_path: Path | str, now: datetime | None = None) -> datetime:
    settings = get_settings(db_path)
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    local_now = (now or datetime.now().astimezone()).astimezone(timezone)

    if settings.get("schedule_mode") == "interval":
        anchor = _interval_anchor_at(
            db_path,
            timezone=timezone,
            fallback=local_now,
        )
        return anchor + timedelta(hours=int(settings.get("interval_hours", 2)))

    refresh_hour, refresh_minute = _refresh_time_parts(settings)
    target = local_now.replace(
        hour=refresh_hour,
        minute=refresh_minute,
        second=0,
        microsecond=0,
    )
    if target <= local_now:
        target += timedelta(days=1)
    return target



def _schedule_api_secret(db_path: Path | str) -> str:
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT value FROM sports_settings WHERE key = ?",
            (SCHEDULE_API_KEY_SETTING,),
        ).fetchone()
    if not row:
        return ""
    return str(_json_load(row["value"], row["value"]) or "").strip()


def schedule_api_status(db_path: Path | str) -> dict:
    """Return credential-free schedule API state for the browser."""
    settings = get_settings(db_path)
    api_key = _schedule_api_secret(db_path)
    url = str(settings.get("schedule_api_url", "") or "").strip()
    enabled = bool(settings.get("schedule_api_enabled"))
    with closing(_connect(db_path)) as conn:
        last = conn.execute(
            """
            SELECT schedule_date, fetched_at, result_count, remaining_quota
            FROM sports_schedule_api_cache
            WHERE source = ? AND league_id = ?
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (SCHEDULE_API_SOURCE, SCHEDULE_API_LEAGUE_ID),
        ).fetchone()
        cached_dates = [
            row["schedule_date"]
            for row in conn.execute(
                """
                SELECT schedule_date
                FROM sports_schedule_api_cache
                WHERE source = ? AND league_id = ?
                ORDER BY schedule_date
                """,
                (SCHEDULE_API_SOURCE, SCHEDULE_API_LEAGUE_ID),
            ).fetchall()
        ]
        cached_event_count = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM sports_schedule_events
                WHERE source = ? AND league_id = ?
                """,
                (SCHEDULE_API_SOURCE, SCHEDULE_API_LEAGUE_ID),
            ).fetchone()[0]
        )
    configured = bool(url and api_key)
    effective = bool(enabled and configured)
    api_entry = None
    if url or api_key:
        api_entry = {
            "id": SCHEDULE_API_SOURCE,
            "provider": SCHEDULE_API_PROVIDER_NAME,
            "scope": "MLB",
            "url": url,
            "enabled": enabled,
            "configured": configured,
            "effective": effective,
            "key_configured": bool(api_key),
            "last_fetch_at": last["fetched_at"] if last else None,
            "last_result_count": int(last["result_count"] or 0) if last else 0,
            "cached_event_count": cached_event_count,
            "remaining_quota": last["remaining_quota"] if last else None,
        }
    return {
        "enabled": enabled,
        "configured": configured,
        "effective": effective,
        "provider": SCHEDULE_API_PROVIDER_NAME,
        "league": "MLB",
        "url": url,
        "key_configured": bool(api_key),
        "last_fetch_at": last["fetched_at"] if last else None,
        "last_fetch_date": last["schedule_date"] if last else None,
        "last_result_count": int(last["result_count"] or 0) if last else 0,
        "cached_event_count": cached_event_count,
        "remaining_quota": last["remaining_quota"] if last else None,
        "cached_dates": cached_dates,
        "fallback_mode": not effective,
        "apis": [api_entry] if api_entry else [],
    }


def update_schedule_api_config(
    db_path: Path | str,
    *,
    enabled: bool | None = None,
    url: str | None = None,
    api_key: str | None = None,
    clear_key: bool = False,
) -> dict:
    """Persist the optional schedule source without exposing its API key."""
    init_db(db_path)
    changes = {}
    if enabled is not None:
        changes["schedule_api_enabled"] = bool(enabled)
    if url is not None:
        cleaned_url = str(url or "").strip().rstrip("/")
        if cleaned_url and not re.match(r"^https?://", cleaned_url, re.I):
            raise ValueError("Schedule API URL must start with http:// or https://.")
        changes["schedule_api_url"] = cleaned_url
    if changes:
        update_settings(db_path, changes)
    with closing(_connect(db_path)) as conn:
        if clear_key:
            conn.execute("DELETE FROM sports_settings WHERE key = ?", (SCHEDULE_API_KEY_SETTING,))
        elif api_key is not None and str(api_key).strip():
            conn.execute(
                "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                (SCHEDULE_API_KEY_SETTING, json.dumps(str(api_key).strip())),
            )
        conn.commit()
    return schedule_api_status(db_path)


def _schedule_api_required_dates(scan_anchor: datetime, settings: dict) -> list[date]:
    window_start, window_end, _sports_date = _target_window(scan_anchor, settings)
    last_instant = window_end - timedelta(microseconds=1)
    current = window_start.date()
    end_date = last_instant.date()
    values = []
    while current <= end_date:
        values.append(current)
        current += timedelta(days=1)
    return values


def _schedule_api_games_url(base_url: str, *, schedule_date: date, season: int, timezone: str) -> str:
    parsed = urllib.parse.urlparse(str(base_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Schedule API URL is invalid.")
    path = parsed.path.rstrip("/")
    if not path.endswith("/games"):
        path = f"{path}/games" if path else "/games"
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    # API-BASEBALL's free endpoint is most reliable with date + timezone.
    # Fetch the day's baseball slate once and filter MLB (league id 1) locally.
    # This also leaves the cached response strategy reusable for other baseball
    # leagues without spending one request per league.
    query.pop("league", None)
    query.pop("season", None)
    query.update(
        {
            "date": [schedule_date.isoformat()],
            "timezone": [timezone],
        }
    )
    encoded = urllib.parse.urlencode(query, doseq=True)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", encoded, ""))


def _fetch_schedule_api_date(
    db_path: Path | str,
    *,
    base_url: str,
    api_key: str,
    schedule_date: date,
    season: int,
    timezone: str,
    fetched_on: str,
    cancel_check: CancelCheck = None,
) -> dict:
    _raise_if_cancelled(cancel_check)
    url = _schedule_api_games_url(
        base_url,
        schedule_date=schedule_date,
        season=season,
        timezone=timezone,
    )
    request = urllib.request.Request(
        url,
        headers={
            "x-apisports-key": api_key,
            "Accept": "application/json",
            "User-Agent": "M3U-Web-Picker/22.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("Schedule API response exceeded the 8 MB safety limit.")
            remaining_header = response.headers.get("x-ratelimit-requests-remaining")
    except Exception as exc:
        raise ValueError(f"Could not fetch MLB schedule for {schedule_date.isoformat()}.") from exc
    _raise_if_cancelled(cancel_check)
    try:
        payload = json.loads(raw.decode("utf-8-sig", errors="replace"))
    except Exception as exc:
        raise ValueError("Schedule API returned invalid JSON.") from exc
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if errors:
        raise ValueError("Schedule API reported an error.")
    games = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(games, list):
        raise ValueError("Schedule API did not return a games list.")
    fetched_at = _now_iso()
    remaining = None
    try:
        if remaining_header is not None:
            remaining = int(remaining_header)
    except (TypeError, ValueError):
        remaining = None
    with closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            DELETE FROM sports_schedule_events
            WHERE source = ? AND league_id = ? AND season = ? AND schedule_date = ?
            """,
            (SCHEDULE_API_SOURCE, SCHEDULE_API_LEAGUE_ID, season, schedule_date.isoformat()),
        )
        stored = 0
        for game in games:
            if not isinstance(game, dict):
                continue
            league = game.get("league") or {}
            if int(league.get("id") or 0) != SCHEDULE_API_REMOTE_LEAGUE_ID:
                continue
            event_id = str(game.get("id") or "").strip()
            scheduled_start = str(game.get("date") or "").strip()
            if not event_id or not scheduled_start:
                continue
            teams = game.get("teams") or {}
            home = teams.get("home") or {}
            away = teams.get("away") or {}
            status = game.get("status") or {}
            conn.execute(
                """
                INSERT OR REPLACE INTO sports_schedule_events
                    (source, api_event_id, league_id, season, schedule_date,
                     scheduled_start, status_short, status_long,
                     home_api_id, home_name, home_logo,
                     away_api_id, away_name, away_logo,
                     raw_json, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    SCHEDULE_API_SOURCE,
                    event_id,
                    SCHEDULE_API_LEAGUE_ID,
                    season,
                    schedule_date.isoformat(),
                    scheduled_start,
                    str(status.get("short") or ""),
                    str(status.get("long") or ""),
                    str(home.get("id") or ""),
                    str(home.get("name") or ""),
                    str(home.get("logo") or ""),
                    str(away.get("id") or ""),
                    str(away.get("name") or ""),
                    str(away.get("logo") or ""),
                    json.dumps(game, separators=(",", ":")),
                    fetched_at,
                ),
            )
            stored += 1
        conn.execute(
            """
            INSERT OR REPLACE INTO sports_schedule_api_cache
                (source, league_id, season, schedule_date, fetched_on,
                 fetched_at, result_count, remaining_quota)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                SCHEDULE_API_SOURCE,
                SCHEDULE_API_LEAGUE_ID,
                season,
                schedule_date.isoformat(),
                fetched_on,
                fetched_at,
                stored,
                remaining,
            ),
        )
        conn.commit()
    return {
        "date": schedule_date.isoformat(),
        "games": stored,
        "remaining_quota": remaining,
        "fetched_at": fetched_at,
    }


def refresh_schedule_api_if_due(
    db_path: Path | str,
    scan_anchor: datetime | None = None,
    *,
    force: bool = False,
    cancel_check: CancelCheck = None,
) -> dict:
    """Refresh MLB schedule cache at most once per local day unless forced.

    This is intentionally the first step in a Sports Automation cycle. If the
    optional API is disabled, blank, unavailable, or over quota, the normal
    provider-derived matching path remains available.
    """
    settings = get_settings(db_path)
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    local_now = (scan_anchor or datetime.now().astimezone()).astimezone(timezone)
    state = schedule_api_status(db_path)
    if not state.get("effective"):
        return {"enabled": False, "used": False, "fetched": [], "cached": [], "warning": ""}
    api_key = _schedule_api_secret(db_path)
    base_url = str(settings.get("schedule_api_url", "") or "").strip()
    season = local_now.year
    required_dates = _schedule_api_required_dates(local_now, settings)
    fetched_on = local_now.date().isoformat()
    fetched = []
    cached = []
    warning = ""
    for schedule_date in required_dates:
        _raise_if_cancelled(cancel_check)
        with closing(_connect(db_path)) as conn:
            row = conn.execute(
                """
                SELECT fetched_on FROM sports_schedule_api_cache
                WHERE source = ? AND league_id = ? AND season = ? AND schedule_date = ?
                """,
                (
                    SCHEDULE_API_SOURCE,
                    SCHEDULE_API_LEAGUE_ID,
                    season,
                    schedule_date.isoformat(),
                ),
            ).fetchone()
        if not force and row and str(row["fetched_on"] or "") == fetched_on:
            cached.append(schedule_date.isoformat())
            continue
        try:
            fetched.append(
                _fetch_schedule_api_date(
                    db_path,
                    base_url=base_url,
                    api_key=api_key,
                    schedule_date=schedule_date,
                    season=season,
                    timezone=str(settings.get("timezone", "America/New_York")),
                    fetched_on=fetched_on,
                    cancel_check=cancel_check,
                )
            )
        except ValueError as exc:
            # A schedule service outage must not take the user's television
            # lineup down. Cached canonical events are used when available;
            # otherwise the legacy provider-discovery path remains authoritative.
            warning = str(exc)
            cached.append(schedule_date.isoformat())
    with closing(_connect(db_path)) as conn:
        available = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM sports_schedule_events
                WHERE source = ? AND league_id = ? AND season = ?
                """,
                (SCHEDULE_API_SOURCE, SCHEDULE_API_LEAGUE_ID, season),
            ).fetchone()[0]
        )
    return {
        "enabled": True,
        "used": available > 0,
        "fetched": fetched,
        "cached": cached,
        "warning": warning,
        "canonical_events_available": available,
    }


def schedule_api_events_for_window(
    db_path: Path | str,
    scan_anchor: datetime | None = None,
) -> list[dict]:
    settings = get_settings(db_path)
    if not schedule_api_status(db_path).get("effective"):
        return []
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    local_now = (scan_anchor or datetime.now().astimezone()).astimezone(timezone)
    window_start, window_end, _ = _target_window(local_now, settings)
    required_dates = [value.isoformat() for value in _schedule_api_required_dates(local_now, settings)]
    if not required_dates:
        return []
    placeholders = ",".join("?" for _ in required_dates)
    with closing(_connect(db_path)) as conn:
        # Only dates that belong to the current Sports Automation window can
        # supply canonical anchors. This prevents an older cached matchup from
        # hijacking a new provider event when today's optional API fetch is
        # unavailable. Missing dates simply use the legacy provider-derived path.
        rows = conn.execute(
            f"""
            SELECT e.*
            FROM sports_schedule_events e
            INNER JOIN sports_schedule_api_cache c
              ON c.source = e.source
             AND c.league_id = e.league_id
             AND c.season = e.season
             AND c.schedule_date = e.schedule_date
            WHERE e.source = ? AND e.league_id = ?
              AND e.season IN (?, ?)
              AND e.schedule_date IN ({placeholders})
            ORDER BY e.scheduled_start
            """,
            (
                SCHEDULE_API_SOURCE,
                SCHEDULE_API_LEAGUE_ID,
                local_now.year - 1,
                local_now.year,
                *required_dates,
            ),
        ).fetchall()
    output = []
    for row in rows:
        try:
            start = datetime.fromisoformat(str(row["scheduled_start"]))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone)
            start = start.astimezone(timezone)
        except Exception:
            continue
        # Keep a generous edge around the current window so replay/time-shifted
        # provider rows can still be assigned to the correct canonical game.
        if not (window_start - timedelta(hours=18) <= start < window_end + timedelta(hours=18)):
            continue
        output.append({
            "api_source": str(row["source"]),
            "api_event_id": str(row["api_event_id"]),
            "league_id": SCHEDULE_API_LEAGUE_ID,
            "season": int(row["season"]),
            "scheduled_start": start,
            "status_short": str(row["status_short"] or ""),
            "status_long": str(row["status_long"] or ""),
            "home_api_id": str(row["home_api_id"] or ""),
            "home_name": str(row["home_name"] or ""),
            "home_logo": str(row["home_logo"] or ""),
            "away_api_id": str(row["away_api_id"] or ""),
            "away_name": str(row["away_name"] or ""),
            "away_logo": str(row["away_logo"] or ""),
        })
    return output


def should_run_scheduled(db_path: Path | str, now: datetime | None = None) -> bool:
    settings = get_settings(db_path)
    if not settings.get("enabled") or not settings.get("auto_update"):
        return False
    if scan_state(db_path, now).get("running"):
        return False

    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    local_now = (now or datetime.now().astimezone()).astimezone(timezone)

    if settings.get("schedule_mode") == "interval":
        return local_now >= next_update_at(db_path, local_now)

    refresh_hour, refresh_minute = _refresh_time_parts(settings)
    if (local_now.hour, local_now.minute) != (refresh_hour, refresh_minute):
        return False

    last = last_scan(db_path)
    if not last:
        return True

    # A successful manual scan before the refresh boundary can target yesterday's
    # sports day, so compare target dates rather than calendar dates.
    target_date = _sports_day(local_now, settings).isoformat()
    if last.get("status") == "success" and last.get("target_date") == target_date:
        return False

    # The scheduler wakes every 30 seconds. Do not retry a failed scheduled scan
    # twice during the same configured minute and hammer the provider.
    if last.get("trigger") == "scheduled":
        attempted = _parse_scheduled_datetime(last.get("started_at"), timezone)
        if attempted and (
            attempted.date() == local_now.date()
            and (attempted.hour, attempted.minute) == (refresh_hour, refresh_minute)
        ):
            return False
    return True


def derive_xmltv_url(source_url: str) -> str:
    """Derive a conventional Xtream XMLTV URL without exposing credentials."""
    if not source_url:
        return ""
    parsed = urllib.parse.urlparse(source_url)
    query = urllib.parse.parse_qs(parsed.query)
    username = query.get("username", [""])[-1]
    password = query.get("password", [""])[-1]
    if not parsed.scheme or not parsed.netloc or not username or not password:
        return ""
    encoded = urllib.parse.urlencode({"username": username, "password": password})
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, "/xmltv.php", "", encoded, "")
    )


def refresh_epg_cache(
    source_url: str,
    cache_path: Path,
    timeout: int = 120,
    cancel_check: CancelCheck = None,
) -> tuple[bool, str]:
    xmltv_url = derive_xmltv_url(source_url)
    if not xmltv_url:
        return False, "No Xtream XMLTV URL could be derived."

    try:
        raw = download_xmltv_bytes(
            xmltv_url,
            timeout=timeout,
            cancel_check=cancel_check,
        )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temp_path.write_bytes(raw)
        temp_path.replace(cache_path)
        return True, f"Cached {len(raw)} bytes of XMLTV data."
    except ScanCancelled:
        raise
    except Exception as exc:
        return False, f"EPG refresh failed: {exc}"


def download_xmltv_bytes(
    xmltv_url: str,
    timeout: int = 120,
    cancel_check: CancelCheck = None,
) -> bytes:
    """Download and validate XMLTV bytes from an explicit URL."""

    request = urllib.request.Request(
        xmltv_url,
        headers={
            "User-Agent": "M3U-Web-Picker/2.0",
            "Accept": "application/xml,text/xml,*/*",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        chunks = []
        while True:
            _raise_if_cancelled(cancel_check)
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks)
        content_encoding = str(response.headers.get("Content-Encoding", "")).lower()

    if content_encoding == "gzip" or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    elif raw[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = [name for name in archive.namelist() if not name.endswith("/")]
            if not members:
                raise ValueError("The EPG archive was empty.")
            raw = archive.read(members[0])
    if b"<tv" not in raw[:10000]:
        raise ValueError("The provider response did not look like XMLTV data.")
    return raw


def _parse_xmltv_time(value: str, default_tz: ZoneInfo) -> datetime | None:
    value = str(value or "").strip()
    match = re.match(r"^(\d{14})(?:\s+([+-]\d{4}|Z))?", value)
    if not match:
        return None
    try:
        base = datetime.strptime(match.group(1), "%Y%m%d%H%M%S")
        offset = match.group(2)
        if not offset:
            return base.replace(tzinfo=default_tz)
        if offset == "Z":
            return base.replace(tzinfo=ZoneInfo("UTC"))
        sign = 1 if offset.startswith("+") else -1
        hours = int(offset[1:3])
        minutes = int(offset[3:5])
        if hours > 23 or minutes > 59:
            raise ValueError("invalid XMLTV UTC offset")
        from datetime import timezone

        return base.replace(
            tzinfo=timezone(sign * timedelta(hours=hours, minutes=minutes))
        )
    except (ValueError, OverflowError) as exc:
        raise MalformedSportsEntry(f"Invalid XMLTV timestamp {value!r}.") from exc


def _channel_text(channel: dict) -> str:
    return " ".join(
        str(channel.get(key, "") or "")
        for key in ("name", "tvg_name", "group", "tvg_id")
    )


def _league_matches(text: str) -> list[str]:
    normalized = str(text or "").lower()
    return [
        league_id
        for league_id, patterns in LEAGUE_PATTERNS.items()
        if any(re.search(pattern, normalized, re.I) for pattern in patterns)
    ]


def _college_football_match(text: str, matches: list[str]) -> str:
    """Resolve college subdivisions without generic "college football" leakage."""
    lowered = str(text or "").lower()
    if "ncaaf-fbs" in matches and re.search(r"\bfbs\b|football bowl subdivision|division i fbs", lowered, re.I):
        return "ncaaf-fbs"
    for league_id in (
        "ncaaf-fcs", "ncaaf-d2", "ncaaf-d3", "naia-football",
        "njcaa-football", "high-school-football",
    ):
        if league_id in matches:
            return league_id
    if "ncaaf-fbs" in matches:
        return "ncaaf-fbs"
    return ""


def _detect_league(primary_text: str, fallback_text: str = "") -> str:
    """Detect a league/series while keeping shared provider groups isolated."""
    primary_matches = _league_matches(primary_text)
    if primary_matches:
        college_match = _college_football_match(primary_text, primary_matches)
        if college_match:
            return college_match
        if "milb" in primary_matches and "mlb" not in primary_matches:
            return "milb"
        if "mlb" in primary_matches and "milb" not in primary_matches:
            return "mlb"
        if len(primary_matches) == 1:
            return primary_matches[0]
        # Patterns are defined from specific competitions to broader fallbacks;
        # the first match therefore wins for non-baseball classifications.
        return primary_matches[0]

    fallback_matches = _league_matches(fallback_text)
    if {"mlb", "milb"}.issubset(fallback_matches):
        return ""
    college_match = _college_football_match(fallback_text, fallback_matches)
    if college_match:
        return college_match
    return fallback_matches[0] if fallback_matches else ""


def _detect_sport_tags(text: str) -> list[str]:
    normalized = str(text or "").lower()
    return [
        sport_id
        for sport_id, patterns in SPORT_PATTERNS.items()
        if any(re.search(pattern, normalized, re.I) for pattern in patterns)
    ]


def _detect_sport(text: str) -> str:
    matches = _detect_sport_tags(text)
    return matches[0] if matches else ""


def _strip_provider_prefix(value: str) -> str:
    text = value.strip()
    if "|" in text:
        prefix, remainder = text.split("|", 1)
        if re.search(r"\d|MLB|NBA|NHL|FLSP|Victory|Apple|MiLB", prefix, re.I):
            text = remainder.strip()
    text = re.sub(r"^(?:NFL|NHL|NCAAF|NCAAB|PPV|EVENTS)\s*(?:SD\s*)?\d{1,3}\s*:\s*", "", text, flags=re.I)
    text = re.sub(r"^(?:MiLB|MLB|NBA|NHL|NFL|NCAAF|NCAAB|NWSL)\s*:\s*", "", text, flags=re.I)
    return text.strip(" |:-")


def _extract_event_datetime(text: str, settings: dict, now: datetime) -> tuple[str, datetime | None]:
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    match = DATE_RE.search(text)
    if match:
        clean = text[: match.start()].strip()
        time_value = match.group("time") or "00:00:00"
        if len(time_value) == 5:
            time_value += ":00"
        timestamp = f"{match.group('date')}T{time_value}"
        try:
            start = datetime.fromisoformat(timestamp).replace(tzinfo=timezone)
        except (ValueError, OverflowError) as exc:
            raise MalformedSportsEntry(
                f"Invalid embedded event timestamp {timestamp!r}."
            ) from exc
        return clean, start

    time_match = LEADING_TIME_RE.search(text)
    if time_match:
        raw_hour = int(time_match.group("hour"))
        minute = int(time_match.group("minute") or 0)
        ampm = time_match.group("ampm").lower()
        if not 1 <= raw_hour <= 12 or not 0 <= minute <= 59:
            raise MalformedSportsEntry(
                f"Invalid event time {time_match.group(0)!r}."
            )
        hour = raw_hour
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        try:
            start = datetime.combine(
                _sports_day(now, settings),
                dt_time(hour, minute),
                tzinfo=timezone,
            )
        except (ValueError, OverflowError) as exc:
            raise MalformedSportsEntry(
                f"Invalid event time {time_match.group(0)!r}."
            ) from exc
        return text, start
    return text, None


def _team_catalog(db_path: Path | str) -> list[dict]:
    return [item for item in catalog_payload(db_path, scope_type="team")]


def _build_team_lookup(db_path: Path | str) -> dict:
    """Build one scan-local, pre-normalized team lookup.

    Event titles repeat across provider channels, XMLTV airings, replays, and
    alternate feeds. Normalizing the complete catalog for every occurrence was
    one of the largest avoidable CPU costs in a broad sports scan. This lookup
    lives only for the current scan and is discarded afterward.
    """
    teams = _team_catalog(db_path)
    aliases_by_league: dict[str, list[tuple[int, str, str, str]]] = defaultdict(list)
    exact_by_league: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
    all_aliases: list[tuple[int, str, str, str]] = []
    exact_all: dict[str, tuple[str, str]] = {}
    for team in teams:
        league_id = str(team.get("league_id", "") or "")
        seen_aliases: set[str] = set()
        for alias in [team.get("name", ""), *team.get("aliases", [])]:
            alias_norm = _normalize(str(alias or ""))
            if not alias_norm or alias_norm in seen_aliases:
                continue
            seen_aliases.add(alias_norm)
            row = (len(alias_norm), alias_norm, str(team["id"]), str(team["name"]))
            aliases_by_league[league_id].append(row)
            all_aliases.append(row)
            exact_by_league[league_id].setdefault(
                alias_norm,
                (str(team["id"]), str(team["name"])),
            )
            exact_all.setdefault(alias_norm, (str(team["id"]), str(team["name"])))
    return {
        "teams": teams,
        "aliases_by_league": dict(aliases_by_league),
        "exact_by_league": {key: dict(value) for key, value in exact_by_league.items()},
        "all_aliases": all_aliases,
        "exact_all": exact_all,
        "resolution_cache": {},
        "cache_hits": 0,
        "cache_misses": 0,
    }


def _find_team_id(
    text: str,
    league_id: str,
    teams: list[dict],
    team_lookup: dict | None = None,
) -> tuple[str, str]:
    normalized = _normalize(text)
    if not normalized:
        return "", text.strip()

    if team_lookup is not None:
        cache = team_lookup.setdefault("resolution_cache", {})
        cache_key = (str(league_id or ""), normalized)
        if cache_key in cache:
            team_lookup["cache_hits"] = int(team_lookup.get("cache_hits", 0)) + 1
            return cache[cache_key]
        team_lookup["cache_misses"] = int(team_lookup.get("cache_misses", 0)) + 1
        exact = None
        if league_id:
            exact = team_lookup.get("exact_by_league", {}).get(str(league_id), {}).get(normalized)
            if exact is None:
                exact = team_lookup.get("exact_by_league", {}).get("", {}).get(normalized)
        else:
            exact = team_lookup.get("exact_all", {}).get(normalized)
        if exact is not None:
            cache[cache_key] = exact
            return exact

        if league_id:
            aliases = [
                *team_lookup.get("aliases_by_league", {}).get(str(league_id), []),
                *team_lookup.get("aliases_by_league", {}).get("", []),
            ]
        else:
            aliases = team_lookup.get("all_aliases", [])
        padded = f" {normalized} "
        candidates = [
            (length, team_id, name)
            for length, alias_norm, team_id, name in aliases
            if normalized == alias_norm or f" {alias_norm} " in padded
        ]
        if candidates:
            _length, team_id, name = max(candidates)
            result = (team_id, name)
        else:
            result = ("", _smart_team_name(text))
        cache[cache_key] = result
        return result

    candidates = []
    for team in teams:
        if league_id and team.get("league_id") and team["league_id"] != league_id:
            continue
        aliases = [team["name"], *team.get("aliases", [])]
        for alias in aliases:
            alias_norm = _normalize(alias)
            if not alias_norm:
                continue
            if normalized == alias_norm or re.search(rf"(?:^|\s){re.escape(alias_norm)}(?:$|\s)", normalized):
                candidates.append((len(alias_norm), team["id"], team["name"]))
    if not candidates:
        return "", _smart_team_name(text)
    _, team_id, name = max(candidates)
    return team_id, name


def _infer_baseball_league(
    left: str,
    right: str,
    teams: list[dict],
    team_lookup: dict | None = None,
) -> str:
    """Resolve an ambiguous shared baseball group from both participants.

    Provider groups commonly say ``MLB / MiLB``. A matchup is promoted to a
    league only when both sides resolve inside the same catalog, preventing an
    MLB nickname embedded in a minor-league team name from leaking across the
    boundary.
    """
    resolved = []
    for candidate in ("mlb", "milb"):
        away_id, _away_name = _find_team_id(left, candidate, teams, team_lookup)
        home_id, _home_name = _find_team_id(right, candidate, teams, team_lookup)
        if away_id and home_id:
            resolved.append(candidate)
    return resolved[0] if len(resolved) == 1 else ""


def _event_from_text(
    db_path: Path | str,
    channel: dict,
    text: str,
    settings: dict,
    now: datetime,
    *,
    forced_start: datetime | None = None,
    forced_end: datetime | None = None,
    extra_text: str = "",
    team_lookup: dict | None = None,
) -> dict | None:
    full_text = f"{text} {extra_text} {_channel_text(channel)}".strip()
    title_text = text.strip()
    if (
        PLACEHOLDER_RE.search(title_text)
        or CLEAR_OFF_AIR_RE.search(title_text)
        or REPLAY_RE.search(full_text) and not settings.get("include_replays")
    ):
        return None
    if PREGAME_RE.search(full_text) and not settings.get("include_pregame"):
        return None

    league_id = _detect_league(text)
    if not league_id and extra_text:
        league_id = _detect_league(extra_text)
    if not league_id:
        league_id = _detect_league("", _channel_text(channel))
    sport_tags = _detect_sport_tags(full_text)
    mapped_sport = LEAGUE_SPORTS.get(league_id, "")
    if mapped_sport and mapped_sport not in sport_tags:
        sport_tags.insert(0, mapped_sport)
    sport_id = mapped_sport or next((tag for tag in sport_tags if tag != "olympics"), "") or (sport_tags[0] if sport_tags else "")
    cleaned, parsed_start = _extract_event_datetime(_strip_provider_prefix(text), settings, now)
    if forced_start:
        timing_source = "xmltv"
    elif parsed_start:
        timing_source = "embedded"
    else:
        timing_source = "untimed"
    time_is_explicit = timing_source != "untimed"
    start = forced_start or parsed_start
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" |:-")

    match = MATCHUP_RE.search(cleaned)
    # Team-sport league rules are intended to add games, not static networks,
    # studio shows, RedZone channels, or numbered empty event slots.
    if league_id in TEAM_MATCHUP_LEAGUES and not match:
        return None
    teams = (
        team_lookup.get("teams", [])
        if team_lookup is not None
        else _team_catalog(db_path)
    )
    away_id = home_id = ""
    away_name = home_name = ""
    if match:
        left = match.group("left").strip(" |:-")
        right = match.group("right").strip(" |:-")
        if not league_id:
            league_id = _infer_baseball_league(left, right, teams, team_lookup)
        away_id, away_name = _find_team_id(left, league_id, teams, team_lookup)
        home_id, home_name = _find_team_id(right, league_id, teams, team_lookup)
        display_name = f"{away_name} at {home_name}"
    else:
        display_name = cleaned

    meaningful = bool(match or start or sport_id)
    if not meaningful or not display_name:
        return None
    if not match and re.fullmatch(r"(?:\d{1,2}\s*(?:am|pm)?|[A-Z0-9 ]*NETWORK|ESPN\d?|FOX|CBS|NBC|TNT|TBS)", display_name, re.I):
        return None

    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    # Untimed provider slots are retained only long enough to merge with a
    # matching XMLTV programme. Do not invent a noon start: that made stale
    # provider slots look permanently current and leaked bogus times into the UI.
    event_date = (
        start.astimezone(timezone).date()
        if isinstance(start, datetime)
        else _sports_day(now, settings)
    )
    identity = "-".join(filter(None, [league_id or sport_id or "sports", away_id or _slug(away_name), home_id or _slug(home_name)]))
    if not match:
        identity = "-".join(filter(None, [league_id or sport_id or "sports", _slug(display_name)]))
    variant_match = EVENT_VARIANT_RE.search(full_text)
    if variant_match:
        variant = variant_match.group("number") or {
            "first": "1",
            "second": "2",
        }.get(str(variant_match.group("word") or "").lower(), "")
        if variant:
            identity = f"{identity}:game-{variant}"
    event_base_key = f"{event_date.isoformat()}:{identity}"

    return {
        # _merge_events adds a stable time suffix after separating same-day
        # doubleheaders/replays. Until then event_key is the base identity.
        "event_key": event_base_key,
        "event_base_key": event_base_key,
        "event_identity": identity,
        "event_date": event_date.isoformat(),
        "league_id": league_id,
        "sport_id": sport_id,
        "sport_tags": sport_tags,
        "display_name": display_name,
        "away_team_id": away_id,
        "away_team_name": away_name,
        "home_team_id": home_id,
        "home_team_name": home_name,
        "start": start,
        "end": forced_end,
        "time_is_explicit": time_is_explicit,
        "timing_source": timing_source,
        # Embedded M3U timestamps are provider schedule anchors. XMLTV times
        # describe airings of those games and may include overnight replays or
        # time-shifted repeats. Keep this provenance through record merging so
        # replay classification does not depend on unreliable <live/> markers.
        "source_kind": "epg" if forced_start else "m3u",
        "has_embedded_anchor": timing_source == "embedded",
        "source_channels": [channel],
        "source_text": full_text,
        "is_replay": bool(REPLAY_RE.search(full_text)),
    }


def _event_has_usable_timing(event: dict) -> bool:
    return (
        str(event.get("timing_source") or "untimed") != "untimed"
        and isinstance(event.get("start"), datetime)
    )


def _primary_event_end(event: dict) -> datetime | None:
    explicit = event.get("end")
    start = event.get("start")
    if isinstance(explicit, datetime):
        if not isinstance(start, datetime) or explicit > start:
            return explicit
        # Bad provider stop values should not instantly kill an otherwise
        # valid event. Fall through to the sport-specific duration estimate.
    if not isinstance(start, datetime):
        return None
    classification_id = str(event.get("league_id") or event.get("sport_id") or "sports")
    return start + _event_duration(classification_id)


def _event_end(event: dict) -> datetime | None:
    """Return the end of the logical event's last retained airing.

    The primary live programme controls the logical game identity. When replay
    and encore support is enabled, later provider airings are attached to that
    same event and extend channel availability without allocating new slots.
    """
    candidates = []
    primary = _primary_event_end(event)
    if isinstance(primary, datetime):
        candidates.append(primary)
    for programme in event.get("epg_programmes", []) or []:
        if not isinstance(programme, dict):
            continue
        stop = programme.get("stop")
        if isinstance(stop, datetime):
            candidates.append(stop)
    return max(candidates) if candidates else None


def _event_overlaps_window(
    event: dict,
    window_start: datetime,
    window_end: datetime,
) -> bool:
    """Return true when any part of an event's live window intersects the scan.

    A start-only comparison drops games already in progress, especially in
    next-24-hours mode and immediately after the daily refresh boundary. Treat
    the event as an interval and keep it until its end plus the postgame grace.
    """
    start = event.get("start")
    end = _event_end(event)
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return False
    try:
        local_start = start.astimezone(window_start.tzinfo)
        local_end = end.astimezone(window_start.tzinfo)
    except Exception:
        return False
    return local_start < window_end and local_end + EVENT_END_GRACE > window_start


def _event_overlaps_replay_context(
    event: dict,
    window_start: datetime,
    window_end: datetime,
) -> bool:
    """Keep recent canonical airings long enough to classify later repeats.

    A two-hour refresh can run after the live programme's 90-minute grace has
    ended but before an overnight replay. Retaining the canonical candidate for
    one day lets the merge stage suppress or attach those repeats correctly.
    The final lineup filter still applies the normal event/grace window.
    """
    start = event.get("start")
    end = _primary_event_end(event)
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return False
    try:
        local_start = start.astimezone(window_start.tzinfo)
        local_end = end.astimezone(window_start.tzinfo)
    except Exception:
        return False
    return local_start < window_end and local_end + REPLAY_ATTACH_WINDOW > window_start


def _event_is_stale(event: dict, scan_anchor: datetime) -> bool:
    """Return true only after a timed event's live window plus grace has ended."""
    if not _event_has_usable_timing(event):
        return True
    end = _event_end(event)
    if not end:
        return True
    try:
        current = (
            scan_anchor.astimezone(end.tzinfo)
            if end.tzinfo
            else scan_anchor.replace(tzinfo=None)
        )
    except Exception:
        current = scan_anchor
    return current >= end + EVENT_END_GRACE


def _m3u_events(
    db_path: Path | str,
    channels: Iterable[dict],
    settings: dict,
    scan_anchor: datetime,
    diagnostics: dict,
    cancel_check: CancelCheck = None,
    *,
    team_lookup: dict | None = None,
    team_feed_channel_ids: set[int] | None = None,
) -> list[dict]:
    window_start, window_end, _ = _target_window(scan_anchor, settings)
    events = []
    for index, channel in enumerate(channels):
        if index % 100 == 0:
            _raise_if_cancelled(cancel_check)
        if (
            id(channel) in team_feed_channel_ids
            if team_feed_channel_ids is not None
            else bool(_team_feed_identity(channel))
        ):
            continue
        text = str(channel.get("name", "") or "")
        try:
            event = _event_from_text(
                db_path,
                channel,
                text,
                settings,
                scan_anchor,
                team_lookup=team_lookup,
            )
        except MalformedSportsEntry as exc:
            _record_malformed_entry(
                diagnostics,
                source="m3u",
                label=text or str(channel.get("tvg_name", "") or ""),
                exc=exc,
            )
            continue
        if not event:
            continue
        # Keep untimed M3U candidates only for possible XMLTV corroboration.
        # Timed candidates must overlap the requested window as intervals.
        if not _event_has_usable_timing(event) or _event_overlaps_replay_context(
            event, window_start, window_end
        ):
            events.append(event)
    return events


def _iterparse_xmltv(path: Path, *, events=("end",)):
    """Iterparse plain or gzip XMLTV without expanding gzip sources to disk."""
    if path.suffix.lower() == ".gz":
        with gzip.open(path, "rb") as handle:
            yield from ElementTree.iterparse(handle, events=events)
        return
    yield from ElementTree.iterparse(path, events=events)


def _epg_events(
    db_path: Path | str,
    epg_path: Path | None,
    channels: list[dict],
    settings: dict,
    scan_anchor: datetime,
    diagnostics: dict,
    cancel_check: CancelCheck = None,
    *,
    team_lookup: dict | None = None,
    source_priority: int = 0,
) -> list[dict]:
    if not epg_path or not epg_path.exists() or epg_path.stat().st_size == 0:
        return []

    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    window_start, window_end, _ = _target_window(scan_anchor, settings)
    by_tvg_id: dict[str, list[dict]] = defaultdict(list)
    by_name: dict[str, list[dict]] = defaultdict(list)
    for index, channel in enumerate(channels):
        if index % 250 == 0:
            _raise_if_cancelled(cancel_check)
        tvg_id = str(channel.get("tvg_id", "") or "").strip()
        if tvg_id:
            by_tvg_id[tvg_id].append(channel)
        for value in (channel.get("tvg_name", ""), channel.get("name", "")):
            normalized = _normalize(str(value or ""))
            if normalized:
                by_name[normalized].append(channel)

    xml_names: dict[str, list[str]] = defaultdict(list)
    output: list[dict] = []
    try:
        for index, (_event, element) in enumerate(
            _iterparse_xmltv(epg_path, events=("end",))
        ):
            if index % 500 == 0:
                _raise_if_cancelled(cancel_check)
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "channel":
                channel_id = element.attrib.get("id", "")
                xml_names[channel_id] = [
                    child.text.strip()
                    for child in element
                    if child.tag.rsplit("}", 1)[-1] == "display-name" and child.text
                ]
                element.clear()
                continue
            if tag != "programme":
                continue

            channel_id = element.attrib.get("channel", "")

            # Reject unrelated XMLTV channels before parsing timestamps or
            # programme fields. Public country guides can contain more than a
            # million programmes, while only a small fraction of their channel
            # IDs/names exist in the loaded IPTV catalog. This ordering keeps
            # the fallback scan streaming/linear without doing date parsing for
            # every unrelated programme in a 500+ MB guide.
            source_channels = list(by_tvg_id.get(channel_id, []))
            if not source_channels:
                for display_name in xml_names.get(channel_id, []):
                    source_channels.extend(by_name.get(_normalize(display_name), []))
            if not source_channels:
                element.clear()
                continue

            raw_start = element.attrib.get("start", "")
            raw_stop = element.attrib.get("stop", "")
            try:
                start = _parse_xmltv_time(raw_start, timezone)
                stop = _parse_xmltv_time(raw_stop, timezone) if raw_stop else None
            except MalformedSportsEntry as exc:
                _record_malformed_entry(
                    diagnostics,
                    source="epg",
                    label=(
                        f"programme channel={channel_id or 'unknown'} "
                        f"start={raw_start or 'missing'}"
                    ),
                    exc=exc,
                )
                element.clear()
                continue
            if not start:
                element.clear()
                continue

            # Cheap time-window prefilter before resolving title/details. The
            # exact sport-specific duration is applied after classification.
            rough_end = stop or (start + MAX_ESTIMATED_EVENT_DURATION)
            try:
                rough_start_local = start.astimezone(window_start.tzinfo)
                rough_end_local = rough_end.astimezone(window_start.tzinfo)
            except Exception:
                element.clear()
                continue
            if not (
                rough_start_local < window_end
                and rough_end_local + REPLAY_ATTACH_WINDOW > window_start
            ):
                element.clear()
                continue

            fields: dict[str, list[str]] = defaultdict(list)
            programme_markers: set[str] = set()
            for child in element:
                child_tag = child.tag.rsplit("}", 1)[-1]
                if child.text and child_tag in {"title", "sub-title", "desc", "category"}:
                    fields[child_tag].append(child.text.strip())
                if child_tag in {"live", "previously-shown", "new"}:
                    programme_markers.add(child_tag)
            title = fields["title"][0] if fields["title"] else ""
            extra = " ".join(fields["sub-title"] + fields["desc"] + fields["category"])
            if not title:
                element.clear()
                continue

            programme_is_replay = "previously-shown" in programme_markers
            if programme_is_replay and not settings.get("include_replays"):
                element.clear()
                continue

            try:
                parsed = _event_from_text(
                    db_path,
                    source_channels[0],
                    title,
                    settings,
                    scan_anchor,
                    forced_start=start,
                    forced_end=stop,
                    extra_text=extra,
                    team_lookup=team_lookup,
                )
            except MalformedSportsEntry as exc:
                _record_malformed_entry(
                    diagnostics,
                    source="epg",
                    label=title,
                    exc=exc,
                )
                element.clear()
                continue
            if parsed and _event_overlaps_replay_context(parsed, window_start, window_end):
                parsed["source_channels"] = source_channels
                effective_stop = stop if isinstance(stop, datetime) and stop > start else None
                comparison_stop = effective_stop or _event_end(parsed)
                try:
                    scan_local = (
                        scan_anchor.astimezone(start.tzinfo)
                        if start.tzinfo
                        else scan_anchor.replace(tzinfo=None)
                    )
                    current_at_scan = bool(
                        comparison_stop
                        and start <= scan_local < comparison_stop
                    )
                except Exception:
                    current_at_scan = False
                parsed["epg_programme"] = {
                    "title": title,
                    "subtitle": fields["sub-title"][0] if fields["sub-title"] else "",
                    "description": fields["desc"][0] if fields["desc"] else "",
                    "categories": list(dict.fromkeys(fields["category"])),
                    "start": start,
                    "stop": effective_stop,
                    "is_live": "live" in programme_markers,
                    "is_replay": programme_is_replay,
                    "is_new": "new" in programme_markers,
                    "current_at_scan": current_at_scan,
                    "source_channel_id": channel_id,
                    "source_priority": int(source_priority),
                }
                parsed["is_replay"] = bool(parsed.get("is_replay") or programme_is_replay)
                output.append(parsed)
            element.clear()
    except (ElementTree.ParseError, OSError):
        return []
    return output


def _previous_generated_event_anchors(
    db_path: Path | str,
    settings: dict,
    scan_anchor: datetime,
    *,
    team_lookup: dict | None = None,
) -> list[dict]:
    """Rehydrate recent logical games as replay-classification anchors.

    Sports Update refreshes the provider playlist before each scan. Some
    providers remove the timed event row as soon as the live game ends while
    their XMLTV still contains overnight replays. Without a short-lived memory
    of the prior logical game, a 2-hour refresh could rediscover that replay as
    a brand-new event. These anchors participate only in matching; the normal
    event-window/stale filters still decide whether a channel remains visible.
    """
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    window_start, window_end, _sports_date = _target_window(scan_anchor, settings)
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT event_key, league_id, event_title, event_start, event_end,
                   epg_programme_json
            FROM sports_generated
            WHERE event_start IS NOT NULL
            GROUP BY event_key
            ORDER BY event_start
            """
        ).fetchall()

    anchors: list[dict] = []
    for row in rows:
        start = _parse_iso_datetime(row["event_start"], timezone)
        end = _parse_iso_datetime(row["event_end"], timezone)
        if not isinstance(start, datetime):
            continue
        if not isinstance(end, datetime) or end <= start:
            end = start + _event_duration(str(row["league_id"] or "sports"))
        try:
            if not (
                start.astimezone(window_start.tzinfo) < window_end
                and end.astimezone(window_start.tzinfo) + REPLAY_ATTACH_WINDOW > window_start
            ):
                continue
        except Exception:
            continue

        title = str(row["event_title"] or "").strip()
        if not title:
            continue
        channel_stub = {
            "name": title,
            "tvg_name": str(row["league_id"] or ""),
            "group": str(row["league_id"] or ""),
            "tvg_id": "",
            "url": "",
        }
        parsed = _event_from_text(
            db_path,
            channel_stub,
            title,
            settings,
            scan_anchor,
            forced_start=start,
            forced_end=end,
            extra_text=str(row["league_id"] or ""),
            team_lookup=team_lookup,
        )
        if not parsed:
            continue
        parsed["timing_source"] = "embedded"
        parsed["source_kind"] = "history"
        parsed["source_kinds"] = ["history"]
        parsed["has_embedded_anchor"] = True
        parsed["historical_anchor"] = True
        parsed["source_channels"] = []

        programme = _json_load(row["epg_programme_json"], {})
        if isinstance(programme, dict) and programme:
            primary = _parse_programme_record(programme, timezone)
            primary.pop("airings", None)
            if primary.get("start"):
                parsed["epg_programme"] = primary
        anchors.append(parsed)
    return anchors


def _timing_rank(event: dict) -> int:
    return {"untimed": 0, "embedded": 1, "xmltv": 2, "schedule_api": 3}.get(
        str(event.get("timing_source") or "untimed"), 0
    )


def _epg_programme_quality(event: dict) -> tuple[int, int, int, int, int, int]:
    """Rank XMLTV metadata while preserving ordered EPG-source precedence."""
    programme = event.get("epg_programme")
    if not isinstance(programme, dict) or not programme:
        return (-1_000_000, 0, 0, 0, 0, 0)
    try:
        source_priority = int(programme.get("source_priority", 0))
    except (TypeError, ValueError):
        source_priority = 0
    return (
        -source_priority,
        1 if programme.get("current_at_scan") else 0,
        1 if isinstance(programme.get("stop"), datetime) else 0,
        1 if programme.get("is_live") else 0,
        1 if programme.get("title") else 0,
        1 if programme.get("description") else 0,
    )


def _adopt_event_timing(target: dict, source: dict) -> None:
    target["start"] = source.get("start")
    target["end"] = source.get("end")
    target["timing_source"] = source.get("timing_source")
    programme = source.get("epg_programme")
    if isinstance(programme, dict) and programme:
        target["epg_programme"] = dict(programme)


def _merge_event_records(existing: dict, incoming: dict) -> dict:
    # A migrated/generated-history row is only a replay-classification hint.
    # When current provider data lands in the same airing cluster, the current
    # record owns the cluster and must not remain marked as historical.
    if existing.get("historical_anchor") and not incoming.get("historical_anchor"):
        existing.pop("historical_anchor", None)
        existing["source_kind"] = incoming.get("source_kind") or existing.get("source_kind")

    seen = {str(ch.get("url", "")) for ch in existing["source_channels"]}
    for channel in incoming["source_channels"]:
        channel_url = str(channel.get("url", ""))
        if channel_url not in seen:
            existing["source_channels"].append(channel)
            seen.add(channel_url)

    existing_rank = _timing_rank(existing)
    incoming_rank = _timing_rank(incoming)
    # The schedule API owns canonical game time/identity, while provider XMLTV
    # remains the richer guide source. Preserve that programme metadata without
    # allowing a replay/time-shifted XMLTV start to redefine the game.
    if existing_rank == 3 and incoming_rank == 2 and _epg_programme_quality(incoming) > _epg_programme_quality(existing):
        programme = incoming.get("epg_programme")
        if isinstance(programme, dict) and programme:
            existing["epg_programme"] = dict(programme)
    elif incoming_rank == 3 and existing_rank == 2 and _epg_programme_quality(existing) > _epg_programme_quality(incoming):
        programme = existing.get("epg_programme")
        if isinstance(programme, dict) and programme:
            incoming["epg_programme"] = dict(programme)
    if incoming_rank > existing_rank:
        _adopt_event_timing(existing, incoming)
    elif incoming_rank == existing_rank and incoming_rank > 0:
        if incoming_rank == 2:
            # Keep one authoritative provider programme intact. Expanding to
            # the earliest start/latest stop creates fake multi-hour windows
            # and loses the exact programme Jellyfin should display.
            if _epg_programme_quality(incoming) > _epg_programme_quality(existing):
                _adopt_event_timing(existing, incoming)
        else:
            incoming_start = incoming.get("start")
            existing_start = existing.get("start")
            if isinstance(incoming_start, datetime) and (
                not isinstance(existing_start, datetime) or incoming_start < existing_start
            ):
                existing["start"] = incoming_start
            incoming_end = incoming.get("end")
            existing_end = existing.get("end")
            if isinstance(incoming_end, datetime) and (
                not isinstance(existing_end, datetime) or incoming_end > existing_end
            ):
                existing["end"] = incoming_end

    existing["time_is_explicit"] = _timing_rank(existing) > 0
    existing["has_embedded_anchor"] = bool(
        existing.get("has_embedded_anchor") or incoming.get("has_embedded_anchor")
    )
    existing["has_schedule_api_anchor"] = bool(
        existing.get("has_schedule_api_anchor") or incoming.get("has_schedule_api_anchor")
    )
    # Preserve canonical schedule identity/metadata when an API anchor is
    # enriched with provider M3U/XMLTV data.  Provider rows may improve guide
    # text and supply playback sources, but they never redefine the API game.
    for key in (
        "api_event_id",
        "api_source",
        "api_canonical_start",
        "api_status_short",
        "api_status_long",
        "api_home_id",
        "api_away_id",
    ):
        if not existing.get(key) and incoming.get(key):
            existing[key] = incoming.get(key)
    source_kinds = set(existing.get("source_kinds", []))
    source_kinds.add(str(existing.get("source_kind") or ""))
    source_kinds.update(incoming.get("source_kinds", []))
    source_kinds.add(str(incoming.get("source_kind") or ""))
    existing["source_kinds"] = sorted(value for value in source_kinds if value)
    existing["is_replay"] = bool(
        existing.get("is_replay") or incoming.get("is_replay")
    )
    return existing


def _timed_events_are_same_slot(left: dict, right: dict) -> bool:
    left_start = left.get("start")
    right_start = right.get("start")
    if not isinstance(left_start, datetime) or not isinstance(right_start, datetime):
        return False
    try:
        delta = abs((left_start - right_start).total_seconds())
    except Exception:
        return False
    return delta <= EVENT_MERGE_TOLERANCE.total_seconds()


def _event_programme(event: dict) -> dict:
    programme = event.get("epg_programme")
    return programme if isinstance(programme, dict) else {}


def _event_is_live_airing(event: dict) -> bool:
    programme = _event_programme(event)
    return bool(programme.get("is_live")) and not bool(
        event.get("is_replay") or programme.get("is_replay")
    )


def _event_is_replay_airing(event: dict) -> bool:
    programme = _event_programme(event)
    return bool(event.get("is_replay") or programme.get("is_replay"))


def _schedule_api_candidate_text(event: dict) -> str:
    programme = _event_programme(event)
    return " ".join(
        str(value or "")
        for value in (
            event.get("source_text"),
            programme.get("title"),
            programme.get("description"),
        )
    )


def _schedule_api_supporting_content(event: dict) -> bool:
    """Return True for same-matchup studio/betting/support rows, not the game.

    This is deliberately scoped to API-assisted canonicalization.  These rows
    remain valid EPG content elsewhere; they simply must not be promoted to the
    live stream for a canonical game merely because both team names appear.
    """
    return bool(SCHEDULE_API_SUPPORT_RE.search(_schedule_api_candidate_text(event)))


def _schedule_api_candidate_duration(event: dict) -> timedelta | None:
    start = event.get("start")
    if not isinstance(start, datetime):
        return None
    programme = _event_programme(event)
    stop = programme.get("stop")
    if isinstance(stop, datetime) and stop > start:
        return stop - start
    end = event.get("end")
    if isinstance(end, datetime) and end > start:
        return end - start
    estimated = _primary_event_end(event)
    if isinstance(estimated, datetime) and estimated > start:
        return estimated - start
    return None


def _schedule_api_live_candidate_score(
    event: dict,
    canonical_start: datetime,
) -> tuple | None:
    """Rank one provider airing as the live representation of an API game.

    Team identity was already matched by _apply_schedule_api_identity.  Here we
    choose the provider row closest to the API start, preferring actual live /
    full-game programming and rejecting studio, wagering, pre/postgame, and
    similar support content.  Rows several hours away are replay/support
    candidates, never a second live game.
    """
    start = event.get("start")
    if not isinstance(start, datetime):
        return None
    try:
        delta = abs(start - canonical_start)
    except Exception:
        return None
    if delta > SCHEDULE_API_LIVE_CANDIDATE_WINDOW:
        return None
    if _event_is_replay_airing(event) or _schedule_api_supporting_content(event):
        return None

    programme = _event_programme(event)
    duration = _schedule_api_candidate_duration(event)
    duration_seconds = duration.total_seconds() if duration is not None else 0
    # 90+ minutes is a useful generic signal for a full team-sport broadcast;
    # exact API-time proximity still dominates for sports with shorter games.
    full_game = 1 if duration_seconds >= 90 * 60 else 0
    live = 1 if programme.get("is_live") else 0
    current = 1 if programme.get("current_at_scan") else 0
    return (
        -delta.total_seconds(),
        live,
        full_game,
        current,
        _timing_rank(event),
        _epg_programme_quality(event),
    )


def _schedule_api_provider_clusters(events: list[dict]) -> list[dict]:
    """Merge only provider rows that describe the same airing slot.

    Schedule API anchors are intentionally excluded by the caller.  Keeping the
    anchor out of the ordinary 10-minute provider merge prevents a 6:00 PM
    betting show from donating its stream URL to a 6:05 PM canonical game.
    """
    timed = sorted(
        (event for event in events if _event_has_usable_timing(event)),
        key=lambda event: event["start"],
    )
    clusters: list[dict] = []
    for event in timed:
        same_slot = False
        if clusters:
            left = clusters[-1].get("start")
            right = event.get("start")
            if isinstance(left, datetime) and isinstance(right, datetime):
                try:
                    # API matching needs a much tighter airing merge than the
                    # legacy 10-minute tolerance.  A 6:00 betting show and a
                    # 6:05 first pitch are distinct candidates even though
                    # they mention the same teams.
                    same_slot = abs((left - right).total_seconds()) <= 90
                except Exception:
                    same_slot = False
        if same_slot:
            _merge_event_records(clusters[-1], event)
        else:
            clusters.append(event)
    return clusters


def _merge_schedule_api_group(
    group: list[dict],
    *,
    include_replays: bool,
) -> dict | None:
    """Collapse every provider airing of one API event into one logical game."""
    api_anchors = [event for event in group if event.get("has_schedule_api_anchor")]
    if not api_anchors:
        return None
    # An API event ID is unique, so there should be exactly one anchor.  Keep
    # deterministic behavior if duplicate cache rows ever appear.
    anchor = min(
        api_anchors,
        key=lambda event: event.get("start")
        or datetime.max.replace(tzinfo=ZoneInfo("UTC")),
    )
    canonical_start = anchor.get("start")
    if not isinstance(canonical_start, datetime):
        return None

    provider_events = [
        event
        for event in group
        if event is not anchor and not event.get("has_schedule_api_anchor")
    ]
    clusters = _schedule_api_provider_clusters(provider_events)

    scored: list[tuple[tuple, dict]] = []
    for cluster in clusters:
        score = _schedule_api_live_candidate_score(cluster, canonical_start)
        if score is not None:
            scored.append((score, cluster))
    if scored:
        # Score's first component is negative absolute start delta, so max()
        # prefers the closest clean candidate.  The remaining terms break ties
        # in favor of live/full-game/high-quality provider metadata.
        _score, live_cluster = max(scored, key=lambda item: item[0])
        _merge_event_records(anchor, live_cluster)

    if include_replays:
        replay_cutoff = canonical_start + SCHEDULE_API_LIVE_CANDIDATE_WINDOW
        for cluster in clusters:
            if scored and cluster is live_cluster:
                continue
            start = cluster.get("start")
            if not isinstance(start, datetime) or start <= canonical_start:
                continue
            # Later same-matchup rows outside the canonical live window are
            # replays/encores even when the provider incorrectly marks them
            # <live/>.  Studio/betting support rows are not replay airings.
            if start >= replay_cutoff and not _schedule_api_supporting_content(cluster):
                _append_replay_airing(
                    anchor,
                    cluster,
                    inferred=not _event_is_replay_airing(cluster),
                )

    return anchor


def _programme_identity(programme: dict) -> tuple[str, str, str, str]:
    def stamp(value) -> str:
        return value.isoformat() if isinstance(value, datetime) else str(value or "")

    return (
        stamp(programme.get("start")),
        stamp(programme.get("stop")),
        str(programme.get("source_channel_id") or ""),
        str(programme.get("title") or ""),
    )


def _append_replay_airing(target: dict, source: dict, *, inferred: bool = False) -> None:
    programme = _event_programme(source)
    if not programme:
        return
    replay = dict(programme)
    replay["is_replay"] = True
    replay["is_live"] = False
    if inferred:
        replay["inferred_replay"] = True
    airings = target.setdefault("epg_programmes", [])
    existing = {_programme_identity(item) for item in airings if isinstance(item, dict)}
    identity = _programme_identity(replay)
    if identity not in existing:
        airings.append(replay)
        airings.sort(
            key=lambda item: item.get("start")
            if isinstance(item.get("start"), datetime)
            else datetime.max.replace(tzinfo=ZoneInfo("UTC"))
        )


def _canonical_replay_anchor_end(event: dict) -> datetime | None:
    return _primary_event_end(event)


def _is_later_airing_of(anchor: dict, candidate: dict) -> bool:
    anchor_start = anchor.get("start")
    candidate_start = candidate.get("start")
    anchor_end = _canonical_replay_anchor_end(anchor)
    if not all(isinstance(value, datetime) for value in (anchor_start, candidate_start, anchor_end)):
        return False
    try:
        return (
            candidate_start > anchor_start + EVENT_MERGE_TOLERANCE
            and candidate_start <= anchor_end + REPLAY_ATTACH_WINDOW
        )
    except Exception:
        return False


def _event_current_at_scan(event: dict) -> bool:
    return bool(_event_programme(event).get("current_at_scan"))


def _event_has_embedded_anchor(event: dict) -> bool:
    return bool(event.get("has_embedded_anchor"))


def _nearest_replay_anchor(candidate: dict, anchors: list[dict]) -> dict | None:
    matches = [anchor for anchor in anchors if _is_later_airing_of(anchor, candidate)]
    if not matches:
        return None
    return max(
        matches,
        key=lambda event: event.get("start")
        or datetime.min.replace(tzinfo=ZoneInfo("UTC")),
    )


def _assign_merged_event_keys(
    events: list[dict],
    timezone_name: str = "America/New_York",
) -> list[dict]:
    event_timezone = ZoneInfo(str(timezone_name or "America/New_York"))
    used: set[str] = set()
    for event in sorted(
        events,
        key=lambda item: (
            str(item.get("event_identity") or item.get("event_base_key") or ""),
            item.get("start") or datetime.max.replace(tzinfo=ZoneInfo("UTC")),
        ),
    ):
        start = event.get("start")
        api_event_id = str(event.get("api_event_id") or "").strip()
        if api_event_id:
            candidate = f"{str(event.get('api_source') or SCHEDULE_API_SOURCE)}:{api_event_id}"
            serial = 2
            unique = candidate
            while unique in used:
                unique = f"{candidate}-{serial}"
                serial += 1
            event["event_key"] = unique
            event["event_base_key"] = candidate
            if isinstance(start, datetime):
                local_start = start if start.tzinfo is not None else start.replace(tzinfo=event_timezone)
                event["event_date"] = local_start.astimezone(event_timezone).date().isoformat()
            used.add(unique)
            continue
        identity = str(event.get("event_identity") or "sports")
        if isinstance(start, datetime):
            local_start = (
                start.replace(tzinfo=event_timezone)
                if start.tzinfo is None
                else start.astimezone(event_timezone)
            )
            event_date = local_start.date().isoformat()
            suffix = local_start.strftime("%H%M")
        else:
            event_date = str(event.get("event_date") or "untimed")
            suffix = "untimed"
        base_key = f"{event_date}:{identity}"
        event["event_date"] = event_date
        event["event_base_key"] = base_key
        candidate = f"{base_key}:{suffix}"
        serial = 2
        while candidate in used:
            candidate = f"{base_key}:{suffix}-{serial}"
            serial += 1
        event["event_key"] = candidate
        used.add(candidate)
    return events


def _logical_broadcast_day(event: dict, timezone_name: str) -> date | None:
    """Return the provider broadcast day for logical-game grouping.

    Evening games commonly replay after midnight. A noon rollover maps a
    6:40 PM live game plus 12:30 AM and 6:00 AM repeats to the same logical
    day, but maps the following evening's actual game to the next day.
    """
    start = event.get("start")
    if not isinstance(start, datetime):
        return None
    timezone = ZoneInfo(str(timezone_name or "America/New_York"))
    try:
        local_start = (
            start.replace(tzinfo=timezone)
            if start.tzinfo is None
            else start.astimezone(timezone)
        )
    except Exception:
        return None
    return (local_start - timedelta(hours=LOGICAL_EVENT_DAY_ROLLOVER_HOUR)).date()


def _cluster_is_history(event: dict) -> bool:
    return bool(event.get("historical_anchor"))


def _bucket_has_schedule_anchor(events: list[dict]) -> bool:
    return any(
        event.get("has_schedule_api_anchor")
        or _event_has_embedded_anchor(event)
        or _cluster_is_history(event)
        for event in events
    )


def _canonical_bucket_anchor(events: list[dict]) -> dict:
    """Pick one canonical game from a same-matchup broadcast-day bucket.

    Current timed M3U rows outrank historical rows. Historical rows still act
    as short-lived replay classifiers when the provider has already removed
    the live event slot. Provider ``<live/>`` markers are intentionally not a
    deciding factor because several providers mark overnight replays as live.
    """
    ranked = sorted(
        events,
        key=lambda event: (
            0
            if event.get("has_schedule_api_anchor")
            else 1
            if _event_has_embedded_anchor(event) and not _cluster_is_history(event)
            else 2
            if _cluster_is_history(event)
            else 3
            if _event_current_at_scan(event)
            else 4,
            event.get("start") or datetime.max.replace(tzinfo=ZoneInfo("UTC")),
        ),
    )
    return ranked[0]


def _is_overnight_repeat(anchor: dict, candidate: dict, timezone_name: str) -> bool:
    """Heuristically identify an after-midnight repeat without a schedule row.

    EPG-only doubleheaders during the afternoon/evening remain separate. A
    programme after midnight but before noon, following an evening airing of
    the same matchup in the same broadcast-day bucket, is treated as a repeat.
    """
    anchor_start = anchor.get("start")
    candidate_start = candidate.get("start")
    if not isinstance(anchor_start, datetime) or not isinstance(candidate_start, datetime):
        return False
    timezone = ZoneInfo(str(timezone_name or "America/New_York"))
    try:
        anchor_local = anchor_start.astimezone(timezone)
        candidate_local = candidate_start.astimezone(timezone)
    except Exception:
        return False
    return bool(
        _logical_broadcast_day(anchor, timezone_name)
        == _logical_broadcast_day(candidate, timezone_name)
        and anchor_local.hour >= LOGICAL_EVENT_DAY_ROLLOVER_HOUR
        and candidate_local.date() > anchor_local.date()
        and candidate_local.hour < LOGICAL_EVENT_DAY_ROLLOVER_HOUR
    )



def _schedule_api_anchor_events(
    raw_events: list[dict],
    settings: dict,
    team_lookup: dict,
) -> list[dict]:
    teams = team_lookup.get("teams", [])
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    anchors = []
    for item in raw_events:
        start = item.get("scheduled_start")
        if not isinstance(start, datetime):
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone)
        start = start.astimezone(timezone)
        away_id, away_name = _find_team_id(
            str(item.get("away_name", "")), SCHEDULE_API_LEAGUE_ID, teams, team_lookup
        )
        home_id, home_name = _find_team_id(
            str(item.get("home_name", "")), SCHEDULE_API_LEAGUE_ID, teams, team_lookup
        )
        if not away_id or not home_id:
            continue
        event_id = str(item.get("api_event_id") or "").strip()
        if not event_id:
            continue
        status_short = str(item.get("status_short") or "").upper()
        identity = f"{SCHEDULE_API_SOURCE}:{event_id}"
        anchors.append({
            "event_key": identity,
            "event_base_key": identity,
            "event_identity": identity,
            "event_date": start.date().isoformat(),
            "league_id": SCHEDULE_API_LEAGUE_ID,
            "sport_id": "baseball",
            "sport_tags": ["baseball"],
            "display_name": f"{away_name} at {home_name}",
            "away_team_id": away_id,
            "away_team_name": away_name,
            "home_team_id": home_id,
            "home_team_name": home_name,
            "start": start,
            "end": None,
            "time_is_explicit": True,
            "timing_source": "schedule_api",
            "source_kind": "schedule_api",
            "source_kinds": ["schedule_api"],
            "source_channels": [],
            "source_text": f"MLB {away_name} at {home_name}",
            "has_schedule_api_anchor": True,
            "api_event_id": event_id,
            "api_source": str(item.get("api_source") or SCHEDULE_API_SOURCE),
            "api_status_short": status_short,
            "api_status_long": str(item.get("status_long") or ""),
            "api_home_id": str(item.get("home_api_id") or ""),
            "api_away_id": str(item.get("away_api_id") or ""),
        })
    return anchors


def _apply_schedule_api_identity(
    provider_events: list[dict],
    api_anchors: list[dict],
) -> list[dict]:
    """Map provider airings to canonical API game IDs before logical merging."""
    if not api_anchors:
        return provider_events
    by_matchup: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for anchor in api_anchors:
        key = (
            str(anchor.get("league_id") or ""),
            str(anchor.get("away_team_id") or ""),
            str(anchor.get("home_team_id") or ""),
        )
        by_matchup[key].append(anchor)
    output = []
    for event in provider_events:
        if str(event.get("league_id") or "") != SCHEDULE_API_LEAGUE_ID:
            output.append(event)
            continue
        away_id = str(event.get("away_team_id") or "")
        home_id = str(event.get("home_team_id") or "")
        candidates = list(by_matchup.get((SCHEDULE_API_LEAGUE_ID, away_id, home_id), []))
        if not candidates:
            # Some providers reverse vs/at semantics. Team identity is still
            # strong enough to find the canonical game, while the API retains
            # authoritative home/away roles for feed preference.
            candidates = list(by_matchup.get((SCHEDULE_API_LEAGUE_ID, home_id, away_id), []))
        if not candidates:
            output.append(event)
            continue
        event_start = event.get("start")
        if isinstance(event_start, datetime):
            ranked = []
            for anchor in candidates:
                anchor_start = anchor.get("start")
                if not isinstance(anchor_start, datetime):
                    continue
                delta = abs((event_start - anchor_start).total_seconds())
                if delta <= SCHEDULE_API_MATCH_WINDOW.total_seconds():
                    ranked.append((delta, anchor_start, anchor))
            if not ranked:
                output.append(event)
                continue
            _delta, _start, match = min(ranked, key=lambda item: (item[0], item[1]))
        elif len(candidates) == 1:
            match = candidates[0]
        else:
            output.append(event)
            continue
        if str(match.get("api_status_short") or "").upper() in {"POST", "CANC", "ABD", "SUSP"}:
            continue
        event["event_identity"] = match["event_identity"]
        event["event_base_key"] = match["event_base_key"]
        event["event_date"] = match["event_date"]
        event["api_event_id"] = match["api_event_id"]
        event["api_source"] = match["api_source"]
        event["api_canonical_start"] = match["start"]
        event["has_schedule_api_identity"] = True
        # API-BASEBALL is authoritative for home/away identity.
        event["away_team_id"] = match["away_team_id"]
        event["away_team_name"] = match["away_team_name"]
        event["home_team_id"] = match["home_team_id"]
        event["home_team_name"] = match["home_team_name"]
        event["display_name"] = match["display_name"]
        output.append(event)
    return output


def _merge_events(
    events: Iterable[dict],
    cancel_check: CancelCheck = None,
    settings: dict | None = None,
) -> list[dict]:
    """Merge provider records into stable logical games.

    A provider programme or timed playlist row is an *airing*, not a channel
    identity. Records are first merged at nearly identical start times, then
    grouped by stable matchup identity and provider broadcast day. The noon
    rollover collapses evening games and their after-midnight repeats even
    when the provider marks every airing as live or a migrated debug database
    contains the old duplicate generated rows as history anchors.

    EPG-only same-day live airings remain distinct because they may represent
    a real doubleheader. Timed playlist/history anchors provide stronger
    evidence that same-broadcast-day repeats belong to one logical game.
    Explicit ``Game 1``/``Game 2`` text is already part of event_identity and
    therefore remains separate before this function is reached.
    """
    settings = settings or {}
    include_replays = bool(settings.get("include_replays"))
    timezone_name = str(settings.get("timezone", "America/New_York"))
    grouped: dict[str, list[dict]] = defaultdict(list)
    for index, event in enumerate(events):
        if index % 100 == 0:
            _raise_if_cancelled(cancel_check)
        identity = str(
            event.get("event_identity")
            or event.get("event_base_key")
            or event.get("event_key")
            or ""
        )
        grouped[identity].append(event)

    merged: list[dict] = []
    for group_index, group in enumerate(grouped.values()):
        if group_index % 100 == 0:
            _raise_if_cancelled(cancel_check)

        # A schedule API event ID is the logical game identity.  Do not split
        # its provider airings by start time or broadcast day: 6:05 PM live,
        # 11:00 PM rebroadcast, and an overnight encore are all representations
        # of the same API game.  The API-specific merger chooses one clean live
        # provider candidate and optionally attaches later replays as programme
        # windows on that same generated channel identity.
        if any(event.get("has_schedule_api_anchor") for event in group):
            api_event = _merge_schedule_api_group(
                group,
                include_replays=include_replays,
            )
            if api_event is not None:
                merged.append(api_event)
            continue

        timed = sorted(
            (event for event in group if _event_has_usable_timing(event)),
            key=lambda event: event["start"],
        )
        untimed = [event for event in group if not _event_has_usable_timing(event)]

        # Merge records describing the same provider airing. Timed records are
        # sorted, so only the latest cluster can fall inside the tolerance.
        clusters: list[dict] = []
        for event in timed:
            if clusters and _timed_events_are_same_slot(clusters[-1], event):
                _merge_event_records(clusters[-1], event)
            else:
                clusters.append(event)

        if len(clusters) == 1:
            for event in untimed:
                _merge_event_records(clusters[0], event)
        elif not clusters and untimed:
            candidate = untimed[0]
            for event in untimed[1:]:
                _merge_event_records(candidate, event)
            clusters.append(candidate)

        if not clusters:
            continue

        # Split one matchup into broadcast-day buckets. This is the missing
        # layer in debug8/debug9: migrated history rows and timed replay slots
        # were all promoted to independent canonical anchors before replay
        # classification could run.
        day_buckets: dict[date | None, list[dict]] = defaultdict(list)
        for cluster in clusters:
            day_buckets[_logical_broadcast_day(cluster, timezone_name)].append(cluster)

        for bucket in sorted(
            day_buckets.values(),
            key=lambda items: min(
                (item.get("start") for item in items if isinstance(item.get("start"), datetime)),
                default=datetime.max.replace(tzinfo=ZoneInfo("UTC")),
            ),
        ):
            ordered = sorted(
                bucket,
                key=lambda event: event.get("start")
                or datetime.max.replace(tzinfo=ZoneInfo("UTC")),
            )

            # Without a playlist/history schedule anchor, preserve possible
            # same-day doubleheaders. Only obvious after-midnight repeats are
            # folded into the prior evening airing. Explicit replay markers are
            # attached/dropped regardless of time.
            if not _bucket_has_schedule_anchor(ordered):
                logical_candidates: list[dict] = []
                explicit_replays: list[dict] = []
                for candidate in ordered:
                    if _event_is_replay_airing(candidate):
                        explicit_replays.append(candidate)
                        continue
                    prior = logical_candidates[-1] if logical_candidates else None
                    if prior is not None and _is_overnight_repeat(
                        prior, candidate, timezone_name
                    ):
                        if include_replays:
                            _append_replay_airing(prior, candidate, inferred=True)
                        continue
                    logical_candidates.append(candidate)

                if include_replays and logical_candidates:
                    for replay in explicit_replays:
                        anchor = _nearest_replay_anchor(replay, logical_candidates) or logical_candidates[0]
                        _append_replay_airing(anchor, replay)
                merged.extend(logical_candidates)
                continue

            anchor = _canonical_bucket_anchor(ordered)
            for candidate in ordered:
                if candidate is anchor:
                    continue
                explicit_replay = _event_is_replay_airing(candidate)
                if include_replays:
                    _append_replay_airing(
                        anchor,
                        candidate,
                        inferred=not explicit_replay,
                    )
                # With replays disabled the candidate is intentionally dropped.
                # Do not merge its source channels into the live game; a replay
                # slot may point to a different delayed provider stream.

            merged.append(anchor)

    return _assign_merged_event_keys(merged, timezone_name)


def _conference_matches(event: dict, conference_id: str) -> bool:
    if event.get("league_id") != "ncaaf-fbs":
        return False
    legacy_id = conference_id.replace("ncaaf-fbs:", "ncaaf:")
    team_names = CONFERENCE_TEAMS.get(legacy_id, [])
    participant_text = _normalize(
        f"{event.get('away_team_name', '')} {event.get('home_team_name', '')}"
    )
    return any(_normalize(team) in participant_text for team in team_names)


def _build_rule_index(rules: list[dict]) -> dict:
    by_scope: dict[str, dict[str, list[dict]]] = {
        scope: defaultdict(list) for scope in SCOPE_TYPES
    }
    for rule in rules:
        scope_type = str(rule.get("scope_type", ""))
        scope_id = str(rule.get("scope_id", ""))
        if scope_type in by_scope and scope_id:
            by_scope[scope_type][scope_id].append(rule)
    return {
        "by_scope": {scope: dict(values) for scope, values in by_scope.items()},
        "rules": rules,
    }


def _matching_rules(event: dict, rules: list[dict] | dict) -> list[dict]:
    if not isinstance(rules, dict) or "by_scope" not in rules:
        rules = _build_rule_index(list(rules))
    by_scope = rules["by_scope"]
    matched: dict[int, dict] = {}

    def add(items: Iterable[dict]) -> None:
        for rule in items:
            matched[int(rule["id"])] = rule

    league_id = str(event.get("league_id", "") or "")
    if league_id:
        add(by_scope["league"].get(league_id, []))
    for team_id in {event.get("away_team_id"), event.get("home_team_id")}:
        if team_id:
            add(by_scope["team"].get(str(team_id), []))
    for conference_id, conference_rules in by_scope["conference"].items():
        if _conference_matches(event, conference_id):
            add(conference_rules)

    event_sports = set(event.get("sport_tags", [])) | {event.get("sport_id", "")}
    source_text = event.get("source_text", "")
    for sport_id, sport_rules in by_scope["sport"].items():
        if sport_id in event_sports or any(
            re.search(pattern, source_text, re.I)
            for pattern in SPORT_PATTERNS.get(sport_id, [])
        ):
            add(sport_rules)

    return sorted(
        matched.values(),
        key=lambda rule: (RULE_PRIORITY.get(rule["scope_type"], 99), rule["id"]),
    )


def _explicit_team_rules(event: dict, matched_rules: Iterable[dict]) -> list[dict]:
    participant_ids = {
        str(value)
        for value in (event.get("away_team_id"), event.get("home_team_id"))
        if value
    }
    return [
        rule
        for rule in matched_rules
        if rule.get("scope_type") == "team"
        and str(rule.get("scope_id", "")) in participant_ids
    ]


def _select_controlling_rule(event: dict, matched_rules: list[dict]) -> tuple[dict, bool]:
    """Choose feed preferences without letting a broad league duplicate games.

    A logical event may match both a team rule and its league rule. It remains
    one event either way. Any explicit participant-team rule enables the
    expanded feed set and controls ranking; league/conference/sport-only games
    receive one best feed.
    """
    team_rules = _explicit_team_rules(event, matched_rules)
    if team_rules:
        return team_rules[0], True
    return matched_rules[0], False


def _provider_priority(channel: dict) -> int:
    try:
        return max(0, int(channel.get("_provider_priority", 0)))
    except (TypeError, ValueError):
        return 0


def _team_feed_index(
    channels: Iterable[dict],
) -> tuple[dict[str, list[dict]], set[int]]:
    output: dict[str, list[dict]] = defaultdict(list)
    channel_ids: set[int] = set()
    for channel in channels:
        identity = _team_feed_identity(channel)
        if identity:
            _league_id, team_id, _team_name = identity
            output[team_id].append(channel)
            channel_ids.add(id(channel))
    return output, channel_ids


def _team_feeds(channels: Iterable[dict]) -> dict[str, list[dict]]:
    return _team_feed_index(channels)[0]


def _feed_type(channel: dict, event: dict, team_id: str = "") -> str:
    text = _channel_text(channel).lower()
    if "backup" in text:
        return "backup"
    if re.search(r"espa[nñ]ol|spanish|\bes\b", text, re.I):
        return "spanish"
    if team_id and team_id == event.get("away_team_id"):
        return "away"
    if team_id and team_id == event.get("home_team_id"):
        return "home"
    if any(word in text for word in NETWORK_WORDS):
        return "national"
    return "event"


def _feed_label(feed_type: str, event: dict, team_id: str) -> tuple[str, str]:
    if feed_type == "away":
        team = event.get("away_team_name") or "Away"
        return f"{team.split()[-1]} Feed", f"Away broadcast • {team}"
    if feed_type == "home":
        team = event.get("home_team_name") or "Home"
        return f"{team.split()[-1]} Feed", f"Home broadcast • {team}"
    if feed_type == "national":
        return "National Feed", "National broadcast"
    if feed_type == "spanish":
        return "Spanish Feed", "Spanish-language broadcast"
    if feed_type == "backup":
        return "Backup Feed", "Backup stream"
    return "Event Feed", "Provider event stream"


def _build_feeds(
    event: dict,
    channels: list[dict] | dict[str, list[dict]],
    rule: dict,
    settings: dict,
) -> list[dict]:
    # Accept the old channel-list form for direct callers/tests, but scans pass
    # the prebuilt map so 6,500 provider rows are not reclassified per event.
    team_feed_map = (
        channels
        if isinstance(channels, dict)
        else _team_feeds(channels)
    )
    candidates_by_url: dict[str, dict] = {}

    def add(channel: dict, team_id: str = "") -> None:
        url = str(channel.get("url", "") or "").strip()
        if not url:
            return
        kind = _feed_type(channel, event, team_id)
        candidate = {
            "channel": channel,
            "feed_type": kind,
            "team_id": team_id,
            "provider_priority": _provider_priority(channel),
        }
        existing = candidates_by_url.get(url)
        if (
            existing is None
            or candidate["provider_priority"] < existing["provider_priority"]
            or (
                candidate["provider_priority"] == existing["provider_priority"]
                and candidate.get("team_id")
                and not existing.get("team_id")
            )
        ):
            # The same stream may first arrive through XMLTV as a generic
            # event source and later through the fixed team-feed index. Keep
            # provider precedence, but prefer the team-aware classification at
            # equal priority so home/away labels are not lost.
            candidates_by_url[url] = candidate

    for source in event.get("source_channels", []):
        add(source)
    for team_id in (event.get("away_team_id"), event.get("home_team_id")):
        if team_id:
            for channel in team_feed_map.get(team_id, []):
                add(channel, team_id)

    candidates = list(candidates_by_url.values())

    if not settings.get("use_backup_feeds"):
        candidates = [candidate for candidate in candidates if candidate["feed_type"] != "backup"]
    elif any(candidate["feed_type"] != "backup" for candidate in candidates):
        # Backups stay hidden unless the user explicitly asks for all feeds.
        if rule.get("feed_preference") != "all":
            candidates = [candidate for candidate in candidates if candidate["feed_type"] != "backup"]

    # Provider precedence is enforced after eligibility filtering. Fallback
    # providers never add extra feeds when a usable primary candidate exists;
    # they fill only the events/feed cases missing from every lower-priority
    # provider. This is intentionally independent of input concatenation order.
    if candidates:
        winning_priority = min(candidate["provider_priority"] for candidate in candidates)
        candidates = [
            candidate
            for candidate in candidates
            if candidate["provider_priority"] == winning_priority
        ]

    preference = rule.get("feed_preference", "best")
    favorite_team_id = rule.get("scope_id") if rule.get("scope_type") == "team" else ""

    rank = {
        "national": 20,
        "event": 25,
        "home": 30,
        "away": 31,
        "spanish": 50,
        "backup": 90,
    }
    if preference == "favorite" and favorite_team_id:
        for candidate in candidates:
            if candidate["team_id"] == favorite_team_id:
                rank[candidate["feed_type"]] = -10
    elif preference == "home":
        rank["home"] = -10
    elif preference == "away":
        rank["away"] = -10
    elif preference == "national":
        rank["national"] = -10
        rank["event"] = 0

    candidates.sort(
        key=lambda candidate: (
            -10 if favorite_team_id and candidate["team_id"] == favorite_team_id else rank.get(candidate["feed_type"], 60),
            str(candidate["channel"].get("name", "")).lower(),
        )
    )

    expanded_feeds = event.get("expanded_feeds")
    if expanded_feeds is None:
        expanded_feeds = rule.get("scope_type") == "team"
    if not expanded_feeds:
        return candidates[:1]
    return candidates


def _rewrite_extinf(line: str, attrs: dict[str, str], display_name: str) -> str:
    if not line.startswith("#EXTINF"):
        line = "#EXTINF:-1,"
    left = line.rsplit(",", 1)[0] if "," in line else line
    for key, value in attrs.items():
        escaped = str(value).replace('"', "'")
        if re.search(rf'{re.escape(key)}="[^"]*"', left):
            left = re.sub(rf'{re.escape(key)}="[^"]*"', f'{key}="{escaped}"', left)
        else:
            left += f' {key}="{escaped}"'
    return f"{left},{display_name}"


def generated_stream_path(assigned_number: int) -> str:
    """Return the app-local playback URL for one generated sports slot.

    Jellyfin may collapse two M3U rows that use the same playback URL even when
    their channel numbers and tvg-id values differ. Generated sports rows use a
    unique local redirect path so a full-time manual channel can coexist with a
    temporary event feed backed by the exact same provider stream.
    """
    number = int(assigned_number)
    if number < 0:
        raise ValueError("Sports channel numbers must be non-negative.")
    return f"/sports/stream/{number}"


def _generated_raw(channel: dict, generated: dict) -> list[str]:
    playback_url = str(
        generated.get("playback_url")
        or generated_stream_path(int(generated["assigned_number"]))
    )
    raw = list(channel.get("raw", []))
    if not raw:
        raw = ["#EXTINF:-1", playback_url]
    attrs = {
        "tvg-id": generated["tvg_id"],
        "tvg-chno": str(generated["assigned_number"]),
        "tvg-name": generated["display_name"],
        "group-title": generated["group_title"],
        "x-sports-event": generated["event_key"],
        "x-sports-feed": generated["feed_type"],
        "x-sports-subtitle": generated["subtitle"],
    }
    if generated.get("tvg_logo"):
        attrs["tvg-logo"] = generated["tvg_logo"]
    raw[0] = _rewrite_extinf(raw[0], attrs, generated["display_name"])
    if raw[-1] != playback_url:
        raw[-1] = playback_url
    return raw



def _generated_tvg_id(assigned_number: int) -> str:
    """Return a credential-free XMLTV id stable for one numbered sports slot.

    Event names and provider URLs change every day. Jellyfin can retain a tuner
    channel between refreshes, so tying the guide id to either value can leave
    the retained channel mapped to yesterday's XMLTV id. The assigned sports
    channel number is the durable identity the user configured.
    """
    number = int(assigned_number)
    if number < 0:
        raise ValueError("Sports channel numbers must be non-negative.")
    return f"m3u-picker-sports-{number}"


def _xmltv_time(value: datetime) -> str:
    local = value if value.tzinfo else value.replace(tzinfo=ZoneInfo("UTC"))
    return local.strftime("%Y%m%d%H%M%S %z")


def _parse_iso_datetime(value: str | None, fallback_tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=fallback_tz)
        return parsed
    except (TypeError, ValueError, OverflowError):
        return None


def _serialize_programme_record(programme: dict) -> dict:
    output = dict(programme)
    for field in ("start", "stop"):
        value = output.get(field)
        output[field] = value.isoformat() if isinstance(value, datetime) else None
    output["categories"] = [
        str(value).strip()
        for value in output.get("categories", [])
        if str(value).strip()
    ]
    return output


def _serialize_epg_programme(event: dict) -> dict:
    programme = event.get("epg_programme")
    if not isinstance(programme, dict) or not programme:
        return {}
    output = _serialize_programme_record(programme)
    output["airings"] = [
        _serialize_programme_record(item)
        for item in event.get("epg_programmes", []) or []
        if isinstance(item, dict) and item
    ]
    return output


def _parse_programme_record(programme: dict, timezone: ZoneInfo) -> dict:
    output = dict(programme)
    output["start"] = _parse_iso_datetime(output.get("start"), timezone)
    output["stop"] = _parse_iso_datetime(output.get("stop"), timezone)
    output["categories"] = [
        str(value).strip()
        for value in output.get("categories", [])
        if str(value).strip()
    ]
    return output


def _epg_programme_from_item(item: dict, timezone: ZoneInfo) -> dict:
    programme = item.get("epg_programme")
    if isinstance(programme, str):
        programme = _json_load(programme, {})
    if not isinstance(programme, dict) or not programme:
        return {}
    output = _parse_programme_record(programme, timezone)
    output["airings"] = [
        _parse_programme_record(airing, timezone)
        for airing in programme.get("airings", []) or []
        if isinstance(airing, dict)
    ]
    return output


def _event_duration(league_id: str) -> timedelta:
    return timedelta(hours=ESTIMATED_EVENT_HOURS.get(league_id, 3))


def _clean_feed_subtitle(value: str) -> str:
    # The M3U subtitle already carries the start time. XMLTV has explicit times,
    # so omit the duplicate trailing time from programme subtitles.
    return re.sub(r"\s*•\s*\d{1,2}:\d{2}\s+(?:AM|PM)\s*$", "", value or "", flags=re.I).strip()


def _add_text(parent: ElementTree.Element, tag: str, text: str, **attrs) -> ElementTree.Element:
    element = ElementTree.SubElement(parent, tag, {key: str(value) for key, value in attrs.items()})
    element.text = str(text)
    return element


def _add_programme(
    root: ElementTree.Element,
    *,
    channel_id: str,
    start: datetime,
    stop: datetime,
    title: str,
    subtitle: str,
    description: str,
    categories: Iterable[str],
    is_live: bool = False,
    is_replay: bool = False,
    is_new: bool = False,
) -> bool:
    if stop <= start:
        return False
    programme = ElementTree.SubElement(
        root,
        "programme",
        {
            "start": _xmltv_time(start),
            "stop": _xmltv_time(stop),
            "channel": channel_id,
        },
    )
    _add_text(programme, "title", title, lang="en")
    if subtitle:
        _add_text(programme, "sub-title", subtitle, lang="en")
    if description:
        _add_text(programme, "desc", description, lang="en")
    for category in categories:
        if category:
            _add_text(programme, "category", category, lang="en")
    if is_live:
        ElementTree.SubElement(programme, "live")
    if is_replay:
        ElementTree.SubElement(programme, "previously-shown")
    if is_new:
        ElementTree.SubElement(programme, "new")
    return True


def _guide_coverage_window(anchor: datetime, settings: dict) -> tuple[datetime, datetime]:
    """Return a continuous guide window around the current sports lineup.

    Generated channels may survive a container restart with a provider timestamp
    that has already gone stale. Keep those channels populated in Jellyfin while
    the next scan corrects or replaces them instead of exporting a blank guide.
    """
    target_start, target_end, _ = _target_window(anchor, settings)
    return (
        min(target_start, anchor - timedelta(hours=6)),
        max(target_end, anchor + timedelta(hours=30)),
    )


def build_sports_xmltv(
    generated: list[dict],
    settings: dict,
    *,
    generated_at: datetime | None = None,
) -> bytes:
    """Build a standalone XMLTV guide for generated sports channels."""
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    anchor = (generated_at or datetime.now().astimezone()).astimezone(timezone)
    root = ElementTree.Element(
        "tv",
        {
            "generator-info-name": XMLTV_GENERATOR_NAME,
            "source-info-name": "Generated sports guide",
        },
    )

    for item in generated:
        channel_id = str(item.get("tvg_id", "") or "").strip()
        if not channel_id:
            continue
        channel = ElementTree.SubElement(root, "channel", {"id": channel_id})
        _add_text(channel, "display-name", item.get("display_name", "Sports"), lang="en")
        # Jellyfin can map guide channels by channel number as well as id/name.
        # Keep a purely numeric alias in addition to the friendly display name.
        assigned_number = str(item.get("assigned_number", "") or "").strip()
        if assigned_number:
            _add_text(channel, "display-name", assigned_number, lang="en")
            _add_text(channel, "display-name", f"CH {assigned_number}", lang="en")
        logo = str(item.get("tvg_logo", "") or "").strip()
        if logo:
            ElementTree.SubElement(channel, "icon", {"src": logo})

    for item in generated:
        channel_id = str(item.get("tvg_id", "") or "").strip()
        if not channel_id:
            continue
        league_id = str(item.get("league_id", "") or "")
        league_label = LEAGUE_NAMES.get(league_id, "Sports")
        event_title = str(item.get("event_title", "") or "").strip()
        if not event_title:
            # Migration fallback for rows generated before event_title existed.
            display = str(item.get("display_name", "Sports event") or "Sports event")
            event_title = re.sub(r"^[^•]+•\s*", "", display)
            event_title = re.sub(r"\s+—\s+[^—]+$", "", event_title).strip()
        feed_subtitle = _clean_feed_subtitle(str(item.get("subtitle", "") or ""))
        start = _parse_iso_datetime(item.get("event_start"), timezone)
        end = _parse_iso_datetime(item.get("event_end"), timezone)
        is_replay = bool(item.get("is_replay"))
        categories = ["Sports", league_label]
        if is_replay:
            categories.append("Replay")

        coverage_start, coverage_end = _guide_coverage_window(anchor, settings)
        source_programme = _epg_programme_from_item(item, timezone)
        source_start = source_programme.get("start")
        source_stop = source_programme.get("stop")

        # When provider XMLTV corroborated the event, clone that exact
        # programme onto every generated feed. Do not reconstruct a synthetic
        # live block and accidentally replace a currently-airing game with a
        # vague "Event window" programme.
        if (
            isinstance(source_start, datetime)
            and isinstance(source_stop, datetime)
            and source_stop > source_start
        ):
            retained_airings = [source_programme]
            retained_airings.extend(
                airing
                for airing in source_programme.get("airings", []) or []
                if isinstance(airing, dict)
                and isinstance(airing.get("start"), datetime)
                and isinstance(airing.get("stop"), datetime)
                and airing["stop"] > airing["start"]
            )
            retained_airings.sort(key=lambda airing: airing["start"])

            latest_retained_stop = max(
                airing["stop"].astimezone(timezone) for airing in retained_airings
            )
            if latest_retained_stop + EVENT_END_GRACE <= coverage_start:
                stale_title = str(retained_airings[0].get("title") or event_title).strip()
                _add_programme(
                    root,
                    channel_id=channel_id,
                    start=coverage_start,
                    stop=coverage_end,
                    title=stale_title,
                    subtitle=feed_subtitle,
                    description=(
                        "Generated sports event channel. Provider schedule data was stale or unavailable; "
                        "guide coverage is being held until the next refresh."
                    ),
                    categories=categories,
                    is_replay=bool(retained_airings[0].get("is_replay") or is_replay),
                )
                continue

            primary_start = retained_airings[0]["start"].astimezone(timezone)
            upcoming_stop = min(primary_start, coverage_end)
            if coverage_start < upcoming_stop:
                primary_title = str(retained_airings[0].get("title") or event_title).strip()
                scheduled = primary_start.strftime("%A, %B %-d at %-I:%M %p %Z")
                _add_programme(
                    root,
                    channel_id=channel_id,
                    start=coverage_start,
                    stop=upcoming_stop,
                    title=f"Upcoming: {primary_title}",
                    subtitle=feed_subtitle,
                    description=f"{league_label} event scheduled for {scheduled}. {feed_subtitle}.",
                    categories=list(
                        dict.fromkeys(
                            [
                                *categories,
                                *retained_airings[0].get("categories", []),
                            ]
                        )
                    ),
                )

            for airing_index, airing in enumerate(retained_airings):
                local_start = airing["start"].astimezone(timezone)
                local_stop = airing["stop"].astimezone(timezone)
                airing_replay = bool(airing.get("is_replay"))
                airing_categories = list(
                    dict.fromkeys([*categories, *airing.get("categories", [])])
                )
                if airing_replay and "Replay" not in airing_categories:
                    airing_categories.append("Replay")

                airing_title = str(airing.get("title") or event_title).strip()
                if airing_replay and not REPLAY_RE.search(airing_title):
                    airing_title = f"Replay: {airing_title}"
                airing_description = str(airing.get("description") or "").strip()
                airing_subtitle = str(airing.get("subtitle") or "").strip()
                description_parts = [
                    value
                    for value in (airing_description, airing_subtitle, feed_subtitle)
                    if value
                ]
                exact_description = " • ".join(dict.fromkeys(description_parts))

                exact_start = max(local_start, coverage_start)
                exact_stop = min(local_stop, coverage_end)
                _add_programme(
                    root,
                    channel_id=channel_id,
                    start=exact_start,
                    stop=exact_stop,
                    title=airing_title,
                    subtitle=feed_subtitle,
                    description=exact_description,
                    categories=airing_categories,
                    is_live=bool(airing.get("is_live")) and not airing_replay,
                    is_replay=airing_replay,
                    is_new=bool(airing.get("is_new")),
                )

                # Hold each airing for exactly the same 90-minute grace used by
                # stale-channel removal. Clip the placeholder at the next
                # retained airing so XMLTV programmes never overlap.
                post_start = max(local_stop, coverage_start)
                post_stop = min(local_stop + EVENT_END_GRACE, coverage_end)
                if airing_index + 1 < len(retained_airings):
                    next_start = retained_airings[airing_index + 1]["start"].astimezone(timezone)
                    post_stop = min(post_stop, next_start)
                if post_start < post_stop:
                    _add_programme(
                        root,
                        channel_id=channel_id,
                        start=post_start,
                        stop=post_stop,
                        title=f"{event_title} — Event window",
                        subtitle=feed_subtitle,
                        description="The generated event feed remains available during the post-event grace period.",
                        categories=airing_categories,
                    )
            continue

        if start:
            local_start = start.astimezone(timezone)
            live_end = (end or (start + _event_duration(league_id))).astimezone(timezone)
            if live_end <= local_start:
                live_end = local_start + _event_duration(league_id)
            scheduled = local_start.strftime("%A, %B %-d at %-I:%M %p %Z")

            # Legacy/embedded provider timestamps can survive a restart before
            # the next scan repairs the lineup. Preserve the existing fallback
            # for those non-XMLTV rows; authoritative XMLTV programmes above
            # never use this broad synthetic coverage.
            if live_end + timedelta(hours=GUIDE_POSTGAME_HOURS) <= coverage_start:
                _add_programme(
                    root,
                    channel_id=channel_id,
                    start=coverage_start,
                    stop=coverage_end,
                    title=f"{league_label} • {event_title}",
                    subtitle=feed_subtitle,
                    description=(
                        "Generated sports event channel. Provider schedule data was stale or unavailable; "
                        "guide coverage is being held until the next refresh."
                    ),
                    categories=categories,
                    is_replay=is_replay,
                )
                continue

            upcoming_stop = min(local_start, coverage_end)
            if coverage_start < upcoming_stop:
                _add_programme(
                    root,
                    channel_id=channel_id,
                    start=coverage_start,
                    stop=upcoming_stop,
                    title=f"Upcoming: {event_title}",
                    subtitle=feed_subtitle,
                    description=f"{league_label} event scheduled for {scheduled}. {feed_subtitle}.",
                    categories=categories,
                )

            live_start = max(local_start, coverage_start)
            live_stop = min(live_end, coverage_end)
            live_prefix = "Replay" if is_replay else league_label
            _add_programme(
                root,
                channel_id=channel_id,
                start=live_start,
                stop=live_stop,
                title=f"{live_prefix} • {event_title}",
                subtitle=feed_subtitle,
                description=f"{event_title}. {feed_subtitle}.",
                categories=categories,
                is_live=not is_replay,
                is_replay=is_replay,
            )

            post_start = max(live_end, coverage_start)
            post_stop = coverage_end
            if post_start < post_stop:
                _add_programme(
                    root,
                    channel_id=channel_id,
                    start=post_start,
                    stop=post_stop,
                    title=f"{event_title} — Event window",
                    subtitle=feed_subtitle,
                    description="The generated event feed remains available during the post-event grace period.",
                    categories=categories,
                )
        else:
            # Exact provider schedule is unavailable. Cover the active lineup
            # continuously so Jellyfin never presents an empty generated channel.
            _add_programme(
                root,
                channel_id=channel_id,
                start=coverage_start,
                stop=coverage_end,
                title=f"{league_label} • {event_title}",
                subtitle=feed_subtitle,
                description="Provider sports event or replay; exact schedule data was unavailable.",
                categories=categories,
                is_replay=is_replay,
            )

    if hasattr(ElementTree, "indent"):
        ElementTree.indent(root, space="  ")
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def _xmltv_fragments(elements: Iterable[ElementTree.Element]) -> bytes:
    """Serialize XMLTV children without introducing a second XML declaration."""
    return b"\n".join(
        ElementTree.tostring(child, encoding="unicode", short_empty_elements=True).encode(
            "ascii", errors="xmlcharrefreplace"
        )
        for child in elements
    )


def _unfiltered_combined_xmltv(base_epg_path: Path, sports_xmltv: bytes) -> bytes:
    """Legacy full-guide merge retained for callers that intentionally request it."""
    base = base_epg_path.read_bytes()
    close_matches = list(re.finditer(rb"</(?:[A-Za-z_][A-Za-z0-9_.-]*:)?tv\s*>", base, flags=re.I))
    if not close_matches:
        return sports_xmltv

    overlay_root = ElementTree.fromstring(sports_xmltv)
    channels = [child for child in overlay_root if child.tag.rsplit("}", 1)[-1] == "channel"]
    programmes = [child for child in overlay_root if child.tag.rsplit("}", 1)[-1] == "programme"]
    channel_fragment = _xmltv_fragments(channels)
    programme_fragment = _xmltv_fragments(programmes)

    close_match = close_matches[-1]
    close_start = close_match.start()
    programme_match = re.search(
        rb"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?programme(?:\s|>)",
        base[:close_start],
        flags=re.I,
    )
    channel_insert = programme_match.start() if programme_match else close_start

    pieces = [base[:channel_insert]]
    if channel_fragment:
        pieces.extend([b"\n", channel_fragment, b"\n"])
    pieces.append(base[channel_insert:close_start])
    if programme_fragment:
        pieces.extend([b"\n", programme_fragment, b"\n"])
    pieces.append(base[close_start:])
    return b"".join(pieces)


def _filtered_provider_xmltv(
    base_epg_path: Path,
    allowed_channel_ids: set[str],
    *,
    cancel_check: CancelCheck = None,
) -> tuple[dict[str, str], list[bytes], list[bytes], set[str], set[str]]:
    """Stream a large plain/gzip XMLTV file and retain selected channel ids."""
    root_attributes: dict[str, str] = {}
    channel_fragments: list[bytes] = []
    programme_fragments: list[bytes] = []
    found_channels: set[str] = set()
    found_programmes: set[str] = set()
    allowed = {str(value).strip() for value in allowed_channel_ids if str(value).strip()}

    document_root = None
    try:
        for index, (event, element) in enumerate(
            _iterparse_xmltv(base_epg_path, events=("start", "end"))
        ):
            if index % 1000 == 0:
                _raise_if_cancelled(cancel_check)
            tag = element.tag.rsplit("}", 1)[-1]
            if event == "start" and tag == "tv" and document_root is None:
                document_root = element
                root_attributes = {str(key): str(value) for key, value in element.attrib.items()}
                continue
            if event != "end":
                continue
            if tag == "channel":
                channel_id = str(element.attrib.get("id", "")).strip()
                if channel_id in allowed:
                    channel_fragments.append(ElementTree.tostring(element, encoding="utf-8"))
                    found_channels.add(channel_id)
                element.clear()
                if document_root is not None:
                    document_root.clear()
            elif tag == "programme":
                channel_id = str(element.attrib.get("channel", "")).strip()
                if channel_id in allowed:
                    programme_fragments.append(ElementTree.tostring(element, encoding="utf-8"))
                    found_programmes.add(channel_id)
                element.clear()
                if document_root is not None:
                    document_root.clear()
    except (ElementTree.ParseError, OSError, EOFError):
        return {}, [], [], set(), set()
    return root_attributes, channel_fragments, programme_fragments, found_channels, found_programmes


def build_combined_xmltv(
    base_epg_path: Path | None,
    sports_xmltv: bytes,
    allowed_base_channel_ids: set[str] | None = None,
    *,
    fallback_epg_paths: Iterable[Path] | None = None,
    cancel_check: CancelCheck = None,
) -> bytes:
    """Build a Jellyfin-sized combined guide with ordered fallback XMLTV sources.

    The first/base EPG has precedence. Later configured/public guides are
    system-wide fallbacks for every selected manual channel: they can fill
    uncovered time windows, but never replace a higher-priority programme that
    overlaps the same channel/time. Gzip inputs are streamed.
    """
    _raise_if_cancelled(cancel_check)
    fallback_paths = [Path(path) for path in (fallback_epg_paths or []) if path]
    valid_base = bool(base_epg_path and base_epg_path.exists() and base_epg_path.stat().st_size)
    if allowed_base_channel_ids is None:
        if valid_base:
            return _unfiltered_combined_xmltv(base_epg_path, sports_xmltv)
        return sports_xmltv

    allowed = {str(value).strip() for value in allowed_base_channel_ids if str(value).strip()}
    attrs: dict[str, str] = {}
    provider_channels: list[bytes] = []
    provider_programmes: list[bytes] = []
    supplied_channels: set[str] = set()
    supplied_programmes: set[str] = set()
    # Intervals accepted from higher-priority XMLTV sources. Lower-priority
    # configured/public guides may fill uncovered windows on the same channel,
    # but an overlapping entry never displaces provider data.
    accepted_intervals: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    xmltv_default_tz = ZoneInfo("UTC")

    def programme_window(fragment: bytes) -> tuple[str, datetime | None, datetime | None]:
        try:
            element = ElementTree.fromstring(fragment)
            cid = str(element.attrib.get("channel", "")).strip()
            start = _parse_xmltv_time(str(element.attrib.get("start", "") or ""), xmltv_default_tz)
            stop = _parse_xmltv_time(str(element.attrib.get("stop", "") or ""), xmltv_default_tz)
            if start and stop and stop > start:
                return cid, start, stop
            return cid, None, None
        except Exception:
            return "", None, None

    def overlaps_higher_priority(cid: str, start: datetime, stop: datetime, higher: dict[str, list[tuple[datetime, datetime]]]) -> bool:
        return any(start < existing_stop and stop > existing_start for existing_start, existing_stop in higher.get(cid, []))

    ordered_sources: list[Path] = []
    if valid_base:
        ordered_sources.append(Path(base_epg_path))
    for candidate in fallback_paths:
        if candidate.exists() and candidate.stat().st_size:
            if not any(candidate.resolve() == existing.resolve() for existing in ordered_sources):
                ordered_sources.append(candidate)

    for source_index, source_path in enumerate(ordered_sources):
        _raise_if_cancelled(cancel_check)
        # Parse all selected IDs from every fallback source. The compact public
        # caches make this cheap, and it allows a public guide to fill an evening
        # hole even when the provider already supplied morning listings.
        source_attrs, channels, programmes, channel_ids, _programme_ids = _filtered_provider_xmltv(
            source_path,
            allowed,
            cancel_check=cancel_check,
        )
        if source_index == 0 and source_attrs:
            attrs = source_attrs
        for fragment in channels:
            try:
                element = ElementTree.fromstring(fragment)
                cid = str(element.attrib.get("id", "")).strip()
            except Exception:
                cid = ""
            if cid and cid not in supplied_channels:
                provider_channels.append(fragment)
                supplied_channels.add(cid)
        supplied_channels.update(channel_ids)

        # Snapshot only the prior sources for overlap checks so odd overlaps
        # inside one provider's own guide are preserved exactly as supplied.
        higher_priority_intervals = {cid: list(values) for cid, values in accepted_intervals.items()}
        source_intervals: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
        for fragment in programmes:
            cid, start, stop = programme_window(fragment)
            if not cid:
                continue
            if source_index > 0:
                if start and stop:
                    if overlaps_higher_priority(cid, start, stop, higher_priority_intervals):
                        continue
                elif cid in supplied_programmes:
                    # Untimed fallback rows cannot be proven to fill a gap, so
                    # keep the higher-priority guide when one already exists.
                    continue
            provider_programmes.append(fragment)
            supplied_programmes.add(cid)
            if start and stop:
                source_intervals[cid].append((start, stop))
        for cid, windows in source_intervals.items():
            accepted_intervals[cid].extend(windows)

    overlay_root = ElementTree.fromstring(sports_xmltv)
    sports_channels = [
        ElementTree.tostring(child, encoding="utf-8")
        for child in overlay_root
        if child.tag.rsplit("}", 1)[-1] == "channel"
    ]
    sports_programmes = [
        ElementTree.tostring(child, encoding="utf-8")
        for child in overlay_root
        if child.tag.rsplit("}", 1)[-1] == "programme"
    ]

    root = ElementTree.Element("tv", attrs or {
        "generator-info-name": XMLTV_GENERATOR_NAME,
        "source-info-name": "Filtered provider/public guide plus generated sports guide",
    })
    shell = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    if shell.endswith(b" />"):
        opening = shell[:-3] + b">"
    else:
        opening = shell.rsplit(b"</tv>", 1)[0]

    fragments = [*provider_channels, *sports_channels, *provider_programmes, *sports_programmes]
    if not fragments:
        return opening + b"</tv>"
    return opening + b"\n" + b"\n".join(fragments) + b"\n</tv>"


def _write_prepared_epg_files(
    generated: list[dict],
    settings: dict,
    *,
    base_epg_path: Path | None,
    base_channel_ids: set[str] | None,
    fallback_epg_paths: Iterable[Path] | None = None,
    sports_epg_path: Path | None,
    combined_epg_path: Path | None,
    generated_at: datetime,
    cancel_check: CancelCheck = None,
) -> list[tuple[Path, Path]]:
    """Write validated temporary XMLTV files and return (temp, final) pairs."""
    prepared: list[tuple[Path, Path]] = []
    try:
        _raise_if_cancelled(cancel_check)
        sports_bytes = build_sports_xmltv(generated, settings, generated_at=generated_at)
        ElementTree.fromstring(sports_bytes)
        payloads = (
            (sports_epg_path, sports_bytes),
            (
                combined_epg_path,
                build_combined_xmltv(
                    base_epg_path,
                    sports_bytes,
                    base_channel_ids,
                    fallback_epg_paths=fallback_epg_paths,
                    cancel_check=cancel_check,
                ),
            ),
        )
        for destination, payload in payloads:
            _raise_if_cancelled(cancel_check)
            if not destination:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp = destination.with_name(destination.name + ".tmp")
            temp.write_bytes(payload)
            prepared.append((temp, destination))
        return prepared
    except Exception:
        for temp, _destination in prepared:
            temp.unlink(missing_ok=True)
        raise


def rebuild_epg_exports(
    db_path: Path | str,
    *,
    base_epg_path: Path | None,
    base_channel_ids: set[str] | None = None,
    fallback_epg_paths: Iterable[Path] | None = None,
    sports_epg_path: Path,
    combined_epg_path: Path,
) -> None:
    """Recreate guide exports from persisted generated rows, such as at startup."""
    settings = get_settings(db_path)
    rows = generated_rows(db_path)
    generated_at = datetime.now().astimezone()
    prepared = _write_prepared_epg_files(
        rows,
        settings,
        base_epg_path=base_epg_path,
        base_channel_ids=base_channel_ids,
        fallback_epg_paths=fallback_epg_paths,
        sports_epg_path=sports_epg_path,
        combined_epg_path=combined_epg_path,
        generated_at=generated_at,
    )
    for temp, destination in prepared:
        temp.replace(destination)



def _local_xml_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _xmltv_index(path: Path | None, fallback_tz: ZoneInfo) -> dict:
    result = {
        "exists": bool(path and path.exists()),
        "channels": set(),
        "programmes": defaultdict(list),
        "error": "",
        "size": 0,
        "modified": "",
    }
    if not path or not path.exists():
        return result
    try:
        result["size"] = path.stat().st_size
        result["modified"] = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
        if path.suffix.lower() == ".gz":
            with gzip.open(path, "rb") as handle:
                root = ElementTree.parse(handle).getroot()
        else:
            root = ElementTree.parse(path).getroot()
        for child in root:
            tag = _local_xml_name(child.tag)
            if tag == "channel":
                channel_id = str(child.attrib.get("id", "") or "").strip()
                if channel_id:
                    result["channels"].add(channel_id)
            elif tag == "programme":
                channel_id = str(child.attrib.get("channel", "") or "").strip()
                if not channel_id:
                    continue
                start = _parse_xmltv_time(str(child.attrib.get("start", "") or ""), fallback_tz)
                stop = _parse_xmltv_time(str(child.attrib.get("stop", "") or ""), fallback_tz)
                if start and stop and stop > start:
                    result["programmes"][channel_id].append((start, stop))
        return result
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result


def _playlist_tvg_ids(path: Path | None) -> tuple[set[str], str]:
    if not path or not path.exists():
        return set(), "playlist file is missing"
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        return {
            match.group(1).strip()
            for match in re.finditer(r'\btvg-id="([^"]+)"', text, flags=re.I)
            if match.group(1).strip()
        }, ""
    except Exception as exc:
        return set(), f"{type(exc).__name__}: {exc}"


def validate_guide_exports(
    db_path: Path | str,
    *,
    playlist_path: Path | None,
    sports_epg_path: Path | None,
    combined_epg_path: Path | None,
) -> dict:
    """Validate the exact files served to Jellyfin without exposing stream URLs."""
    settings = get_settings(db_path)
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    rows = generated_rows(db_path)
    expected_ids = {str(row.get("tvg_id", "") or "").strip() for row in rows}
    expected_ids.discard("")

    playlist_ids, playlist_error = _playlist_tvg_ids(playlist_path)
    sports_index = _xmltv_index(sports_epg_path, timezone)
    combined_index = _xmltv_index(combined_epg_path, timezone)

    missing_playlist = sorted(expected_ids - playlist_ids)
    missing_sports_channels = sorted(expected_ids - sports_index["channels"])
    missing_sports_programmes = sorted(
        channel_id for channel_id in expected_ids if not sports_index["programmes"].get(channel_id)
    )
    missing_combined_channels = sorted(expected_ids - combined_index["channels"])
    missing_combined_programmes = sorted(
        channel_id for channel_id in expected_ids if not combined_index["programmes"].get(channel_id)
    )

    uncovered_event_starts = []
    for row in rows:
        channel_id = str(row.get("tvg_id", "") or "").strip()
        event_start = _parse_iso_datetime(row.get("event_start"), timezone)
        if not channel_id or not event_start:
            continue
        event_start = event_start.astimezone(timezone)
        intervals = sports_index["programmes"].get(channel_id, [])
        if not any(start <= event_start < stop for start, stop in intervals):
            uncovered_event_starts.append(channel_id)

    errors = [value for value in (playlist_error, sports_index["error"], combined_index["error"]) if value]
    ok = not any(
        (
            errors,
            missing_playlist,
            missing_sports_channels,
            missing_sports_programmes,
            missing_combined_channels,
            missing_combined_programmes,
            uncovered_event_starts,
        )
    )
    return {
        "ok": ok,
        "generated_channels": len(expected_ids),
        "playlist_sports_ids": len(expected_ids & playlist_ids),
        "sports_xml_channels": len(expected_ids & sports_index["channels"]),
        "sports_xml_programme_channels": sum(
            1 for channel_id in expected_ids if sports_index["programmes"].get(channel_id)
        ),
        "combined_xml_channels": len(expected_ids & combined_index["channels"]),
        "combined_xml_programme_channels": sum(
            1 for channel_id in expected_ids if combined_index["programmes"].get(channel_id)
        ),
        "missing_playlist_ids": missing_playlist,
        "missing_sports_channels": missing_sports_channels,
        "missing_sports_programmes": missing_sports_programmes,
        "missing_combined_channels": missing_combined_channels,
        "missing_combined_programmes": missing_combined_programmes,
        "uncovered_event_starts": sorted(uncovered_event_starts),
        "errors": errors,
        "sports_xml_size": sports_index["size"],
        "sports_xml_modified": sports_index["modified"],
        "combined_xml_size": combined_index["size"],
        "combined_xml_modified": combined_index["modified"],
    }


def _record_scan(
    db_path: Path | str,
    *,
    started_at: str,
    status: str,
    message: str,
    event_count: int,
    channel_count: int,
    target_date: str,
    trigger: str,
) -> None:
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO sports_scan_runs
                (started_at, finished_at, status, message,
                 event_count, channel_count, target_date, trigger)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                started_at,
                _now_iso(),
                status,
                message,
                event_count,
                channel_count,
                target_date,
                trigger,
            ),
        )
        conn.commit()


def begin_scan_state(
    db_path: Path | str,
    *,
    trigger: str = "manual",
    started_at: str | None = None,
    stage: str = "Starting sports update",
) -> dict:
    """Persist the active scan so reopening the browser keeps its status."""
    init_db(db_path)
    started = started_at or _now_iso()
    updated = _now_iso()
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO sports_scan_state
                (id, running, started_at, updated_at, stage, trigger)
            VALUES (1, 1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                running = 1,
                started_at = excluded.started_at,
                updated_at = excluded.updated_at,
                stage = excluded.stage,
                trigger = excluded.trigger
            """,
            (started, updated, str(stage).strip()[:160], str(trigger).strip()[:40] or "manual"),
        )
        conn.commit()
    return scan_state(db_path)


def update_scan_stage(db_path: Path | str, stage: str) -> dict:
    """Update the human-readable stage for an active scan."""
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            UPDATE sports_scan_state
            SET updated_at = ?, stage = ?
            WHERE id = 1 AND running = 1
            """,
            (_now_iso(), str(stage).strip()[:160]),
        )
        conn.commit()
    return scan_state(db_path)


def finish_scan_state(db_path: Path | str) -> None:
    """Mark the active scan complete without deleting its start metadata."""
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            UPDATE sports_scan_state
            SET running = 0, updated_at = ?, stage = ''
            WHERE id = 1
            """,
            (_now_iso(),),
        )
        conn.commit()


def scan_state(db_path: Path | str, now: datetime | None = None) -> dict:
    """Return credential-free persistent scan activity for the browser."""
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT running, started_at, updated_at, stage, trigger
            FROM sports_scan_state
            WHERE id = 1
            """
        ).fetchone()
    if not row:
        return {
            "running": False,
            "started_at": None,
            "updated_at": None,
            "stage": "",
            "trigger": "manual",
            "elapsed_seconds": 0,
        }

    payload = dict(row)
    payload["running"] = bool(payload.get("running"))
    elapsed = 0
    if payload["running"] and payload.get("started_at"):
        try:
            started = datetime.fromisoformat(str(payload["started_at"]))
            current = now or datetime.now().astimezone()
            if started.tzinfo is None:
                started = started.replace(tzinfo=current.tzinfo)
            elapsed = max(0, int((current - started).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            elapsed = 0
    payload["elapsed_seconds"] = elapsed
    return payload


def recover_interrupted_scan(db_path: Path | str) -> bool:
    """Convert a stale running state left by a process restart into a failure."""
    state = scan_state(db_path)
    if not state.get("running"):
        return False
    record_scan_failure(
        db_path,
        "The previous sports update was interrupted by an app restart.",
        trigger=str(state.get("trigger") or "manual"),
        started_at=str(state.get("started_at") or _now_iso()),
    )
    finish_scan_state(db_path)
    return True


def record_scan_cancelled(
    db_path: Path | str,
    trigger: str = "manual",
    *,
    started_at: str | None = None,
) -> None:
    settings = get_settings(db_path)
    now = datetime.now().astimezone()
    _record_scan(
        db_path,
        started_at=started_at or _now_iso(),
        status="cancelled",
        message="Sports update cancelled. Existing sports channels were kept.",
        event_count=0,
        channel_count=len(generated_rows(db_path, include_cached=True)),
        target_date=_sports_day(now, settings).isoformat(),
        trigger=trigger,
    )


def record_scan_failure(
    db_path: Path | str,
    message: str,
    trigger: str = "scheduled",
    *,
    started_at: str | None = None,
) -> None:
    settings = get_settings(db_path)
    now = datetime.now().astimezone()
    _record_scan(
        db_path,
        started_at=started_at or _now_iso(),
        status="failed",
        message=message,
        event_count=0,
        channel_count=0,
        target_date=_sports_day(now, settings).isoformat(),
        trigger=trigger,
    )


def _is_sd_channel(channel: dict) -> bool:
    group = str(channel.get("group", "") or "").strip().upper()
    name = str(channel.get("name", "") or "").strip()
    return group == "LOW BANDWIDTH" or bool(re.search(r"(?:^|[ |_-])SD(?:$|[ |_-])", name, re.I))


def _classification_id(event: dict) -> str:
    return str(event.get("league_id") or event.get("sport_id") or "sports")


def _classification_label(classification_id: str) -> str:
    return LEAGUE_NAMES.get(
        classification_id,
        SPORT_NAMES.get(classification_id, classification_id.replace("-", " ").title() or "Sports"),
    )


def _block_index_map(classification_ids: Iterable[str]) -> dict[str, int]:
    """Return stable primary block indexes, including provider-only classes."""
    mapping = dict(LEAGUE_BLOCK_INDEX)
    unknown = sorted(
        {str(value) for value in classification_ids if value and value not in mapping}
    )
    next_index = len(mapping)
    for classification_id in unknown:
        mapping[classification_id] = next_index
        next_index += 1
    return mapping


def assigned_channel_number(
    classification_id: str,
    event_index: int,
    feed_index: int,
    *,
    start_channel: int = 1000,
    channels_per_event: int = 10,
    block_index: int | None = None,
) -> int:
    """Assign one feed inside a league's 1,000-channel block.

    A 10-feed event size yields 100 normal event slots. Overflow is moved to a
    distant continuation block instead of spilling into the next league.
    """
    per_event = max(1, int(channels_per_event))
    capacity = max(1, LEAGUE_BLOCK_SIZE // per_event)
    resolved_index = (
        int(block_index)
        if block_index is not None
        else LEAGUE_BLOCK_INDEX.get(classification_id, len(LEAGUE_BLOCK_ORDER))
    )
    event_index = max(0, int(event_index))
    feed_index = max(0, int(feed_index))
    block_number = event_index // capacity
    slot_index = event_index % capacity
    if block_number == 0:
        block_start = int(start_channel) + resolved_index * LEAGUE_BLOCK_SIZE
    else:
        block_start = (
            int(start_channel)
            + OVERFLOW_BLOCK_OFFSET
            + resolved_index * 10_000
            + (block_number - 1) * LEAGUE_BLOCK_SIZE
        )
    return block_start + slot_index * per_event + feed_index


def effective_start_channel(configured_start: int, manual_channel_count: int) -> int:
    """Return a sports block start that cannot collide with manual numbering.

    Manual channels are numbered sequentially from 1 in saved order. Sports
    channels occupy 1,000-channel league blocks. When the configured sports
    start falls inside the manual range, move the complete sports block map
    upward by whole 1,000-channel blocks while preserving the user's configured
    offset.
    """
    configured = max(1, int(configured_start))
    manual_count = max(0, int(manual_channel_count))
    if manual_count < configured:
        return configured
    blocks_to_skip = ((manual_count - configured) // LEAGUE_BLOCK_SIZE) + 1
    return configured + blocks_to_skip * LEAGUE_BLOCK_SIZE


def numbering_plan(settings: dict) -> dict:
    start = int(settings.get("start_channel", 1000))
    per_event = int(settings.get("channels_per_event", 10))
    capacity = max(1, LEAGUE_BLOCK_SIZE // max(1, per_event))
    blocks = []
    for league_id, name, sport_id, _subtitle, _aliases, _patterns in LEAGUE_DEFINITIONS:
        index = LEAGUE_BLOCK_INDEX[league_id]
        block_start = start + index * LEAGUE_BLOCK_SIZE
        blocks.append(
            {
                "id": league_id,
                "name": name,
                "sport_id": sport_id,
                "sport": SPORT_NAMES.get(sport_id, sport_id),
                "index": index,
                "start": block_start,
                "end": block_start + LEAGUE_BLOCK_SIZE - 1,
            }
        )
    return {
        "start_channel": start,
        "league_block_size": LEAGUE_BLOCK_SIZE,
        "channels_per_event": per_event,
        "events_per_primary_block": capacity,
        "overflow_start_offset": OVERFLOW_BLOCK_OFFSET,
        "blocks": blocks,
    }


def scan_channels(
    db_path: Path | str,
    channels: list[dict],
    epg_path: Path | None = None,
    *,
    provider_epg_sources: list[tuple[Path, list[dict]]] | None = None,
    sports_epg_path: Path | None = None,
    combined_epg_path: Path | None = None,
    trigger: str = "manual",
    now: datetime | None = None,
    started_at: str | None = None,
    base_channel_ids: set[str] | None = None,
    fallback_epg_paths: Iterable[Path] | None = None,
    manual_channel_count: int = 0,
    cancel_check: CancelCheck = None,
) -> dict:
    scan_clock = perf_counter()
    scan_timings: dict[str, float] = {}
    pipeline_trace: list[str] = []

    def record_timing(name: str, started: float) -> None:
        scan_timings[name] = round(perf_counter() - started, 3)

    init_db(db_path)
    _raise_if_cancelled(cancel_check)
    started_at = started_at or _now_iso()
    settings = get_settings(db_path)
    # Freeze one timestamp for the entire scan. Long provider/XMLTV parses must
    # not change event eligibility halfway through the same update.
    scan_anchor = now or datetime.now().astimezone()
    target_date = _sports_day(scan_anchor, settings).isoformat()

    if not settings.get("enabled"):
        result = {
            "ok": True,
            "count": len(generated_rows(db_path, include_cached=True)),
            "events": 0,
            "message": "Sports Automation is disabled; cached generated channels remain hidden for up to 24 hours.",
            "target_date": target_date,
        }
        _record_scan(
            db_path,
            started_at=started_at,
            status="skipped",
            message=result["message"],
            event_count=0,
            channel_count=result["count"],
            target_date=target_date,
            trigger=trigger,
        )
        return result

    if settings.get("exclude_sd"):
        channels = [channel for channel in channels if not _is_sd_channel(channel)]

    rules = [rule for rule in get_rules(db_path) if rule["enabled"]]
    everything_mode = bool(settings.get("everything_mode"))
    diagnostics = _new_scan_diagnostics()

    index_started = perf_counter()
    team_lookup = _build_team_lookup(db_path)
    team_feed_map, team_feed_channel_ids = _team_feed_index(channels)
    rule_index = _build_rule_index(rules)
    team_catalog = {item["id"]: item for item in team_lookup.get("teams", [])}
    record_timing("index_build", index_started)

    epg_started = perf_counter()
    epg_events: list[dict] = []
    if provider_epg_sources is None:
        epg_events.extend(
            _epg_events(
                db_path,
                epg_path,
                channels,
                settings,
                scan_anchor,
                diagnostics,
                cancel_check,
                team_lookup=team_lookup,
            )
        )
    else:
        for source_index, (source_epg_path, source_channels) in enumerate(provider_epg_sources):
            if source_index % 5 == 0:
                _raise_if_cancelled(cancel_check)
            epg_events.extend(
                _epg_events(
                    db_path,
                    source_epg_path,
                    source_channels,
                    settings,
                    scan_anchor,
                    diagnostics,
                    cancel_check,
                    team_lookup=team_lookup,
                    source_priority=source_index,
                )
            )

    record_timing("epg_parse", epg_started)

    m3u_started = perf_counter()
    m3u_events = _m3u_events(
        db_path,
        channels,
        settings,
        scan_anchor,
        diagnostics,
        cancel_check,
        team_lookup=team_lookup,
        team_feed_channel_ids=team_feed_channel_ids,
    )
    record_timing("m3u_parse", m3u_started)

    history_started = perf_counter()
    previous_anchors = _previous_generated_event_anchors(
        db_path,
        settings,
        scan_anchor,
        team_lookup=team_lookup,
    )
    record_timing("history_anchors", history_started)

    api_started = perf_counter()
    schedule_rows = schedule_api_events_for_window(db_path, scan_anchor)
    api_anchors = _schedule_api_anchor_events(schedule_rows, settings, team_lookup)
    provider_events = _apply_schedule_api_identity(
        [*previous_anchors, *m3u_events, *epg_events],
        api_anchors,
    )
    # Cancelled/postponed API events never become generated channels. Active
    # canonical games are included as authoritative timing anchors, even when
    # the provider exposes only static team feeds rather than timed event rows.
    active_api_anchors = [
        event for event in api_anchors
        if str(event.get("api_status_short") or "").upper() not in {"POST", "CANC", "ABD", "SUSP"}
    ]
    record_timing("schedule_mapping", api_started)

    merge_started = perf_counter()
    events = _merge_events(
        [*active_api_anchors, *provider_events],
        cancel_check,
        settings,
    )
    record_timing("logical_merge", merge_started)
    filter_started = perf_counter()
    window_start, window_end, _ = _target_window(scan_anchor, settings)
    untimed_skipped = sum(
        1 for event in events if not _event_has_usable_timing(event)
    )
    events = [
        event
        for event in events
        if _event_has_usable_timing(event)
        and _event_overlaps_window(event, window_start, window_end)
        and not _event_is_stale(event, scan_anchor)
    ]
    record_timing("window_filter", filter_started)

    _raise_if_cancelled(cancel_check)
    rules_started = perf_counter()
    selected_events = []
    for index, event in enumerate(events):
        if index % 100 == 0:
            _raise_if_cancelled(cancel_check)
        if everything_mode:
            event["matched_rule"] = {
                "id": 0,
                "scope_type": "sport",
                "scope_id": event.get("sport_id") or event.get("league_id") or "sports",
                "display_name": "Everything Mode",
                "feed_preference": "best",
                "enabled": 1,
            }
            event["matched_rules"] = [event["matched_rule"]]
            event["expanded_feeds"] = False
        else:
            matched = _matching_rules(event, rule_index)
            if not matched:
                continue
            controlling_rule, expanded_feeds = _select_controlling_rule(event, matched)
            event["matched_rules"] = matched
            event["matched_rule"] = controlling_rule
            event["expanded_feeds"] = expanded_feeds
        selected_events.append(event)
    record_timing("rule_matching", rules_started)
    pipeline_trace.append("sports_scan_match")

    classification_ids = {_classification_id(event) for event in selected_events}
    classification_blocks = _block_index_map(classification_ids)
    selected_events.sort(
        key=lambda event: (
            classification_blocks[_classification_id(event)],
            event.get("start") or datetime.max.replace(tzinfo=ZoneInfo("UTC")),
            event.get("display_name", "").lower(),
        )
    )

    configured_start_number = int(settings.get("start_channel", 1000))
    start_number = effective_start_channel(configured_start_number, manual_channel_count)
    block_size = int(settings.get("channels_per_event", 10))
    group_title = str(settings.get("group_title", "Sports Today"))
    generated = []
    event_positions: dict[str, int] = defaultdict(int)

    feed_started = perf_counter()
    for event_index, event in enumerate(selected_events):
        if event_index % 50 == 0:
            _raise_if_cancelled(cancel_check)
        classification_id = _classification_id(event)
        classification_event_index = event_positions[classification_id]
        event_positions[classification_id] += 1
        rule = event["matched_rule"]
        feeds = _build_feeds(event, team_feed_map, rule, settings)[:block_size]
        for feed_index, feed in enumerate(feeds):
            channel = feed["channel"]
            feed_type = feed["feed_type"]
            feed_label, subtitle = _feed_label(feed_type, event, feed.get("team_id", ""))
            start_text = ""
            if event.get("start"):
                start_text = event["start"].astimezone(
                    ZoneInfo(str(settings.get("timezone", "America/New_York")))
                ).strftime("%-I:%M %p")
            if start_text:
                subtitle = f"{subtitle} • {start_text}"
            league_label = _classification_label(classification_id)
            display_name = f"{league_label} • {event['display_name']} — {feed_label}"
            assigned = assigned_channel_number(
                classification_id,
                classification_event_index,
                feed_index,
                start_channel=start_number,
                channels_per_event=block_size,
                block_index=classification_blocks[classification_id],
            )
            logo = str(channel.get("tvg_logo", "") or "")
            if not logo:
                preferred_team = team_catalog.get(feed.get("team_id", ""))
                if preferred_team:
                    logo = preferred_team.get("logo_url", "")
            source_channel_key = str(channel.get("url", "") or "")
            event_start = event.get("start")
            event_end = _event_end(event)
            item = {
                "channel_key": f"sports:{event['event_key']}:{feed_type}:{source_channel_key}",
                "source_channel_key": source_channel_key,
                "event_key": event["event_key"],
                "league_id": classification_id,
                "display_name": display_name,
                "subtitle": subtitle,
                "feed_type": feed_type,
                "assigned_number": assigned,
                "group_title": group_title,
                "url": source_channel_key,
                "playback_url": generated_stream_path(assigned),
                "tvg_id": _generated_tvg_id(assigned),
                "source_tvg_id": str(channel.get("tvg_id", "") or ""),
                "tvg_logo": logo,
                "event_title": event.get("display_name", ""),
                "event_start": event_start.isoformat() if event_start else None,
                "event_end": event_end.isoformat() if event_end else None,
                "is_replay": bool(event.get("is_replay")),
                "epg_programme": _serialize_epg_programme(event),
            }
            item["raw"] = _generated_raw(channel, item)
            generated.append(item)
    record_timing("feed_selection", feed_started)
    pipeline_trace.append("channel_build")

    generated_at_dt = scan_anchor.astimezone()
    generated_at = generated_at_dt.isoformat(timespec="seconds")
    guide_started = perf_counter()
    prepared_epg = _write_prepared_epg_files(
        generated,
        settings,
        base_epg_path=epg_path,
        base_channel_ids=base_channel_ids,
        fallback_epg_paths=fallback_epg_paths,
        sports_epg_path=sports_epg_path,
        combined_epg_path=combined_epg_path,
        generated_at=generated_at_dt,
        cancel_check=cancel_check,
    )
    record_timing("guide_generation", guide_started)
    _raise_if_cancelled(cancel_check)
    persist_started = perf_counter()
    installed_epg: list[tuple[Path, Path | None]] = []
    with closing(_connect(db_path)) as conn:
        # Database rows and guide exports are replaced as one operation. If an
        # export cannot be installed, SQLite rolls back and the old guide files
        # are restored, preserving yesterday's working lineup.
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM sports_generated")
            for item in generated:
                conn.execute(
                    """
                    INSERT INTO sports_generated
                        (channel_key, source_channel_key, event_key, league_id,
                         display_name, subtitle, feed_type, assigned_number,
                         group_title, url, tvg_id, source_tvg_id, tvg_logo, raw_json,
                         event_title, event_start, event_end, is_replay,
                         epg_programme_json, generated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["channel_key"],
                        item["source_channel_key"],
                        item["event_key"],
                        item["league_id"],
                        item["display_name"],
                        item["subtitle"],
                        item["feed_type"],
                        item["assigned_number"],
                        item["group_title"],
                        item["url"],
                        item["tvg_id"],
                        item["source_tvg_id"],
                        item["tvg_logo"],
                        json.dumps(item["raw"]),
                        item["event_title"],
                        item["event_start"],
                        item["event_end"],
                        1 if item["is_replay"] else 0,
                        json.dumps(item.get("epg_programme") or {}),
                        generated_at,
                    ),
                )

            for temp_path, destination in prepared_epg:
                backup_path = None
                if destination.exists():
                    backup_path = destination.with_name(destination.name + ".previous")
                    backup_path.unlink(missing_ok=True)
                    destination.replace(backup_path)
                try:
                    temp_path.replace(destination)
                except Exception:
                    if backup_path and backup_path.exists():
                        backup_path.replace(destination)
                    raise
                installed_epg.append((destination, backup_path))
            conn.commit()
        except Exception:
            conn.rollback()
            for destination, backup_path in reversed(installed_epg):
                destination.unlink(missing_ok=True)
                if backup_path and backup_path.exists():
                    backup_path.replace(destination)
            raise
        finally:
            for temp_path, _destination in prepared_epg:
                temp_path.unlink(missing_ok=True)
            for _destination, backup_path in installed_epg:
                if backup_path:
                    backup_path.unlink(missing_ok=True)
    record_timing("persist", persist_started)
    pipeline_trace.append("epg_publish")
    scan_timings["total"] = round(perf_counter() - scan_clock, 3)

    malformed_count = _malformed_count(diagnostics)
    mode_text = " detected" if everything_mode else " matching"
    message = f"Generated {len(generated)} channels for {len(selected_events)}{mode_text} events."
    if malformed_count:
        message += (
            f" Skipped {malformed_count} malformed provider "
            f"entr{'y' if malformed_count == 1 else 'ies'}."
        )
        _log_malformed_summary(diagnostics)
    if untimed_skipped:
        message += (
            f" Skipped {untimed_skipped} untimed provider event"
            f"{'s' if untimed_skipped != 1 else ''} without XMLTV schedule confirmation."
        )
    print(
        "Sports scan timings: "
        + ", ".join(f"{name}={seconds:.3f}s" for name, seconds in scan_timings.items())
        + f"; team_cache={team_lookup.get('cache_hits', 0)} hits/"
          f"{team_lookup.get('cache_misses', 0)} misses"
        + f"; provider_channels={len(channels)}; epg_events={len(epg_events)}; "
          f"m3u_events={len(m3u_events)}; history_anchors={len(previous_anchors)}; "
          f"schedule_api_events={len(api_anchors)}; logical_events={len(events)}; "
          f"selected_events={len(selected_events)}; generated_channels={len(generated)}"
    )
    _record_scan(
        db_path,
        started_at=started_at,
        status="success",
        message=message,
        event_count=len(selected_events),
        channel_count=len(generated),
        target_date=target_date,
        trigger=trigger,
    )
    return {
        "ok": True,
        "count": len(generated),
        "events": len(selected_events),
        "generated_at": generated_at,
        "target_date": target_date,
        "message": message,
        "skipped_entries": malformed_count,
        "malformed_m3u": diagnostics.get("malformed_m3u", 0),
        "malformed_epg": diagnostics.get("malformed_epg", 0),
        "untimed_skipped": untimed_skipped,
        "guide_channels": len(generated),
        "everything_mode": everything_mode,
        "timings": scan_timings,
        "pipeline_trace": pipeline_trace,
        "scan_metrics": {
            "provider_channels": len(channels),
            "epg_events": len(epg_events),
            "m3u_events": len(m3u_events),
            "history_anchors": len(previous_anchors),
            "schedule_api_events": len(api_anchors),
            "logical_events": len(events),
            "selected_events": len(selected_events),
            "generated_channels": len(generated),
            "team_cache_hits": int(team_lookup.get("cache_hits", 0)),
            "team_cache_misses": int(team_lookup.get("cache_misses", 0)),
        },
        "numbering": {
            "configured_start_channel": configured_start_number,
            "effective_start_channel": start_number,
            "manual_channel_count": max(0, int(manual_channel_count)),
            "auto_shifted": start_number != configured_start_number,
            "league_block_size": LEAGUE_BLOCK_SIZE,
            "events_per_primary_block": max(1, LEAGUE_BLOCK_SIZE // max(1, block_size)),
            "used_blocks": [
                {
                    "id": classification_id,
                    "name": _classification_label(classification_id),
                    "index": classification_blocks[classification_id],
                    "events": event_positions[classification_id],
                    "start": start_number + classification_blocks[classification_id] * LEAGUE_BLOCK_SIZE,
                    "end": start_number + (classification_blocks[classification_id] + 1) * LEAGUE_BLOCK_SIZE - 1,
                }
                for classification_id in sorted(classification_ids, key=lambda value: classification_blocks[value])
            ],
        },
    }


def generated_rows(
    db_path: Path | str,
    *,
    include_cached: bool = False,
    now: datetime | None = None,
) -> list[dict]:
    """Return generated rows visible to clients, or the disabled cache when requested."""
    init_db(db_path)
    purge_expired_disabled_cache(db_path, now)
    if not include_cached and not get_settings(db_path).get("enabled"):
        return []
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT id, channel_key, source_channel_key, event_key, league_id,
                   display_name, subtitle, feed_type, assigned_number,
                   group_title, url, tvg_id, source_tvg_id, tvg_logo, raw_json,
                   event_title, event_start, event_end, is_replay,
                   epg_programme_json, generated_at
            FROM sports_generated
            ORDER BY assigned_number
            """
        ).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        item["raw"] = _json_load(item.pop("raw_json"), [])
        if item["raw"]:
            item["raw"][-1] = generated_stream_path(int(item["assigned_number"]))
        item["epg_programme"] = _json_load(
            item.pop("epg_programme_json", "{}"), {}
        )
        output.append(item)
    return output



def generated_stream_target(db_path: Path | str, assigned_number: int) -> str:
    """Resolve a generated slot to its current provider playback URL."""
    init_db(db_path)
    if not get_settings(db_path).get("enabled"):
        return ""
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT url FROM sports_generated WHERE assigned_number = ?",
            (int(assigned_number),),
        ).fetchone()
    return str(row["url"] or "").strip() if row else ""

def generated_channel_payloads(db_path: Path | str) -> list[dict]:
    output = []
    for index, row in enumerate(generated_rows(db_path), start=1):
        output.append(
            {
                "id": -index,
                "key": row["channel_key"],
                "name": row["display_name"],
                "group": row["group_title"],
                "url": generated_stream_path(int(row["assigned_number"])),
                "raw": row["raw"],
                "tvg_id": row["tvg_id"],
                "tvg_name": row["display_name"],
                "tvg_logo": row["tvg_logo"],
                "tvg_chno": str(row["assigned_number"]),
                "sports_subtitle": row["subtitle"],
                "sports_feed_type": row["feed_type"],
                "sports_event_key": row["event_key"],
                "is_sports_generated": True,
            }
        )
    return output


def last_scan(db_path: Path | str) -> dict | None:
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT id, started_at, finished_at, status, message,
                   event_count, channel_count, target_date, trigger
            FROM sports_scan_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    return dict(row) if row else None


def status_payload(db_path: Path | str, now: datetime | None = None) -> dict:
    settings = get_settings(db_path)
    generated = generated_rows(db_path, now=now)
    cache = disabled_cache_status(db_path, now)
    next_run = next_update_at(db_path, now)
    return {
        "settings": settings,
        "rules": get_rules(db_path),
        "catalog": catalog_payload(db_path),
        "generated": [
            {
                "id": row["id"],
                "display_name": row["display_name"],
                "subtitle": row["subtitle"],
                "feed_type": row["feed_type"],
                "assigned_number": row["assigned_number"],
                "event_start": row["event_start"],
                "generated_at": row["generated_at"],
            }
            for row in generated
        ],
        "last_scan": last_scan(db_path),
        "scan": scan_state(db_path, now),
        "next_update": next_run.isoformat(),
        "disabled_cache": cache,
        "numbering": numbering_plan(settings),
        "schedule_api": schedule_api_status(db_path),
    }
