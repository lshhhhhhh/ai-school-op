"""One authorized, prompt-only shot revision with a protected generation workflow."""
import argparse
import copy
import datetime as dt
import difflib
import json
import msvcrt
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import render_school_full_op as batch
import review_school_op as review

ROOT=batch.p.ROOT
PROD=ROOT/'assets/anime_op/school_full_op_v1'

def prepare(shot_id, revision):
    if not revision.replace('_','').isalnum():
        raise ValueError('Invalid revision name')
    plan=batch.read(PROD/'full_plan.json')
    shot=next(s for s in plan['shots'] if s['id']==shot_id)
    job=PROD/'revisions'/f'{shot_id}_{revision}'
    original=PROD/'shots'/shot_id/'A/h3_workflow.json'
    old=batch.read(original)
    prompt=(job/'prompt_requested.txt').read_text(encoding='utf-8-sig')
    prefix=f'video/anime_op/school_full_op_v1/revisions/{shot_id}_{revision}'
    workflow=copy.deepcopy(old)
    workflow['104']['inputs']['prompt']=prompt
    workflow['92']['inputs']['filename_prefix']=prefix
    check=copy.deepcopy(workflow)
    check['104']['inputs']['prompt']=old['104']['inputs']['prompt']
    check['92']['inputs']['filename_prefix']=old['92']['inputs']['filename_prefix']
    assert check==old and prompt!=old['104']['inputs']['prompt']
    timing=batch.read(PROD/'shots'/shot_id/'A/timing.json')
    selection=timing['selection']
    assert len(selection)==shot['frames'] and sorted(set(selection))==selection
    assert selection[0]==0 and selection[-1]==shot['model_frames']-1
    inputs=[]
    for n in old.values():
        if n['class_type'] in ('LoadImage','LoadVideo'):
            name=n['inputs'].get('image') or n['inputs'].get('file')
            path=ROOT/'ComfyUI/input'/name
            digest=batch.p.sha(path)
            assert digest[:20]==name.split('_')[1].split('.')[0],f'Staged source changed: {name}'
            inputs.append(dict(path=str(path),sha256=digest))
    if (job/'workflow.json').exists():
        assert batch.read(job/'workflow.json')==workflow,'Prepared revision cannot change after submission'
    else:
        batch.p.write_json(job/'workflow.json',workflow)
        (job/'prompt.txt').write_text(prompt,encoding='utf-8')
        (job/'prompt_before.txt').write_text(old['104']['inputs']['prompt'],encoding='utf-8')
        (job/'prompt.diff').write_text(''.join(difflib.unified_diff(
            old['104']['inputs']['prompt'].splitlines(True),prompt.splitlines(True),
            fromfile=f'{shot_id} original actual prompt',tofile=f'{shot_id} {revision}')),encoding='utf-8')
        batch.p.write_json(job/'preparation.json',dict(shot=shot_id,revision=revision,
            production_plan_sha256=batch.p.sha(PROD/'full_plan.json'),
            source_workflow=str(original),source_workflow_sha256=batch.p.sha(original),
            workflow_sha256=batch.signature(workflow),changed_fields=['104.inputs.prompt','92.inputs.filename_prefix'],
            unchanged_inputs=inputs,seed=old['15']['inputs']['noise_seed'],steps=old['9']['inputs']['steps'],
            frames=shot['frames'],generated_frames=shot['model_frames'],frame_selection=selection,
            super_resolution=False,background_batch_remains_paused=True))
    return shot,job,workflow,prefix,selection

def run(shot,job,workflow,prefix,selection,label,preview_only=False,audio_source=None):
    """audio_source: clip whose first audio track is the shot's original audio (default: the A candidate).
    selection may be a callable taking the raw H3 output path, for layouts decided after generation."""
    sid=shot['id']
    lock=(batch.RUN/'worker.lock').open('a+b');lock.seek(0)
    msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    monitor=None
    try:
        if batch.STOP.exists():
            raise RuntimeError('GPU safety STOP exists; retained, no automatic restart')
        if not batch.p.queue_empty():
            raise RuntimeError('Queue is occupied; no overlapping submission')
        if (job/'validation.json').exists():
            raise RuntimeError('Revision already validated; inspect registration instead of generating again')
        batch.OUT=job
        batch.NATIVE_ONLY=True
        batch.STATE=dict(status='single_shot_revision',shot=sid,variant=job.name,started=batch.stamp())
        batch.p.write_json(job/'worker_pid.json',dict(pid=os.getpid(),script=__file__,shot=sid,started=batch.stamp()))
        monitor_stop=batch.RUN/'STOP_GUARD_MONITOR'
        if not monitor_stop.exists():
            raise RuntimeError('Expected stopped monitor marker; inspect existing guard before starting another')
        archive=job/'previous_user_monitor_stop.json'
        if archive.exists():
            raise RuntimeError('A prior monitor attempt exists; inspect before retrying')
        shutil.move(str(monitor_stop),str(archive))
        monitor_start=time.time()
        monitor=subprocess.Popen([sys.executable,str(ROOT/'pipelines/anime_op/gpu_guard.py')],
            cwd=str(ROOT),stdout=(job/'guard.log').open('w'),stderr=(job/'guard.err').open('w'),
            creationflags=subprocess.CREATE_NO_WINDOW)
        batch.p.write_json(job/'guard_pid.json',dict(pid=monitor.pid,started=batch.stamp()))
        seen=set()
        while time.time()-monitor_start<30 or len(seen)<12:
            if monitor.poll() is not None or batch.STOP.exists():
                raise RuntimeError('Safety monitor exited or tripped')
            g=batch.read(batch.RUN/'gpu_guard_status.json')
            if 'time' in g and dt.datetime.fromisoformat(g['time']).timestamp()>=monitor_start:
                g=batch.guard()
                if g['temperature_c']>60:
                    raise RuntimeError('GPU must be <=60C before submitting')
                seen.add(g['time'])
            if time.time()-monitor_start>60:
                raise RuntimeError('Insufficient continuous fresh monitoring')
            time.sleep(2)
        batch.p.write_json(job/'monitor_observation.json',dict(seconds=time.time()-monitor_start,distinct_samples=len(seen),last=batch.guard()))
        raw=batch.gpu_job(workflow,job,'h3',prefix)
        info=batch.p.video_info(raw)
        assert (info['frames'],info['width'],info['height'])==(shot['model_frames'],1024,576),info
        if preview_only:
            # A short preflight is not a finished shot and must not enter approval records.
            preview=job/'preflight_5frames.mp4'
            batch.p.run([batch.p.FFMPEG,'-v','error','-y','-i',raw,'-vf',
                'setpts=N*1001/(24000*TB),setsar=1','-frames:v',str(shot['model_frames']),
                '-r','24000/1001','-an','-c:v','libx264','-crf','12','-pix_fmt','yuv420p',preview])
            first=job/'first_frame.png'
            batch.p.run([batch.p.FFMPEG,'-v','error','-y','-i',preview,'-frames:v','1',first])
            validation=batch.validate_output(preview,shot['model_frames'],False)
            assert batch.p.sha(PROD/'full_plan.json')==batch.read(job/'preparation.json')['production_plan_sha256']
            batch.p.write_json(job/'validation.json',dict(video=str(preview),first_frame=str(first),
                sha256=batch.p.sha(preview),validation=validation,full_decode_passed=True,
                kind='composition_preflight_only',audio=False,registered_for_shot_approval=False,
                limitation='Short temporal condition is not equivalent to full-shot first frame; no motion acceptance.',
                user_visual_review='pending',completed=batch.stamp()))
            batch.state(status='preflight_ready_for_user',stage='complete',video=str(preview),first_frame=str(first))
            print('PREFLIGHT READY '+str(preview),flush=True)
            return
        if callable(selection):
            selection=selection(raw)
        native=job/'native_fullframe.mp4'
        if not native.exists():
            choose='+'.join(f'eq(n\\,{i})' for i in selection)
            batch.p.run([batch.p.FFMPEG,'-v','error','-y','-i',raw,'-vf',
                f'select={choose},setpts=N*1001/(24000*TB),setsar=1',
                '-frames:v',str(shot['frames']),'-r','24000/1001','-an','-c:v','libx264',
                '-crf','12','-pix_fmt','yuv420p',native])
        batch.p.write_json(job/'timing.json',dict(source_interval=[shot['start'],shot['end']],selection=selection,
            generated_frames=shot['model_frames'],method='Unchanged uniform full-frame timing from original candidate'))
        old_audio=Path(audio_source) if audio_source else PROD/'shots'/sid/'A/native_review_with_audio.mp4'
        preview=job/'review_with_audio.mp4'
        if not preview.exists():
            batch.p.run([batch.p.FFMPEG,'-v','error','-y','-i',native,'-i',old_audio,
                '-map','0:v:0','-map','1:a:0','-c','copy','-movflags','+faststart',preview])
        validation=batch.validate_output(preview,shot['frames'],True)
        assert batch.audio_hash(preview)==batch.audio_hash(old_audio),'Original shot audio packets changed'
        assert batch.p.sha(PROD/'full_plan.json')==batch.read(job/'preparation.json')['production_plan_sha256']
        batch.p.write_json(job/'validation.json',dict(video=str(preview),sha256=batch.p.sha(preview),
            validation=validation,full_decode_passed=True,audio_packets_match_previous_shot=True,
            changed_generation_parameter=batch.read(job/'preparation.json').get('generation_change_description',
                'prompt only; unchanged seed, actual inputs and all sampling parameters'),
            super_resolution=False,user_visual_review='pending',completed=batch.stamp()))
        review.register(sid,preview,job/'workflow.json',label)
        batch.state(status='revision_ready_for_user',stage='complete',video=str(preview))
        print('READY '+str(preview),flush=True)
    except Exception as exc:
        batch.p.write_json(job/'failure.json',dict(time=batch.stamp(),error=repr(exc)))
        raise
    finally:
        if monitor is not None and batch.p.queue_empty():
            batch.release()
            batch.p.write_json(batch.RUN/'STOP_GUARD_MONITOR',dict(time=batch.stamp(),
                reason=f'Authorized single-shot {sid} revision ended; full batch remains paused'))
            try:monitor.wait(timeout=12)
            except subprocess.TimeoutExpired:pass
        lock.close()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--shot',required=True)
    ap.add_argument('--revision',required=True)
    ap.add_argument('--label',default='提示词修订')
    ap.add_argument('--prepare',action='store_true')
    args=ap.parse_args()
    prepared=prepare(args.shot,args.revision)
    if args.prepare:
        print(json.dumps(batch.read(prepared[1]/'preparation.json'),ensure_ascii=False),flush=True)
    else:run(*prepared,args.label)

if __name__=='__main__':main()
