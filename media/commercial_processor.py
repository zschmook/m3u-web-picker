"""Turn a long commercial compilation into a validated, atomic clip batch."""
import json
import math
import re
import subprocess
from pathlib import Path


def probe(path):
    result=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type',
        '-of','json',str(path)],capture_output=True,text=True,timeout=60,check=True)
    info=json.loads(result.stdout);duration=float(info['format']['duration'])
    kinds={row['codec_type'] for row in info.get('streams',[])}
    if not math.isfinite(duration) or duration<=0 or not {'video','audio'}<=kinds:
        raise ValueError('The imported MP4 must contain usable video and audio.')
    return duration,kinds


def boundaries(duration,blacks,silences):
    candidates=[]
    for start,end in blacks:
        if start<2 or end>duration-2: continue
        quiet=any(a<=end+.15 and b>=start-.15 for a,b in silences)
        candidates.append(dict(time=round((start+end)/2*24)/24,black=end-start,silence=quiet,
            evidence='black + silence' if quiet else 'black'))
    for start,end in silences:
        point=(start+end)/2
        if end-start>=.18 and 2<point<duration-2 and not any(abs(row['time']-point)<1 for row in candidates):
            candidates.append(dict(time=round(point*24)/24,black=0,silence=True,evidence='silence only'))
    candidates.sort(key=lambda row:row['time']);clustered=[]
    for row in candidates:
        if clustered and row['time']-clustered[-1]['time']<.75:
            if (row['silence'],row['black'])>(clustered[-1]['silence'],clustered[-1]['black']): clustered[-1]=row
        else: clustered.append(row)
    nodes=[dict(time=0,evidence='file start',black=0,silence=False),*clustered,
           dict(time=duration,evidence='file end',black=0,silence=False)]
    costs=[0]+[float('inf')]*(len(nodes)-1);previous=[None]*len(nodes)
    for end in range(1,len(nodes)):
        for start in range(end-1,-1,-1):
            length=nodes[end]['time']-nodes[start]['time']
            if length<7 and end!=len(nodes)-1: continue
            length_cost=min(abs(length-target)+penalty for target,penalty in
                ((15,0),(30,0),(60,5),(10,2),(20,2),(45,4),(90,15),(120,28)))
            skipped=sum(3 if node['black'] else .25 for node in nodes[start+1:end])
            score=costs[start]+1.5+length_cost+skipped+(.75 if nodes[end]['evidence']=='silence only' else 0)
            if score<costs[end]: costs[end],previous[end]=score,start
    path=[];cursor=len(nodes)-1
    while cursor is not None: path.append(cursor);cursor=previous[cursor]
    path.reverse();rows=[]
    for number,(start,end) in enumerate(zip(path,path[1:]),1):
        left,right=nodes[start],nodes[end];length=right['time']-left['time']
        review=min(abs(length-target) for target in (15,30,60))>2 or 'silence only' in (left['evidence'],right['evidence'])
        rows.append(dict(clip=f'ad_{number:04d}.mp4',start=round(left['time'],6),end=round(right['time'],6),
            duration=round(length,6),review=review,start_evidence=left['evidence'],end_evidence=right['evidence']))
    return rows


def _progress_seconds(line):
    if line.startswith('out_time_us='):
        try:return int(line.split('=',1)[1])/1_000_000
        except ValueError:return None
    return None


def detect(source,duration,progress):
    command=['ffmpeg','-hide_banner','-nostdin','-threads','2','-filter_threads','1','-i',str(source),
        '-vf','scale=320:-2,blackdetect=d=0.06:pix_th=0.12:pic_th=0.95',
        '-af','silencedetect=noise=-35dB:d=0.08','-progress','pipe:1','-nostats','-f','null','-']
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
    blacks=[];silences=[];silence_start=None;processed=0
    for line in process.stdout:
        match=re.search(r'black_start:([\d.]+) black_end:([\d.]+)',line)
        if match: blacks.append((float(match.group(1)),float(match.group(2))))
        match=re.search(r'silence_start: ([\d.]+)',line)
        if match: silence_start=float(match.group(1))
        match=re.search(r'silence_end: ([\d.]+)',line)
        if match and silence_start is not None: silences.append((silence_start,float(match.group(1))));silence_start=None
        value=_progress_seconds(line)
        if value is not None:
            processed=min(duration,value);provisional=boundaries(max(processed,1),blacks,silences)
            found=max(0,len(provisional)-(0 if processed>=duration-.25 else 1))
            progress('scanning',processed,duration,found)
    if process.wait()!=0: raise ValueError('FFmpeg could not scan the imported compilation.')
    return blacks,silences


def encode(source,work,rows,duration,progress):
    times=','.join(str(row['start']) for row in rows[1:])
    command=['ffmpeg','-hide_banner','-nostdin','-y','-i',str(source),'-map','0:v:0','-map','0:a:0',
        '-vf','fps=24','-c:v','libx264','-preset','veryfast','-crf','20','-forced-idr','1','-bf','0',
        *(('-force_key_frames',times) if times else ()),
        '-c:a','aac','-b:a','160k','-ar','44100','-ac','2','-f','segment',
        *(('-segment_times',times,'-segment_time_delta',str(1/48)) if times else ()),
        '-reset_timestamps','1','-segment_start_number','1','-segment_format_options','movflags=+faststart',
        '-progress','pipe:1','-nostats',str(work/'ad_%04d.mp4')]
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
    for line in process.stdout:
        value=_progress_seconds(line)
        if value is not None: progress('cutting',min(duration,value),duration,len(rows))
    if process.wait()!=0: raise ValueError('FFmpeg could not cut the imported compilation.')


def process(source,work,published,progress):
    duration,_=probe(source);progress('scanning',0,duration,0)
    blacks,silences=detect(source,duration,progress);rows=boundaries(duration,blacks,silences)
    if not rows: raise ValueError('No commercial segments were detected.')
    work.mkdir(parents=True,exist_ok=False)
    manifest=dict(source=source.name,duration=duration,clips=rows,
        rules=dict(black_min_seconds=.06,silence_db=-35,silence_min_seconds=.08,
                   preferred_lengths_seconds=[15,30,60],boundary_policy='Detected boundaries only; uncertain lengths flagged.'))
    (work/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    progress('cutting',0,duration,len(rows));encode(source,work,rows,duration,progress)
    verified=[]
    for index,row in enumerate(rows,1):
        actual,kinds=probe(work/row['clip']);difference=actual-row['duration']
        verified.append(dict(clip=row['clip'],expected=row['duration'],actual=actual,difference=difference,av_present={'video','audio'}<=kinds))
        progress('validating',index,len(rows),len(rows))
    if not all(row['av_present'] and abs(row['difference'])<=.25 for row in verified):
        raise ValueError('One or more generated commercials failed validation.')
    (work/'verification.json').write_text(json.dumps(verified,indent=2),encoding='utf-8')
    work.replace(published)
    return dict(duration=duration,clips=len(rows),review=sum(bool(row['review']) for row in rows))
