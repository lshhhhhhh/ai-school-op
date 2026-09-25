"""1080p teaser of shots 001-020 from the user-approved versions.

--sr        SeedVR2 1080p for approved revisions that have no 1080p yet (GPU, one guarded session)
--assemble  concatenate the 1080p clips in shot order with the original audio (CPU)

Per shot: originals (copy mode) are cut from the 1080p source; approved A candidates reuse their
existing SeedVR2 1080p; approved H3 revisions get SeedVR2 from their native output; the 011
imagegen card is resized from its generated image. 019 has no approved version yet: the newest
019 revision is used unless --original-019 is given.
"""
import argparse
import contextlib
import json
import msvcrt
import shutil
import subprocess
import sys
import time
import datetime as dt
from pathlib import Path

import revise_school_shot_prompt as runner
import try_seedvr2_1080p as sr

batch = runner.batch
PROD, ROOT = runner.PROD, runner.ROOT
OUT = PROD / 'teaser_001_020'
DELIVERY = ROOT / 'deliverables/ai_school_op/完整OP/AI校园OP_001-020_1080p_预热样片.mp4'
LAST = '020'
PINNED = {'009': 'revision-f77549169798-764ac0ff'}  # user: 009 uses revision 6 (revision 7 failed)
CLOUD_HEAD = PROD / 'cloud_h3_001-004/cloud_h3_001-002-003-004_1080p.mp4'  # hosted H3 2K -> 1080p, source frames 0-57


def shots():
    """(plan shot, chosen review version or None) for 001..020 in order."""
    plan = batch.read(PROD / 'full_plan.json')
    manifest = batch.read(PROD / 'review/manifest.json')
    state = batch.read(PROD / 'review/review_state.json')
    entries = {s['id']: s for s in manifest['shots']}
    chosen = []
    for s in plan['shots']:
        if s['id'] > LAST:
            break
        versions = entries[s['id']]['versions']
        approved = [v for v in versions if state['decisions'].get(f"{s['id']}/{v['id']}", {}).get('status') == 'approved']
        if s['id'] in PINNED:
            v = next(x for x in approved if x['id'] == PINNED[s['id']])
        elif approved:
            v = approved[-1]
        elif s['id'] in ('004', '014_015'):
            v = versions[0]  # user accepted these A candidates for now
        elif s['id'] == '019':
            v = next((x for x in reversed(versions) if x['id'].startswith('revision-')), None)
        else:
            raise RuntimeError(f"{s['id']}: no usable version")
        chosen.append((s, v))
    return plan, chosen


def revision_dir(v):
    """Folder of an H3 revision from its registered workflow path."""
    return Path(v['prompt']['source']).parent


def needs_sr(s, v):
    return v and v['id'].startswith('revision-') and s['id'] != '011'


@contextlib.contextmanager
def guarded(job):
    """The shared runner's GPU safety session: fresh guard monitoring before any submission."""
    lock = (batch.RUN / 'worker.lock').open('a+b')
    lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    monitor = None
    try:
        if batch.STOP.exists():
            raise RuntimeError('GPU safety STOP exists; retained, no automatic restart')
        if not batch.p.queue_empty():
            raise RuntimeError('Queue is occupied; no overlapping submission')
        batch.OUT = job
        batch.NATIVE_ONLY = False
        batch.STATE = dict(status='teaser_super_resolution', shot='001-020', variant=job.name, started=batch.stamp())
        monitor_stop = batch.RUN / 'STOP_GUARD_MONITOR'
        if not monitor_stop.exists():
            raise RuntimeError('Expected stopped monitor marker; inspect existing guard before starting another')
        shutil.move(str(monitor_stop), str(job / f"previous_monitor_stop_{dt.datetime.now():%Y%m%d_%H%M%S}.json"))
        started = time.time()
        monitor = subprocess.Popen([sys.executable, str(ROOT / 'pipelines/anime_op/gpu_guard.py')], cwd=str(ROOT),
            stdout=(job / 'guard.log').open('w'), stderr=(job / 'guard.err').open('w'), creationflags=subprocess.CREATE_NO_WINDOW)
        seen = set()
        while time.time() - started < 30 or len(seen) < 12:
            if monitor.poll() is not None or batch.STOP.exists():
                raise RuntimeError('Safety monitor exited or tripped')
            g = batch.read(batch.RUN / 'gpu_guard_status.json')
            if 'time' in g and dt.datetime.fromisoformat(g['time']).timestamp() >= started:
                g = batch.guard()
                if g['temperature_c'] > 60:
                    raise RuntimeError('GPU must be <=60C before submitting')
                seen.add(g['time'])
            if time.time() - started > 60:
                raise RuntimeError('Insufficient continuous fresh monitoring')
            time.sleep(2)
        yield
    finally:
        if monitor is not None and batch.p.queue_empty():
            batch.release()
            batch.p.write_json(batch.RUN / 'STOP_GUARD_MONITOR', dict(time=batch.stamp(),
                reason='Teaser 001-020 super-resolution ended; full batch remains paused'))
            try:
                monitor.wait(timeout=12)
            except subprocess.TimeoutExpired:
                pass
        lock.close()


def super_resolve():
    OUT.mkdir(exist_ok=True)
    _, chosen = shots()
    todo = [(s, v) for s, v in chosen if needs_sr(s, v) and not (revision_dir(v) / '1080p.mp4').exists()]
    if not todo:
        print('nothing to super-resolve')
        return
    from overnight_queue import COOL_C, gpu_temperature
    waited = time.time()
    while gpu_temperature() > COOL_C:  # the guard refuses to submit above 60 C
        if time.time() - waited > 30 * 60:
            raise RuntimeError('GPU did not cool down')
        time.sleep(20)
    with guarded(OUT):
        for s, v in todo:
            folder = revision_dir(v)
            native = folder / 'native_fullframe.mp4'
            seed = batch.read(folder / 'workflow.json')['15']['inputs']['noise_seed'] + 500000
            prefix = 'video/anime_op/school_full_op_v1/teaser_001_020/' + folder.name + '_seedvr2'
            workflow = sr.workflow(native, prefix, seed)
            batch.p.write_json(folder / 'seedvr2_workflow.json', workflow)
            enhanced = batch.gpu_job(workflow, folder, 'seedvr2', prefix)
            target = folder / '1080p.mp4'
            sr.extract(enhanced, target, 0, s['frames'])
            sr.validate(target, s['frames'])
            print('SR ready', s['id'], target, flush=True)


def clip_for(plan, s, v, original_019, cloud_head=False):
    """Path of a 1920x1080, 24000/1001, silent clip with exactly this shot's frames."""
    OUT.mkdir(exist_ok=True)
    if cloud_head and s['id'] in ('002', '003', '004'):
        target = OUT / f"{s['id']}_cloud_h3_1080p.mp4"
        if not target.exists():
            sr.extract(CLOUD_HEAD, target, s['start'], s['end'])
        return target, 'hosted MiniMax H3 2K edit of 001-004, downscaled to 1080p'
    if s['mode'] == 'copy' or (s['id'] == '019' and (original_019 or v is None)):
        target = OUT / f"{s['id']}_original_1080p.mp4"
        if not target.exists():
            sr.extract(plan['source'], target, s['start'], s['end'])
        return target, 'original 1080p source'
    if s['id'] == '011':
        image = revision_dir(v) / 'generated_frame.png'
        target = OUT / '011_imagegen_1080p.mp4'
        if not target.exists():
            batch.p.run([batch.p.FFMPEG, '-v', 'error', '-y', '-loop', '1', '-i', image, '-frames:v', s['frames'],
                '-vf', 'scale=1920:1080:flags=lanczos,setsar=1', '-r', sr.FPS, '-an', '-c:v', 'libx264', '-crf', '12',
                '-pix_fmt', 'yuv420p', target])
        return target, 'approved imagegen card resized to 1080p'
    if v['id'].startswith('revision-'):
        return revision_dir(v) / '1080p.mp4', 'SeedVR2 1080p of the approved revision'
    return PROD / 'shots' / s['id'] / 'A/1080p.mp4', 'existing SeedVR2 1080p of the approved A candidate'


def assemble(original_019, cloud_head=False):
    plan, chosen = shots()
    parts, records = [], []
    for s, v in chosen:
        clip, method = clip_for(plan, s, v, original_019, cloud_head)
        sr.validate(clip, s['frames'])
        parts.append(clip)
        records.append(dict(shot=s['id'], frames=s['frames'], version=v['id'] if v else None, clip=str(clip),
                            sha256=batch.p.sha(clip), method=method))
    frames = sum(s['frames'] for s, _ in chosen)
    assert chosen[-1][0]['end'] == frames, 'shots 001-020 must be contiguous from frame 0'
    video = OUT / f"video_1080p{'_cloud_head' if cloud_head else ''}.mp4"
    inputs = sum((['-i', p] for p in parts), [])
    graph = ''.join(f'[{i}:v]' for i in range(len(parts))) + f'concat=n={len(parts)}:v=1:a=0,setpts=N*1001/(24000*TB)[v]'
    batch.p.run([batch.p.FFMPEG, '-v', 'error', '-y', *inputs, '-filter_complex', graph, '-map', '[v]', '-r', sr.FPS,
        '-c:v', 'libx264', '-preset', 'slow', '-crf', '14', '-pix_fmt', 'yuv420p', video])
    suffix = '_云端片头' if cloud_head else ''
    final = OUT / f'AI校园OP_001-020_1080p_预热样片{suffix}.mp4'
    batch.p.run([batch.p.FFMPEG, '-v', 'error', '-y', '-i', video, '-i', PROD / 'original_audio_master.m4a',
        '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '256k',
        '-t', f'{frames * 1001 / 24000:.9f}', '-movflags', '+faststart', final])
    info = sr.validate(final, frames, True)
    DELIVERY.parent.mkdir(parents=True, exist_ok=True)
    delivery = DELIVERY.with_name(final.name)
    shutil.copy2(final, delivery)
    batch.p.write_json(OUT / f"teaser_manifest{'_cloud_head' if cloud_head else ''}.json", dict(created=batch.stamp(), frames=frames, validation=info,
        video=str(delivery), sha256=batch.p.sha(delivery), audio='original master 0-%.6fs, AAC 256k' % (frames * 1001 / 24000),
        original_019=original_019, shots=records))
    print('TEASER', delivery, info, flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--sr', action='store_true')
    ap.add_argument('--assemble', action='store_true')
    ap.add_argument('--original-019', action='store_true')
    ap.add_argument('--cloud-head', action='store_true', help='002-004 from the hosted H3 edit')
    args = ap.parse_args()
    if args.sr:
        super_resolve()
    if args.assemble:
        assemble(args.original_019, args.cloud_head)
