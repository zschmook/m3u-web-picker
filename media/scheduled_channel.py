"""Persistent broadcast timelines: one schedule shared by playback and the guide."""
from bisect import bisect_right
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import random
import tempfile

SEGMENT_GAP = .1  # Matches the encoder timestamp guard between segments.


def new_schedule(episodes, commercials, now, seed, *, order='random', ads=None):
    if not episodes or (ads is None and not commercials):
        raise ValueError('Episodes and commercials are required')
    if order not in ('random', 'ordered'):
        raise ValueError('Invalid episode order')
    for item in [*episodes, *commercials]:
        if not math.isfinite(item['duration']) or item['duration'] <= 0:
            raise ValueError('Invalid media duration')
    return dict(version=1, seed=seed, epoch=now, cursor=now, until_break=900.0,
                episodes=episodes, commercials=commercials, episode_bag=[], commercial_bag=[],
                episode_cycle=0, commercial_cycle=0, last_episode=None, last_commercial=None,
                sequence=0, segments=[], programmes=[], order=order, ads=ads)


def draw(state, kind):
    bag = state[kind+'_bag']
    assets = state['episodes' if kind == 'episode' else 'commercials']
    if not bag:
        bag.extend(range(len(assets)))
        rng = random.Random(f"{state['seed']}:{kind}:{state[kind+'_cycle']}")
        ordered = kind == 'episode' and state.get('order') == 'ordered'
        if ordered:
            bag.sort(key=lambda i:(assets[i]['season'],assets[i]['episode'],assets[i]['filename']),reverse=True)
        else:
            rng.shuffle(bag)
        if not ordered and len(bag)>1 and bag[-1] == state['last_'+kind]:
            bag[0],bag[-1] = bag[-1],bag[0]
        state[kind+'_cycle'] += 1
    index = bag.pop()
    state['last_'+kind] = index
    return assets[index]


def append_segment(state, kind, asset, start, duration):
    begin = state['cursor']
    state['sequence'] += 1
    state['segments'].append(dict(
        kind=kind, filename=asset['filename'], path=asset.get('path',''), start=start, duration=duration,
        title=asset.get('title',asset['filename']), sequence=state['sequence'],
        label=f"{state['sequence']:07d}_{asset['filename']}_{start:.3f}",
        wall_start=begin, wall_stop=begin+duration+SEGMENT_GAP))
    if asset.get('part'):
        state['segments'][-1]['part'] = asset['part']
    if asset.get('server_id'):
        state['segments'][-1]['server_id'] = asset['server_id']
    state['cursor'] = begin+duration+SEGMENT_GAP


def append_ad(state, role='break'):
    ad = draw(state,'commercial')
    append_segment(state,'commercial',ad,0,ad['duration'])
    state['segments'][-1]['ad_role'] = role


def extend_schedule(state, horizon):
    """Shuffle persists across requests, restarts, and guide horizon extensions."""
    while state['cursor'] < horizon:
        episode = draw(state,'episode')
        begin = state['cursor']
        # A scheduled opening commercial precedes each episode, never each viewer.
        options = state.get('ads')
        enabled = options is None or options.get('enabled',False)
        interval = 900 if options is None else options.get('minutes',15)*60
        count = 3 if options is None else options.get('count',3)
        mode = 'minutes' if options is None else options.get('mode','minutes')
        if enabled and (options is None or options.get('preroll',True)):
            append_ad(state,'preroll')
        start = 0.0
        while start < episode['duration']-.000001:
            if enabled and mode == 'minutes' and state['until_break'] < .000001:
                for _ in range(count):
                    append_ad(state)
                state['until_break'] = interval
            duration = min(state['until_break'],episode['duration']-start) if enabled and mode=='minutes' else episode['duration']-start
            append_segment(state,'episode',episode,start,duration)
            start += duration
            state['until_break'] = max(0,state['until_break']-duration)
        state['episodes_since_break'] = state.get('episodes_since_break',0)+1
        if enabled and mode == 'episodes' and state['episodes_since_break'] >= options.get('episodes',1):
            for _ in range(count):
                append_ad(state)
            state['episodes_since_break'] = 0
        state['programmes'].append(dict(title=episode['title'], filename=episode['filename'],
                                       season=episode['season'], episode=episode['episode'],
                                       start=begin, stop=state['cursor']))
        for key in ('id', 'server_id', 'part', 'release_date', 'year', 'description', 'genres'):
            if key in episode:
                state['programmes'][-1][key] = episode[key]


def trim_schedule(state, before):
    state['segments'] = [s for s in state['segments'] if s['wall_stop']>before]
    state['programmes'] = [p for p in state['programmes'] if p['stop']>before]


def save_schedule(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                                     prefix=path.name+'.',suffix='.tmp',delete=False) as handle:
        json.dump(state,handle,separators=(',',':'))
        handle.flush()
        os.fsync(handle.fileno())
        temporary = handle.name
    os.replace(temporary,path)


def segments_at(state, now):
    """Seek into the scheduled asset, including when 'now' falls inside an ad."""
    segments = state['segments']
    index = bisect_right([s['wall_stop'] for s in segments],now)
    for segment in segments[index:]:
        elapsed = max(0,now-segment['wall_start'])
        if elapsed >= segment['duration']:
            continue  # A timestamp guard is not additional source footage.
        item = dict(segment)
        item['start'] += elapsed
        item['duration'] -= elapsed
        yield item


def programme_payload(programme):
    return dict(title=programme['title'], subtitle='With vintage commercial breaks',
                description='Random Breaking Bad episodes. One opening commercial per episode; three commercials every 15 minutes of show time.',
                categories=['Drama'], season=programme['season'], episode=programme['episode'],
                start=datetime.fromtimestamp(programme['start'],timezone.utc).isoformat(),
                stop=datetime.fromtimestamp(programme['stop'],timezone.utc).isoformat())
