"""Overnight H3 revision queue: runs prepared shot revisions one after another on the GPU.

Each job reuses the shared single-shot runner (safety guard, validation, review registration).
Before every job the GPU must cool to COOL_C; a safety STOP or a guard trip halts the queue,
any other failure is recorded and the queue moves on. `--check` validates every job without
writing job files or touching the GPU.

Job kinds (runtime/overnight_queue.json, run in order):
  single  existing A candidate: same source, seed and timing; references and prompt replaced
  new     no A candidate yet: workflow copied from TEMPLATE with this shot's source, length and seed
  merged  several neighbouring shots generated and reviewed as one unit (e.g. "021-022")
"""
import argparse
import copy
import difflib
import json
import math
import subprocess
import time
import traceback
from pathlib import Path

import revise_school_shot_prompt as runner

batch = runner.batch
review = runner.review
PROD, ROOT = runner.PROD, runner.ROOT
RUN = PROD / 'runtime'
QUEUE = RUN / 'overnight_queue.json'
STATUS = RUN / 'overnight_queue_status.json'
TEMPLATE = PROD / 'shots/076/A/h3_workflow.json'
SKILL = ROOT / '.claude/skills/h3-prompt-writing'
SKILL_COMMIT = 'a107547fa669c509b8e6363fe18378d46ab3066c'
COOL_C = 57
COOL_TIMEOUT = 45 * 60
REF_NODE = 200  # reference LoadImage nodes are 200, 201, ...


def snap(n, minimum=56):
    return max(minimum, 5 + math.ceil((n - 5) / 17) * 17)


def plan_shot(plan, sid):
    return copy.deepcopy(next(s for s in plan['shots'] if s['id'] == sid))


def set_refs(workflow, cast, plan):
    """Replace every reference image with the cast in Picture order (Picture 1 = cast[0])."""
    inp = workflow['104']['inputs']
    for key in [k for k in inp if k.startswith('ref_images.')]:
        workflow.pop(str(inp.pop(key)[0]), None)
    for i, name in enumerate(cast):
        workflow[str(REF_NODE + i)] = dict(class_type='LoadImage',
            inputs=dict(image=batch.p.stage(plan['cast'][name]['reference'] if name in plan['cast'] else name)))  # a cast name, or an image path (keyframe)
        inp[f'ref_images.ref_image_{i}'] = [str(REF_NODE + i), 0]
    staged = [workflow[str(REF_NODE + i)]['inputs']['image'] for i in range(len(cast))]
    assert len(set(staged)) == len(staged), f'identical reference images (same file content): {staged}'


def without(workflow, fields):
    """Copy of a workflow minus references and the given (node, input) fields, for change checks."""
    w = copy.deepcopy(workflow)
    for key in [k for k in w['104']['inputs'] if k.startswith('ref_images.')]:
        w.pop(str(w['104']['inputs'].pop(key)[0]), None)
    for node, name in fields:
        w[node]['inputs'].pop(name, None)
    return w


def stretched(plan, start, end, g, target):
    """The pipeline's whole-frame reference: source frames [start, end) uniformly stretched to g frames."""
    n = end - start
    vf = (f'trim=start_frame={start}:end_frame={end},setpts=N*{g - 1}/({n - 1}*24*TB),fps=24,'
          f'scale=1024:576:flags=lanczos,setsar=1,tpad=stop_mode=clone:stop_duration=0.2')
    batch.p.run([batch.p.FFMPEG, '-v', 'error', '-y', '-i', plan['source'], '-vf', vf, '-frames:v', g,
        '-r', '24', '-an', '-c:v', 'libx264', '-crf', '12', '-pix_fmt', 'yuv420p', target])
    assert batch.p.video_info(target)['frames'] == g


def master_audio(start, end, target):
    """Original audio for source frames [start, end), made like the per-shot review clips."""
    a, b = start * 1001 / 24000, end * 1001 / 24000
    batch.p.run([batch.p.FFMPEG, '-v', 'error', '-y', '-i', PROD / 'original_audio_master.m4a', '-vn',
        '-af', f'atrim=start={a:.12f}:end={b:.12f},asetpts=PTS-STARTPTS', '-c:a', 'aac', '-b:a', '192k',
        '-t', f'{(end - start) * 1001 / 24000:.9f}', target])


def scene_scores(raw):
    out = batch.p.run([batch.p.FFMPEG, '-hide_banner', '-i', raw, '-vf', "select='gte(scene,0)',metadata=print",
        '-an', '-f', 'null', '-']).stderr
    scores, frame = {}, None
    for line in out.splitlines():
        if 'pts_time:' in line:
            frame = round(float(line.split('pts_time:')[1].split()[0]) * 24)
        elif 'lavfi.scene_score=' in line:
            scores[frame] = float(line.split('=')[1])
    return scores


def cut_selection(counts, planned, total):
    """Selection for segments separated by hard cuts. `planned` holds the model frame where each later
    segment should start; H3 may move a cut by a few frames, so the actual cut is detected near each
    planned one and every segment is spread over its actual frames."""
    def choose(raw):
        scores = scene_scores(raw)
        bounds = [0] + [max(range(p - 6, p + 5), key=lambda k: scores.get(k, 0)) for p in planned] + [total]
        chosen = []
        for n, lo, hi in zip(counts, bounds, bounds[1:]):
            chosen += [lo + math.floor(i * (hi - 1 - lo) / (n - 1) + 0.5) for i in range(n)]
        batch.p.write_json(JOB_DIR[0] / 'detected_cuts.json', dict(planned=planned, detected=bounds[1:-1],
            scores={k: scores.get(k) for k in sorted(scores)}))
        return chosen
    return choose


def internal_cuts(shot, cuts):
    """A uniformly stretched shot with hard cuts at the given source indices."""
    n, g = shot['frames'], shot['model_frames']
    edges = [0] + sorted(cuts) + [n]
    counts = [b - a for a, b in zip(edges, edges[1:])]
    return cut_selection(counts, [math.floor(c * (g - 1) / (n - 1) + 0.5) for c in sorted(cuts)], g)


JOB_DIR = [None]


def record(job_dir, plan, workflow, base, base_label, prompt, old_prompt, extra, write):
    if not write:
        return
    if (job_dir / 'workflow.json').exists():
        assert batch.read(job_dir / 'workflow.json') == workflow, 'Prepared revision is immutable'
        return
    batch.p.write_json(job_dir / 'workflow.json', workflow)
    (job_dir / 'prompt.txt').write_text(prompt, encoding='utf-8')
    (job_dir / 'prompt_before.txt').write_text(old_prompt, encoding='utf-8')
    (job_dir / 'prompt.diff').write_text(''.join(difflib.unified_diff(old_prompt.splitlines(True), prompt.splitlines(True),
        fromfile=base_label, tofile=job_dir.name)), encoding='utf-8')
    inputs = []
    for n in workflow.values():
        if n['class_type'] in ('LoadImage', 'LoadVideo'):
            name = n['inputs'].get('image') or n['inputs'].get('file')
            inputs.append(dict(path=str(ROOT / 'ComfyUI/input' / name), sha256=batch.p.sha(ROOT / 'ComfyUI/input' / name)))
    skill = [SKILL / 'SKILL.md', SKILL / 'references/ref-en.txt', SKILL / 'references/base-en.txt']
    batch.p.write_json(job_dir / 'preparation.json', dict(
        production_plan_sha256=batch.p.sha(PROD / 'full_plan.json'),
        source_workflow=str(base), source_workflow_sha256=batch.p.sha(base), workflow_sha256=batch.signature(workflow),
        prompt_guide=dict(repository='https://github.com/MiniMax-AI/MiniMax-H3', path='skills/h3-prompt-writing',
            commit=SKILL_COMMIT, files=[dict(path=str(f), sha256=batch.p.sha(f)) for f in skill]),
        actual_inputs=inputs, seed=workflow['15']['inputs']['noise_seed'], steps=workflow['9']['inputs']['steps'],
        generated_frames=workflow['104']['inputs']['length'], super_resolution=False,
        background_batch_remains_paused=True, queued_overnight=True,
        **{k: ('chosen after generation at the detected cuts; see detected_cuts.json' if callable(v) else v)
           for k, v in extra.items()}))


def prepare(job, plan, write):
    """Returns (shot, job_dir, workflow, prefix, selection, audio_source)."""
    job_dir = PROD / 'revisions' / job['dir']
    JOB_DIR[0] = job_dir
    prefix = 'video/anime_op/school_full_op_v1/revisions/' + job['dir']
    prompt = (job_dir / 'prompt_requested.txt').read_text(encoding='utf-8-sig')
    cast = job['cast']
    if job['kind'] == 'single':
        shot = plan_shot(plan, job['id'])
        base = PROD / 'shots' / job['id'] / 'A/h3_workflow.json'
        old = batch.read(base)
        workflow = copy.deepcopy(old)
        set_refs(workflow, cast, plan)
        workflow['104']['inputs']['prompt'] = prompt
        workflow['92']['inputs']['filename_prefix'] = prefix
        changed = [('104', 'prompt'), ('92', 'filename_prefix')]
        assert without(workflow, changed) == without(old, changed), job['id'] + ': unexpected workflow change'
        selection = batch.read(PROD / 'shots' / job['id'] / 'A/timing.json')['selection']
        assert len(selection) == shot['frames'] and selection[-1] == shot['model_frames'] - 1
        if job.get('cuts'):
            selection = internal_cuts(shot, job['cuts'])
        record(job_dir, plan, workflow, base, f"{job['id']} A actual prompt", prompt, old['104']['inputs']['prompt'],
            dict(shot=job['id'], kind='single', actual_cast=cast, frames=shot['frames'], frame_selection=selection,
                 changed_fields=['104.inputs.prompt', '104.inputs.ref_images', '92.inputs.filename_prefix'],
                 generation_change_description='Official H3 Ref2VA prompt and the verified cast in Picture order; same '
                     'source video, seed, steps and timing as the A candidate.'), write)
        return shot, job_dir, workflow, prefix, selection, None
    if job['kind'] == 'new':
        shot = plan_shot(plan, job['id'])
        n, g = shot['frames'], shot['model_frames']
        ref = job_dir / 'reference_fullframe.mp4'
        if not ref.exists():
            stretched(plan, shot['start'], shot['end'], g, ref)
        old = batch.read(TEMPLATE)
        workflow = copy.deepcopy(old)
        workflow['210']['inputs']['file'] = batch.p.stage(ref)
        workflow['104']['inputs']['length'] = g
        workflow['15']['inputs']['noise_seed'] = shot['seed_A']
        set_refs(workflow, cast, plan)
        workflow['104']['inputs']['prompt'] = prompt
        workflow['92']['inputs']['filename_prefix'] = prefix
        changed = [('210', 'file'), ('104', 'length'), ('15', 'noise_seed'), ('104', 'prompt'), ('92', 'filename_prefix')]
        assert without(workflow, changed) == without(old, changed), job['id'] + ': unexpected template change'
        selection = [math.floor(i * (g - 1) / (n - 1) + 0.5) for i in range(n)]
        if job.get('cuts'):
            selection = internal_cuts(shot, job['cuts'])
        audio = job_dir / 'original_audio.m4a'
        if not audio.exists():
            master_audio(shot['start'], shot['end'], audio)
        record(job_dir, plan, workflow, TEMPLATE, 'template workflow (076 A) prompt', prompt, '',
            dict(shot=job['id'], kind='new', actual_cast=cast, frames=n, frame_selection=selection,
                 changed_fields=['210.inputs.file', '104.inputs.length', '15.inputs.noise_seed', '104.inputs.prompt',
                                 '104.inputs.ref_images', '92.inputs.filename_prefix'],
                 generation_change_description="First candidate for this shot (the batch stopped before it); 076's "
                     'workflow as template with this shot\'s source, length and planned seed.'), write)
        return shot, job_dir, workflow, prefix, selection, audio
    if job['kind'] == 'merged':
        members = [plan_shot(plan, sid) for sid in job['members']]
        counts = [s['frames'] for s in members]
        start, end = members[0]['start'], members[-1]['end']
        assert all(a['end'] == b['start'] for a, b in zip(members, members[1:]))
        ref = job_dir / 'reference_merged.mp4'
        if job['layout'] == 'continuous':
            blocks = [snap(sum(counts))]
        else:
            blocks = [snap(counts[0])] + [17 * math.ceil(n / 17) for n in counts[1:]]
        g = sum(blocks)
        assert (g - 5) % 17 == 0
        if not ref.exists():
            if len(blocks) == 1:
                stretched(plan, start, end, g, ref)
            else:
                pieces = []
                for k, (s, gk) in enumerate(zip(members, blocks)):
                    piece = job_dir / f'reference_part{k + 1}.mp4'
                    stretched(plan, s['start'], s['end'], gk, piece)
                    pieces.append(piece)
                inputs = sum((['-i', p] for p in pieces), [])
                batch.p.run([batch.p.FFMPEG, '-v', 'error', '-y', *inputs, '-filter_complex',
                    ''.join(f'[{i}:v]' for i in range(len(pieces))) + f'concat=n={len(pieces)}:v=1:a=0,setpts=N/(24*TB)',
                    '-r', '24', '-an', '-c:v', 'libx264', '-crf', '12', '-pix_fmt', 'yuv420p', ref])
            assert batch.p.video_info(ref)['frames'] == g
        base = PROD / 'shots' / job['members'][0] / 'A/h3_workflow.json'
        old = batch.read(base)
        workflow = copy.deepcopy(old)
        workflow['210']['inputs']['file'] = batch.p.stage(ref)
        workflow['104']['inputs']['length'] = g
        set_refs(workflow, cast, plan)
        workflow['104']['inputs']['prompt'] = prompt
        workflow['92']['inputs']['filename_prefix'] = prefix
        changed = [('210', 'file'), ('104', 'length'), ('104', 'prompt'), ('92', 'filename_prefix')]
        assert without(workflow, changed) == without(old, changed), job['id'] + ': unexpected workflow change'
        n = sum(counts)
        if len(blocks) > 1:
            selection = cut_selection(counts, [sum(blocks[:i + 1]) for i in range(len(blocks) - 1)], g)
        elif job.get('cuts'):
            selection = internal_cuts(dict(frames=n, model_frames=g), job['cuts'])
        else:
            selection = [math.floor(i * (g - 1) / (n - 1) + 0.5) for i in range(n)]
        audio = job_dir / 'original_audio.m4a'
        if not audio.exists():
            master_audio(start, end, audio)
        unit = job['id']
        if write:
            ensure_unit(plan, unit, job['members'], members, counts, job.get('description', ''))
        record(job_dir, plan, workflow, base, f"{job['members'][0]} A actual prompt", prompt, old['104']['inputs']['prompt'],
            dict(shot=unit, kind='merged', members=job['members'], layout=job['layout'], blocks=blocks,
                 source_frames=[start, end], actual_cast=cast, frames=n,
                 changed_fields=['210.inputs.file', '104.inputs.length', '104.inputs.prompt', '104.inputs.ref_images',
                                 '92.inputs.filename_prefix'],
                 generation_change_description='Neighbouring shots generated and reviewed as one unit; official H3 '
                     'Ref2VA prompt; seed from the first member\'s A candidate.'), write)
        shot = dict(id=unit, start=start, end=end, frames=n, model_frames=g)
        return shot, job_dir, workflow, prefix, selection, audio
    raise ValueError('Unknown job kind: ' + job['kind'])


def ensure_unit(plan, unit, member_ids, members, counts, description):
    source = review.REVIEW / 'source' / f'{unit}.mp4'
    if not source.exists():
        batch.p.run([batch.p.FFMPEG, '-v', 'error', '-y', '-i', plan['source'], '-vf',
            f"trim=start_frame={members[0]['start']}:end_frame={members[-1]['end']},setpts=N*1001/(24000*TB),"
            'scale=1024:576:flags=lanczos,setsar=1', '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '19',
            '-pix_fmt', 'yuv420p', '-r', '24000/1001', source])
    with review.LOCK:
        manifest = review.read(review.REVIEW / 'manifest.json')
        if any(s['id'] == unit for s in manifest['shots']):
            return
        old = [s for s in manifest['shots'] if s['id'] in member_ids]
        entry = dict(id=unit, start=members[0]['start_seconds'], end=members[-1]['end_seconds'], frames=sum(counts),
            cast=sorted({c for s in members for c in s['cast']}), credits=members[0]['credits'],
            description=description or f'{unit} 合并单元。', source_video=str(source),
            planned_prompt='（合并单元，没有原计划提示词；实际提示词见各版本。）', versions=[],
            merged_from=member_ids, members=old, created=review.stamp())
        at = manifest['shots'].index(old[0])
        manifest['shots'] = [s for s in manifest['shots'] if s['id'] not in member_ids]
        manifest['shots'].insert(at, entry)
        review.write(review.REVIEW / 'manifest.json', manifest)


def gpu_temperature():
    out = subprocess.run(['nvidia-smi', '--query-gpu=temperature.gpu', '--format=csv,noheader,nounits'],
        capture_output=True, text=True, check=True).stdout
    return int(out.strip().splitlines()[0])


def status(update):
    state = batch.read(STATUS) if STATUS.exists() else dict(jobs={})
    state.update(update)
    batch.p.write_json(STATUS, state)
    return state


def job_status(job_id, **values):
    state = batch.read(STATUS) if STATUS.exists() else dict(jobs={})
    state['jobs'].setdefault(job_id, {}).update(values, updated=batch.stamp())
    batch.p.write_json(STATUS, state)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='validate every job; no job files, no GPU')
    ap.add_argument('--only', nargs='*', help='run only these job ids')
    args = ap.parse_args()
    plan = batch.read(PROD / 'full_plan.json')
    jobs = batch.read(QUEUE)['jobs']
    if args.only:
        jobs = [j for j in jobs if j['id'] in args.only]
    if args.check:
        for job in jobs:
            shot, job_dir, workflow, prefix, selection, audio = prepare(job, plan, write=False)
            refs = [workflow[str(REF_NODE + i)]['inputs']['image'] for i in range(len(job['cast']))]
            print(f"OK {job['id']:>9} {job['kind']:<6} frames={shot['frames']:>3} model={workflow['104']['inputs']['length']:>3} "
                  f"seed={workflow['15']['inputs']['noise_seed']} refs={job['cast']} {refs}", flush=True)
        return
    status(dict(started=batch.stamp(), pid=__import__('os').getpid(), state='running', total=len(jobs)))
    for job in jobs:
        job_dir = PROD / 'revisions' / job['dir']
        if (job_dir / 'validation.json').exists():
            job_status(job['id'], state='done_before')
            continue
        if batch.STOP.exists():
            status(dict(state='stopped_safety', reason='STOP_GPU_GUARD.json present'))
            print('SAFETY STOP present; queue halted', flush=True)
            return
        waited = time.time()
        while gpu_temperature() > COOL_C:
            if time.time() - waited > COOL_TIMEOUT:
                status(dict(state='stopped_cooling_timeout'))
                print('GPU did not cool down; queue halted', flush=True)
                return
            time.sleep(20)
        job_status(job['id'], state='running', started=batch.stamp())
        print(f"=== {job['id']} ({job['kind']}) {batch.stamp()}", flush=True)
        try:
            shot, job_dir, workflow, prefix, selection, audio = prepare(job, plan, write=True)
            runner.run(shot, job_dir, workflow, prefix, selection, job['label'], audio_source=audio)
            job_status(job['id'], state='done', finished=batch.stamp())
            with review.LOCK:
                manifest = review.read(review.REVIEW / 'manifest.json')
                entry = next(s for s in manifest['shots'] if s['id'] == job['id'])
                if entry.get('queued'):
                    entry['queued']['state'] = 'done'
                    review.write(review.REVIEW / 'manifest.json', manifest)
        except Exception as exc:
            job_status(job['id'], state='failed', error=repr(exc), trace=traceback.format_exc()[-2000:])
            print(f"FAILED {job['id']}: {exc!r}", flush=True)
            if batch.STOP.exists() or 'Safety monitor' in str(exc):
                status(dict(state='stopped_safety', reason=repr(exc)))
                return
    status(dict(state='finished', finished=batch.stamp()))


if __name__ == '__main__':
    main()
