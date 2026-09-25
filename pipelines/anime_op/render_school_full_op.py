"""Resumable serial whole-frame H3 -> SeedVR2 OP production; A before B.

Safety interlock is independent of generation. No local patch/mask compositing.
No aesthetic frame review. Outputs retain the exact source cut timeline.
"""
import argparse
import csv
import ctypes
import datetime as dt
import hashlib
import html
import json
import math
import msvcrt
import os
from pathlib import Path
import shutil
import time
import urllib.error
import urllib.request
import uuid

import op_pipeline as p
import op_graph as graph
import try_seedvr2_1080p as sr

OUT=p.ROOT/'assets/anime_op/school_full_op_v1'
RUN=OUT/'runtime'
DELIVERY=p.ROOT/'deliverables/ai_school_op/完整OP'
PREFIX='video/anime_op/school_full_op_v1/'
STOP=RUN/'STOP_GPU_GUARD.json'
SERVER='http://127.0.0.1:8188'
STATE={}
PLAN={}
NATIVE_ONLY=False
POWER_LIMIT_W=400
COOLDOWN_TO_C=60

def completion_name():return 'native_complete.json' if NATIVE_ONLY else 'complete.json'
def output_name():return 'native_fullframe.mp4' if NATIVE_ONLY else '1080p.mp4'
def delivery_variant(variant):return variant+'_原生' if NATIVE_ONLY else variant
def movie_suffix():return '1024x576' if NATIVE_ONLY else '1080p'

def validate_output(path,frames,audio=False):
    if not NATIVE_ONLY:return sr.validate(path,frames,audio)
    info=p.video_info(path)
    if (info['width'],info['height'],info['frames'],info['fps'],info['has_audio'])!=(1024,576,frames,sr.FPS,audio):
        raise RuntimeError(f'Unexpected native export: {path}: {info}')
    p.run([p.FFMPEG,'-v','error','-i',path,'-f','null','-'])
    return info

def native_extract(source,target,start,end):
    p.run([p.FFMPEG,'-v','error','-y','-i',source,'-vf',
        f'trim=start_frame={start}:end_frame={end},setpts=N*1001/(24000*TB),scale=1024:576:flags=lanczos,setsar=1',
        '-frames:v',end-start,'-r',sr.FPS,'-an','-c:v','libx264','-crf','12','-pix_fmt','yuv420p',target])

class SafetyStop(RuntimeError):pass

def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def stamp():return dt.datetime.now().astimezone().isoformat()
def signature(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def state(**kwargs):
    STATE.update(kwargs,updated=stamp())
    p.write_json(OUT/'status.json',STATE)

def api(path,data=None):
    req=urllib.request.Request(SERVER+path,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=30) as r:
            raw=r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'ComfyUI {path}: {exc.code}: {exc.read().decode()[:3000]}') from exc

def stop_owned(prompt_id):
    q=api('/queue')
    if any(x[1]==prompt_id for x in q['queue_running']):api('/interrupt',{})
    if any(x[1]==prompt_id for x in q['queue_pending']):api('/queue',{'delete':[prompt_id]})

def guard():
    if STOP.exists():raise SafetyStop('GPU interlock latched: '+json.dumps(read(STOP),ensure_ascii=False))
    try:
        g=read(RUN/'gpu_guard_status.json')
        age=time.time()-dt.datetime.fromisoformat(g['time']).timestamp()
        if g.get('tripped') or age>45 or age < -10:raise ValueError(f'Guard stale/tripped ({age:.1f}s)')
        if g['temperature_c']>=87 or g['power_limit_w']>POWER_LIMIT_W+0.5 or g['thermal_slowdown']:raise ValueError('Unsafe telemetry')
        return g
    except Exception as exc:
        p.write_json(STOP,dict(time=stamp(),reason=f'Batch guard check failed: {exc}',automatic_restart=False))
        raise SafetyStop(str(exc)) from exc

def release():
    if p.queue_empty():api('/free',{'unload_models':True,'free_memory':True})

def cooldown():
    if not p.queue_empty():raise RuntimeError('Another ComfyUI job is active; will not overlap GPU jobs')
    release()
    while True:
        g=guard()
        if g['temperature_c']<=COOLDOWN_TO_C:return
        state(stage='cooldown',temperature_c=g['temperature_c'])
        time.sleep(10)

def raw_from_history(entry,prefix):
    root=(p.ROOT/'ComfyUI/output').resolve()
    for node in entry.get('outputs',{}).values():
        for key in ('videos','gifs','images'):
            for output in node.get(key,[]) or []:
                name=output.get('filename','')
                if not name.lower().endswith('.mp4'):continue
                path=(root/output.get('subfolder','')/name).resolve()
                if path.is_relative_to(root) and path.is_file() and path.as_posix().startswith((root/prefix).as_posix()):return path
    base=root/prefix
    matches=sorted(base.parent.glob(base.name+'_*.mp4'))
    if len(matches)==1:return matches[0]
    raise RuntimeError(f'Cannot uniquely resolve result for {prefix}')

def gpu_job(workflow,folder,kind,prefix):
    record=folder/(kind+'_result.json')
    wf_hash=signature(workflow)
    if record.exists():
        r=read(record); raw=Path(r['raw'])
        if r['workflow_sha256']!=wf_hash or not raw.is_file() or p.sha(raw)!=r['sha256']:raise RuntimeError('Recorded result changed: '+str(record))
        return raw
    submission=folder/(kind+'_submission.json')
    if submission.exists():
        sub=read(submission)
        if sub['workflow_sha256']!=wf_hash:raise RuntimeError('Submitted workflow changed')
        prompt_id=sub['prompt_id'];started=sub['started_epoch']
        history=api('/history/'+prompt_id)
        queued=api('/queue')
        if prompt_id not in history and not any(x[1]==prompt_id for x in queued['queue_running']+queued['queue_pending']):
            raise RuntimeError('Prior submission missing from queue/history; inspect before re-rendering: '+prompt_id)
    else:
        cooldown();guard()
        response=api('/prompt',{'prompt':workflow,'client_id':str(uuid.uuid4())})
        prompt_id=response['prompt_id'];started=time.time()
        p.write_json(submission,dict(prompt_id=prompt_id,started=stamp(),started_epoch=started,workflow_sha256=wf_hash,prefix=prefix))
    state(stage=kind,prompt_id=prompt_id)
    peak=0;last_note=0
    while True:
        try:g=guard()
        except SafetyStop:
            stop_owned(prompt_id)
            raise
        hist=api('/history/'+prompt_id)
        if prompt_id in hist:
            entry=hist[prompt_id]
            p.write_json(folder/(kind+'_history.json'),entry)
            status=entry.get('status',{})
            if status.get('status_str')=='error' or not status.get('completed',True):
                raise RuntimeError('ComfyUI generation failed: '+json.dumps(status,ensure_ascii=False)[-3500:])
            raw=raw_from_history(entry,prefix)
            p.write_json(record,dict(raw=str(raw),sha256=p.sha(raw),seconds=time.time()-started,peak_vram_mb=peak,
                prompt_id=prompt_id,workflow_sha256=wf_hash,completed=stamp()))
            release()
            return raw
        peak=max(peak,graph.core.vram_used_mb())
        elapsed=time.time()-started
        if elapsed>7200:
            stop_owned(prompt_id)
            raise RuntimeError('Single-job time limit reached; interrupted only this job')
        if elapsed-last_note>=30:
            print(f'{STATE.get("variant")} {STATE.get("shot")} {kind}: {elapsed:.0f}s, {g["temperature_c"]} C, peak {peak/1024:.1f} GiB',flush=True)
            state(elapsed_seconds=round(elapsed),temperature_c=g['temperature_c'])
            last_note=elapsed
        time.sleep(5)

def link_or_copy(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if p.sha(source)!=p.sha(target):raise RuntimeError('Delivery path already has different data: '+str(target))
        return
    try:os.link(source,target)
    except OSError:shutil.copy2(source,target)

def prepare_reference(s):
    folder=OUT/'shots'/s['id'];folder.mkdir(parents=True,exist_ok=True)
    target=folder/'reference_fullframe.mp4'
    n=s['frames'];g=s['model_frames'];w=PLAN['settings']['width'];h=PLAN['settings']['height']
    if not target.exists():
        vf=f'trim=start_frame={s["start"]}:end_frame={s["end"]},setpts=N*{g-1}/({n-1}*24*TB),fps=24,scale={w}:{h}:flags=lanczos,setsar=1,tpad=stop_mode=clone:stop_duration=0.2'
        p.run([p.FFMPEG,'-v','error','-y','-i',PLAN['source'],'-vf',vf,'-frames:v',g,'-r','24','-an','-c:v','libx264','-crf','12','-pix_fmt','yuv420p',target])
    info=p.video_info(target)
    if (info['frames'],info['width'],info['height'],info['fps'])!=(g,w,h,'24/1'):raise RuntimeError('Invalid prepared whole-frame reference')
    return target

def prepare_audio():
    audio=OUT/'original_audio_master.m4a'
    if not audio.exists():
        p.run([p.FFMPEG,'-v','error','-y','-i',PLAN['source'],'-map','0:a:0','-vn','-t',PLAN['duration'],'-c:a','aac','-b:a','256k',audio])
    return audio

def audio_hash(path):return p.run([p.FFMPEG,'-v','error','-i',path,'-map','0:a:0','-c:a','copy','-f','hash','-hash','sha256','-']).stdout.strip()

def publish_shot(s,variant,target,record):
    folder=target.parent
    preview=folder/('native_review_with_audio.mp4' if NATIVE_ONLY else 'review_with_audio.mp4')
    if not preview.exists():
        p.run([p.FFMPEG,'-v','error','-y','-i',target,'-i',prepare_audio(),'-map','0:v:0','-map','1:a:0','-af',
            f'atrim=start={s["start_seconds"]:.12f}:end={s["end_seconds"]:.12f},asetpts=PTS-STARTPTS',
            '-c:v','copy','-c:a','aac','-b:a','192k','-t',s['frames']*1001/24000,'-movflags','+faststart',preview])
        validate_output(preview,s['frames'],True)
    link_or_copy(preview,DELIVERY/delivery_variant(variant)/(s['id']+'.mp4'))
    record.update(review_video=str(preview),review_sha256=p.sha(preview))
    p.write_json(folder/completion_name(),record)
    update_index()

def shot(s,variant):
    guard()
    folder=OUT/'shots'/s['id']/variant;folder.mkdir(parents=True,exist_ok=True)
    target=folder/output_name();completion=folder/completion_name()
    state(variant=variant,shot=s['id'],stage='preparing')
    spec_hash=signature(dict(plan=STATE['plan_sha256'],shot=s['id'],variant=variant))
    if NATIVE_ONLY:spec_hash=signature(dict(source_signature=spec_hash,output='native_1024x576_v1'))
    if completion.exists():
        record=read(completion)
        if record['signature']!=spec_hash or not target.is_file() or p.sha(target)!=record['sha256']:raise RuntimeError('Completed shot changed')
        publish_shot(s,variant,target,record)
        return
    if variant=='A' and s['a_reuse']:
        source=Path(s['a_reuse']);sr.validate(source,s['frames'])
        if NATIVE_ONLY:
            if not target.exists():native_extract(source,target,0,s['frames'])
            method='CPU full-frame reduction of previously accepted shot; no new super-resolution'
        else:
            link_or_copy(source,target)
            method='Reused accepted full-frame shot or accepted typography; Gemini uses new full-frame rerender'
    elif s['mode']=='copy':
        if variant=='B':link_or_copy(OUT/'shots'/s['id']/'A'/output_name(),target)
        elif not target.exists():
            if NATIVE_ONLY:native_extract(PLAN['source'],target,s['start'],s['end'])
            else:sr.extract(PLAN['source'],target,s['start'],s['end'])
        method='Original full-frame prop/graphic, unchanged by design; A and B share it'
    else:
        ref=prepare_reference(s)
        refs=[p.stage(PLAN['cast'][k]['reference']) for k in s['cast']]
        seed=s['seed_'+variant]
        prefix=PREFIX+variant+'/'+s['id']+'_h3'
        wf=graph.core.build_workflow(s['prompt'],PLAN['settings']['width'],PLAN['settings']['height'],s['model_frames'],
             seed,PLAN['settings']['steps'],'res_multistep','simple',prefix,video=p.stage(ref),ref_images=refs,ref_audio=False)
        wf['6']['inputs']['unet_name']=graph.HYBRID
        wf.pop('23');wf['91']['inputs'].pop('audio')
        p.write_json(folder/'h3_workflow.json',wf)
        (folder/'prompt.txt').write_text(s['prompt'],encoding='utf-8')
        raw=gpu_job(wf,folder,'h3',prefix)
        info=p.video_info(raw)
        if (info['frames'],info['width'],info['height'])!=(s['model_frames'],PLAN['settings']['width'],PLAN['settings']['height']):raise RuntimeError('Unexpected H3 output geometry')
        native=folder/'native_fullframe.mp4'
        selection=[math.floor(i*(s['model_frames']-1)/(s['frames']-1)+0.5) for i in range(s['frames'])]
        if not native.exists():
            choose='+'.join(f'eq(n\\,{i})' for i in selection)
            p.run([p.FFMPEG,'-v','error','-y','-i',raw,'-vf',f'select={choose},setpts=N*1001/(24000*TB),setsar=1',
                '-frames:v',s['frames'],'-r',sr.FPS,'-an','-c:v','libx264','-crf','12','-pix_fmt','yuv420p',native])
        if p.video_info(native)['frames']!=s['frames']:raise RuntimeError('Native timing mismatch')
        p.write_json(folder/'timing.json',dict(source_interval=[s['start'],s['end']],generated_frames=s['model_frames'],selection=selection,method='Uniform full-frame timing restoration; no local mask, crop or patch'))
        if NATIVE_ONLY:
            method='H3 full-frame direct generation -> uniform full-frame timing; SeedVR2 deferred by user'
        else:
            sr_prefix=PREFIX+variant+'/'+s['id']+'_seedvr2'
            swf=sr.workflow(native,sr_prefix,seed+500000)
            p.write_json(folder/'seedvr2_workflow.json',swf)
            enhanced=gpu_job(swf,folder,'seedvr2',sr_prefix)
            sr.extract(enhanced,target,0,s['frames'])
            method='H3 full-frame direct generation -> uniform full-frame timing -> full-frame SeedVR2 1080p'
    info=validate_output(target,s['frames'])
    record=dict(id=s['id'],variant=variant,signature=spec_hash,sha256=p.sha(target),video=str(target),validation=info,
                method=method,local_character_composite=False,visual_review='Pending user review',completed=stamp())
    publish_shot(s,variant,target,record)
    done=completed_counts()
    state(stage='shot_complete',completed=done)
    print(f'Completed {variant} {s["id"]}: {done}',flush=True)

def completed_counts():return {v:sum((OUT/'shots'/s['id']/v/completion_name()).exists() for s in PLAN['shots']) for v in ('A','B')}

def update_index():
    DELIVERY.mkdir(parents=True,exist_ok=True)
    # Preserve user edits even while new B candidates continue arriving.
    choice_file=DELIVERY/('镜头选择_原生.csv' if NATIVE_ONLY else '镜头选择.csv')
    rows=[];csvrows=[]
    for s in PLAN['shots']:
        cells=[]
        for v in ('A','B'):
            vd=delivery_variant(v)
            exists=(DELIVERY/vd/(s['id']+'.mp4')).exists()
            cells.append(f'<a href="{vd}/{s["id"]}.mp4">{v} ▶</a>' if exists else ('暂缓' if NATIVE_ONLY and v=='B' else '待生成'))
        note='共用原镜头' if s['mode']=='copy' else '独立种子候选'
        rows.append(f'<tr><td>{s["id"]}</td><td>{s["start_seconds"]:.2f}–{s["end_seconds"]:.2f}</td><td>{html.escape(", ".join(s["cast"]) or "道具/文字")}</td><td>{cells[0]}</td><td>{cells[1]}</td><td>{note}</td></tr>')
        csvrows.append([s['id'],s['start_seconds'],s['end_seconds'],','.join(s['cast']),str(DELIVERY/delivery_variant('A')/(s['id']+'.mp4')),str(DELIVERY/delivery_variant('B')/(s['id']+'.mp4')),'A',note])
    counts=completed_counts()
    movies=' '.join(f'<a href="AI校园OP_{v}_{movie_suffix()}.mp4">完整 {v} 版（{movie_suffix()}） ▶</a>' for v in ('A','B') if (DELIVERY/f'AI校园OP_{v}_{movie_suffix()}.mp4').exists())
    notice=''
    partial_note=''
    if STOP.exists():
        stop=read(STOP)
        notice=f'<p style="color:#ffd29a">GPU 保护已停止渲染，等待用户确认恢复方案。已完成素材保留。{html.escape(stop.get("time",""))}：{html.escape(stop.get("reason",""))}</p>'
    elif (RUN/'STOP_AFTER_SHOT').exists():
        notice='<p style="color:#ffd29a">已按用户要求暂停生成，供先验收当前阶段样片。已完成镜头保留，等待用户要求继续。</p>'
    partial_record=OUT/'partial_review.json'
    if partial_record.exists():
        partial=read(partial_record);part_path=Path(partial['delivery'])
        if part_path.is_file():
            movies+=f' <a href="{html.escape(part_path.name)}">A 阶段样片：前 {partial["duration"]:.2f} 秒（未完成全片） ▶</a>'
            partial_note=f'\n\n[阶段样片：前 {partial["duration"]:.2f} 秒]({part_path.name})，仅包含连续已完成的 {partial["completed_shots"]} 段；不是完整 A 版。'
    content=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>校园 AI OP · 镜头挑选</title><style>body{{font:16px system-ui;background:#151923;color:#e8ecf2;max-width:1150px;margin:40px auto;padding:0 20px}}a{{color:#97ceff;margin-right:24px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:12px;text-align:left;border-bottom:1px solid #343c49}}p{{line-height:1.7;color:#b9c5d6}}h1{{font-size:30px}}</style><h1>校园 AI OP · 镜头挑选</h1><p>90.966 秒 · 1080P · 原音乐。先完成 A，再补不同种子的 B。当前 A {counts['A']}/{len(PLAN['shots'])}，B {counts['B']}/{len(PLAN['shots'])}。点击片段即可播放；编号与时间相同，可混合挑选。未完成的 B 保持“待生成”，不会用 A 冒充。</p><p>{movies}</p><p>模型整镜头输出后整幅超分；纯道具/图形镜头共用原片，前段已接受镜头用于 A。美术、动作、文字及标志等待你的验收。AI 产业人名为虚构致敬，不代表真实参与；原歌曲保持不变。</p><table><thead><tr><th>镜头</th><th>时间</th><th>角色</th><th>A</th><th>B</th><th>说明</th></tr></thead><tbody>{''.join(rows)}</tbody></table></html>'''
    content=content.replace('<p>90.966 秒',notice+'<p>90.966 秒',1)
    if (DELIVERY/'逐镜验收.html').exists():
        content=content.replace('</h1>', '</h1><p><a href="逐镜验收.html">逐镜验收：原片对照、实际提示词、确认与修改意见 →</a></p>', 1)
    if NATIVE_ONLY:
        content=content.replace('1080P · 原音乐。先完成 A，再补不同种子的 B。','1024×576 · 原音乐。当前只完成 A，1080P 超分与 B 均暂缓。')
        content=content.replace('模型整镜头输出后整幅超分；','模型整镜头输出，暂不超分；')
    (DELIVERY/'看片入口.html').write_text(content,encoding='utf-8')
    if not choice_file.exists():
        with choice_file.open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f);writer.writerow(['镜头','开始秒','结束秒','角色','A文件','B文件','选择','备注']);writer.writerows(csvrows)
    scope='只完成 A 的 1024×576 版本，1080P 超分与 B 均暂缓。' if NATIVE_ONLY else '完整 A 优先；B 时间允许逐镜补齐。'
    (DELIVERY/'README.md').write_text(f'# 校园 AI OP 完整版\n\n先看 [看片入口](看片入口.html)。{scope}目前 A {counts["A"]}/{len(PLAN["shots"])}、B {counts["B"]}/{len(PLAN["shots"])}。\n\n{delivery_variant("A")}/ 与 {delivery_variant("B")}/ 下同编号对应同一镜头，片段带原音乐；{choice_file.name} 可填写选择。纯道具/图形镜头共用。未完成镜头不冒充完整。\n\nAI 主创名为虚构致敬，原歌曲未改。尺寸、帧率、解码与音轨做技术校验，画面由用户验收。\n',encoding='utf-8')
    if notice or partial_note:
        with (DELIVERY/'README.md').open('a',encoding='utf-8') as f:
            if STOP.exists():f.write('\n\nGPU 保护已停止渲染，未自动重启；已完成结果保留。原因：'+read(STOP).get('reason','未知')+'。等待用户确认恢复方案。')
            elif notice:f.write('\n\n已按用户要求暂停生成，等待用户验收阶段样片后要求继续。')
            f.write(partial_note+'\n')

def assemble(variant):
    paths=[OUT/'shots'/s['id']/variant/output_name() for s in PLAN['shots']]
    if any(not (x.parent/completion_name()).exists() for x in paths):raise RuntimeError('Cannot assemble incomplete variant')
    target=OUT/f'op_full_{variant}_{movie_suffix()}.mp4'
    validation=OUT/(f'validation_{variant}_native.json' if NATIVE_ONLY else f'validation_{variant}.json')
    if validation.exists():
        check=read(validation)
        if not target.is_file() or p.sha(target)!=check['sha256']:raise RuntimeError('Validated full movie changed')
    else:
        concat=OUT/f'concat_{variant}_{movie_suffix()}.txt'
        concat.write_text(''.join(f"file '{x.as_posix()}'\n" for x in paths),encoding='utf-8')
        state(stage='assembling',variant=variant)
        p.run([p.FFMPEG,'-v','error','-y','-f','concat','-safe','0','-i',concat,'-i',prepare_audio(),'-map','0:v:0','-map','1:a:0',
            '-vf','setpts=N*1001/(24000*TB),setsar=1','-frames:v',PLAN['frames'],'-r',sr.FPS,'-c:v','libx264','-crf','15',
            '-pix_fmt','yuv420p','-c:a','copy','-t',PLAN['duration'],'-movflags','+faststart',target])
        info=validate_output(target,PLAN['frames'],True)
        hashes=[audio_hash(x) for x in (prepare_audio(),target)]
        if hashes[0]!=hashes[1]:
            # Finishing a frame-limited encode can end audio demux one packet early.
            # Remux the completed video with the original AAC master independently.
            fixed=OUT/f'op_full_{variant}_audio_mux.mp4'
            p.run([p.FFMPEG,'-v','error','-y','-i',target,'-i',prepare_audio(),'-map','0:v:0','-map','1:a:0',
                '-c','copy','-t',PLAN['duration'],'-movflags','+faststart',fixed])
            info=validate_output(fixed,PLAN['frames'],True)
            if audio_hash(fixed)!=hashes[0]:raise RuntimeError('Two-pass full audio mux mismatch')
            fixed.replace(target)
            hashes[1]=audio_hash(target)
        if hashes[0]!=hashes[1]:raise RuntimeError('Full original-audio master packets changed during assembly')
        p.write_json(validation,dict(video=str(target),sha256=p.sha(target),validation=info,plan_sha256=STATE['plan_sha256'],
            source_audio='Original source soundtrack encoded once to 256kbps AAC; master packets copied unchanged to A and B',
            audio_master_hash=hashes[0],output_audio_hash=hashes[1],full_decode_passed=True,
            user_visual_review_pending=True,completed=stamp()))
    link_or_copy(target,DELIVERY/f'AI校园OP_{variant}_{movie_suffix()}.mp4')
    update_index()
    state(stage='variant_complete',variant=variant,completed=completed_counts(),latest_full_video=str(target))
    print('Full movie complete: '+str(target),flush=True)

def preflight():
    global PLAN
    PLAN=read(OUT/'full_plan.json')
    if p.sha(PLAN['source'])!=PLAN['source_sha256']:raise RuntimeError('Original source changed')
    for c in PLAN['cast'].values():
        if not Path(c['reference']).is_file():raise RuntimeError('Missing identity reference: '+c['reference'])
    if shutil.disk_usage(OUT).free<20*1024**3:raise RuntimeError('Less than 20 GiB free')
    guard()
    # Verify signatures of all source images in a single immutable record.
    provenance=dict(plan_sha256=p.sha(OUT/'full_plan.json'),source_sha256=PLAN['source_sha256'],references={k:p.sha(c['reference']) for k,c in PLAN['cast'].items()})
    existing=OUT/'input_provenance.json'
    if existing.exists() and read(existing)!=provenance:raise RuntimeError('Frozen production inputs changed')
    p.write_json(existing,provenance)
    return provenance

def main():
    global NATIVE_ONLY
    parser=argparse.ArgumentParser();parser.add_argument('--preflight-only',action='store_true');parser.add_argument('--a-only',action='store_true');parser.add_argument('--native-only',action='store_true');args=parser.parse_args()
    NATIVE_ONLY=args.native_only
    if NATIVE_ONLY:args.a_only=True
    RUN.mkdir(parents=True,exist_ok=True)
    lock=(RUN/'worker.lock').open('a+b');lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:raise RuntimeError('A full-OP worker already holds the production lock')
    provenance=preflight()
    if args.preflight_only:
        print(json.dumps(dict(status='preflight_passed',shots=len(PLAN['shots']),references=len(PLAN['cast']),power_limit_w=guard()['power_limit_w']),ensure_ascii=False));return
    p.write_json(RUN/'worker_pid.json',dict(pid=os.getpid(),started=stamp(),script=str(Path(__file__).resolve()),native_only=NATIVE_ONLY,a_only=args.a_only))
    state(status='running',started=stamp(),plan_sha256=provenance['plan_sha256'],completed=completed_counts(),safety={**PLAN['safety'],'power_limit_w':POWER_LIMIT_W,'cooldown_to_c':COOLDOWN_TO_C},native_only=NATIVE_ONLY,a_only=args.a_only,super_resolution_deferred=NATIVE_ONLY)
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    try:
        prepare_audio();update_index()
        for variant in (['A'] if args.a_only else ['A','B']):
            for s in PLAN['shots']:
                if (RUN/'STOP_AFTER_SHOT').exists():
                    state(status='stopped_by_request',stage='between_shots');return
                shot(s,variant)
            assemble(variant)
        state(status='complete_pending_user_review',stage='complete',completed=completed_counts(),finished=stamp())
    except BaseException as exc:
        state(status='safety_stopped' if isinstance(exc,SafetyStop) else 'failed',error=str(exc),finished=stamp())
        print(type(exc).__name__+': '+str(exc),flush=True)
        raise
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        try:release()
        except Exception as exc:print('Cleanup: '+str(exc),flush=True)
        lock.close()

if __name__=='__main__':main()
