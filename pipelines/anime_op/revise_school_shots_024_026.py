"""024-026 revision 2: one H3 generation for the shared planning-credit overlay, split back into three shots.

024 and 025 are one continuous street shot; 026 cuts closer at source frame 543 while the
credit overlay stays fixed. Each part is stretched onto its own block of the model grid
(45 source frames -> model frames 0-55, 15 source frames -> 56-72), so the cut sits exactly
on H3's 17-frame latent block boundary instead of snapping to a nearby latent (see 009 rev7).
"""
import argparse
import copy
import difflib
import json
import math

import revise_school_shot_prompt as runner

batch = runner.batch
review = runner.review
PROD, ROOT = runner.PROD, runner.ROOT
JOB = PROD / 'revisions/024_026_merged_v2'
BASE = PROD / 'shots/025/A/h3_workflow.json'  # DeepSeek = Picture 1, Gemini = Picture 2
PREFIX = 'video/anime_op/school_full_op_v1/revisions/024_026_merged_v2'
# (source start, source end, model frames); the second part begins on block boundary 56.
PARTS = [(498, 543, 56), (543, 558, 17)]
MODEL_FRAMES = sum(g for _, _, g in PARTS)
LABEL = '修订2·024-026合并·英文竖排企画'
SKILL = ROOT / '.claude/skills/h3-prompt-writing'
SKILL_COMMIT = 'a107547fa669c509b8e6363fe18378d46ab3066c'


def selections(plan):
    """Model frame chosen for each source frame, per shot (the pipeline's uniform rounding)."""
    chosen, offset = [], 0
    for start, end, g in PARTS:
        n = end - start
        chosen += [offset + math.floor(i * (g - 1) / (n - 1) + 0.5) for i in range(n)]
        offset += g
    first = PARTS[0][0]
    result = {}
    for sid in ('024', '025', '026'):
        s = next(x for x in plan['shots'] if x['id'] == sid)
        result[sid] = chosen[s['start'] - first:s['end'] - first]
        assert len(result[sid]) == s['frames']
    return result


def reference(plan):
    target = JOB / 'reference_merged_73.mp4'
    width, height = plan['settings']['width'], plan['settings']['height']
    if not target.exists():
        pieces = []
        for k, (start, end, g) in enumerate(PARTS):
            n = end - start
            piece = JOB / f'reference_part{k + 1}.mp4'
            vf = (f'trim=start_frame={start}:end_frame={end},setpts=N*{g - 1}/({n - 1}*24*TB),fps=24,'
                  f'scale={width}:{height}:flags=lanczos,setsar=1,tpad=stop_mode=clone:stop_duration=0.2')
            batch.p.run([batch.p.FFMPEG, '-v', 'error', '-y', '-i', plan['source'], '-vf', vf, '-frames:v', g,
                '-r', '24', '-an', '-c:v', 'libx264', '-crf', '12', '-pix_fmt', 'yuv420p', piece])
            assert batch.p.video_info(piece)['frames'] == g
            pieces.append(piece)
        batch.p.run([batch.p.FFMPEG, '-v', 'error', '-y', '-i', pieces[0], '-i', pieces[1], '-filter_complex',
            '[0:v][1:v]concat=n=2:v=1:a=0,setpts=N/(24*TB)', '-r', '24', '-an', '-c:v', 'libx264', '-crf', '12',
            '-pix_fmt', 'yuv420p', target])
    info = batch.p.video_info(target)
    assert (info['frames'], info['width'], info['height'], info['fps']) == (MODEL_FRAMES, width, height, '24/1'), info
    return target


def prepare():
    plan = batch.read(PROD / 'full_plan.json')
    assert (MODEL_FRAMES - 5) % 17 == 0 and (PARTS[0][2] - 5) % 17 == 0
    sel = selections(plan)
    old = batch.read(BASE)
    old_prompt = old['104']['inputs']['prompt']
    for node, design in (('200', 'deepseek_school_front_v1.png'), ('201', 'gemini_school_front_v1.png')):
        staged = ROOT / 'ComfyUI/input' / old[node]['inputs']['image']
        assert batch.p.sha(staged) == batch.p.sha(ROOT / 'assets/character_designs/school_v1/outputs' / design)
    staged = batch.p.stage(reference(plan))
    prompt = (JOB / 'prompt_requested.txt').read_text(encoding='utf-8-sig')
    workflow = copy.deepcopy(old)
    workflow['210']['inputs']['file'] = staged
    workflow['104']['inputs']['length'] = MODEL_FRAMES
    workflow['104']['inputs']['prompt'] = prompt
    workflow['92']['inputs']['filename_prefix'] = PREFIX
    check = copy.deepcopy(workflow)
    check['210']['inputs']['file'] = old['210']['inputs']['file']
    check['104']['inputs']['length'] = old['104']['inputs']['length']
    check['104']['inputs']['prompt'] = old_prompt
    check['92']['inputs']['filename_prefix'] = old['92']['inputs']['filename_prefix']
    assert check == old, 'Unexpected changes beyond source, length, prompt and output prefix'
    inputs = []
    for n in workflow.values():
        if n['class_type'] in ('LoadImage', 'LoadVideo'):
            name = n['inputs'].get('image') or n['inputs'].get('file')
            path = ROOT / 'ComfyUI/input' / name
            digest = batch.p.sha(path)
            assert digest[:20] == name.split('_')[1].split('.')[0]
            inputs.append(dict(path=str(path), sha256=digest))
    skill_files = [SKILL / 'SKILL.md', SKILL / 'references/ref-en.txt', SKILL / 'references/base-en.txt']
    if (JOB / 'workflow.json').exists():
        assert batch.read(JOB / 'workflow.json') == workflow, 'Prepared revision is immutable'
    else:
        batch.p.write_json(JOB / 'workflow.json', workflow)
        (JOB / 'prompt.txt').write_text(prompt, encoding='utf-8')
        (JOB / 'prompt_before.txt').write_text(old_prompt, encoding='utf-8')
        (JOB / 'prompt.diff').write_text(''.join(difflib.unified_diff(
            old_prompt.splitlines(True), prompt.splitlines(True),
            fromfile='025 original actual prompt', tofile='024-026 merged revision 2')), encoding='utf-8')
        batch.p.write_json(JOB / 'preparation.json', dict(shots=['024', '025', '026'], revision='merged_v2',
            production_plan_sha256=batch.p.sha(PROD / 'full_plan.json'),
            source_workflow=str(BASE), source_workflow_sha256=batch.p.sha(BASE),
            workflow_sha256=batch.signature(workflow),
            changed_fields=['210.inputs.file', '104.inputs.length', '104.inputs.prompt', '92.inputs.filename_prefix'],
            generation_change_description='024, 025 and 026 generated together from one 73-frame reference so the '
                'planning-credit overlay is consistent; official H3 Ref2VA prompt with the seven names as vertical '
                'columns (English names rotated sideways). Base workflow, references and seed from 025.',
            source_parts=[dict(source_frames=[a, b], model_frames=[sum(g for *_, g in PARTS[:k]),
                sum(g for *_, g in PARTS[:k + 1]) - 1]) for k, (a, b, _) in enumerate(PARTS)],
            frame_selection=sel, generated_frames=MODEL_FRAMES,
            prompt_guide=dict(repository='https://github.com/MiniMax-AI/MiniMax-H3', path='skills/h3-prompt-writing',
                commit=SKILL_COMMIT, files=[dict(path=str(f), sha256=batch.p.sha(f)) for f in skill_files]),
            actual_inputs=inputs, seed=old['15']['inputs']['noise_seed'], steps=old['9']['inputs']['steps'],
            actual_cast=['deepseek', 'gemini'], super_resolution=False, background_batch_remains_paused=True))
    shot = copy.deepcopy(next(s for s in plan['shots'] if s['id'] == '024'))
    shot['model_frames'] = MODEL_FRAMES
    return plan, sel, (shot, JOB, workflow, PREFIX, sel['024'])


def split(plan, sel):
    """024 is written by the shared runner at the job root; 025 and 026 go to their own folders."""
    raw = batch.read(JOB / 'h3_result.json')['raw']
    for sid in ('025', '026'):
        s = next(x for x in plan['shots'] if x['id'] == sid)
        folder = JOB / f'shot_{sid}'
        folder.mkdir(exist_ok=True)
        if (folder / 'validation.json').exists():
            continue
        native = folder / 'native_fullframe.mp4'
        choose = '+'.join(f'eq(n\\,{i})' for i in sel[sid])
        batch.p.run([batch.p.FFMPEG, '-v', 'error', '-y', '-i', raw, '-vf',
            f'select={choose},setpts=N*1001/(24000*TB),setsar=1', '-frames:v', s['frames'], '-r', '24000/1001',
            '-an', '-c:v', 'libx264', '-crf', '12', '-pix_fmt', 'yuv420p', native])
        old_audio = PROD / 'shots' / sid / 'A/native_review_with_audio.mp4'
        preview = folder / 'review_with_audio.mp4'
        batch.p.run([batch.p.FFMPEG, '-v', 'error', '-y', '-i', native, '-i', old_audio,
            '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy', '-movflags', '+faststart', preview])
        validation = batch.validate_output(preview, s['frames'], True)
        assert batch.audio_hash(preview) == batch.audio_hash(old_audio), 'Original shot audio packets changed'
        batch.p.write_json(folder / 'validation.json', dict(video=str(preview), sha256=batch.p.sha(preview),
            validation=validation, full_decode_passed=True, audio_packets_match_previous_shot=True,
            model_frames=sel[sid], super_resolution=False, user_visual_review='pending', completed=batch.stamp()))
        review.register(sid, preview, JOB / 'workflow.json', LABEL)
    batch.p.write_json(JOB / 'timing.json', dict(parts=PARTS, selection=sel, generated_frames=MODEL_FRAMES,
        method='Per-part uniform stretch; cut on the 17-frame latent block boundary; 024 output at the job root'))
    window = JOB / 'merged_fullframe_no_audio.mp4'
    if not window.exists():
        batch.p.run([batch.p.FFMPEG, '-v', 'error', '-y', '-i', raw, '-vf', 'setpts=N/(24*TB),setsar=1',
            '-r', '24', '-an', '-c:v', 'libx264', '-crf', '12', '-pix_fmt', 'yuv420p', window])


UNIT = '024-026'
UNIT_LABEL = '修订2·合并生成·英文竖排企画'


def detected_cut(raw):
    """Model frame where the generated cut actually landed (largest scene change near the planned one)."""
    out = batch.p.run([batch.p.FFMPEG, '-hide_banner', '-i', raw, '-vf', "select='gte(scene,0)',metadata=print",
        '-an', '-f', 'null', '-']).stderr
    scores, frame = {}, None
    for line in out.splitlines():
        if 'pts_time:' in line:
            frame = round(float(line.split('pts_time:')[1].split()[0]) * 24)  # raw output is 24 fps
        elif 'lavfi.scene_score=' in line:
            scores[frame] = float(line.split('=')[1])
    planned = PARTS[0][2]
    return max(range(planned - 6, planned + 4), key=lambda k: scores.get(k, 0))


def merge_unit(plan):
    """Review 024-026 as one unit: 60 frames from the single generation with the original audio span."""
    batch.NATIVE_ONLY = True  # validate as a native 1024x576 candidate, as the shared runner does
    folder = JOB / 'unit_024-026'
    folder.mkdir(exist_ok=True)
    raw = batch.read(JOB / 'h3_result.json')['raw']
    cut = detected_cut(raw)
    # Keep 025's frames before the generated cut and 026's frames after it.
    (a0, a1, _), (b0, b1, _) = PARTS
    na, nb = a1 - a0, b1 - b0
    chosen = ([math.floor(i * (cut - 1) / (na - 1) + 0.5) for i in range(na)]
              + [cut + math.floor(j * (MODEL_FRAMES - 1 - cut) / (nb - 1) + 0.5) for j in range(nb)])
    frames = na + nb
    native = folder / 'native_fullframe.mp4'
    batch.p.run([batch.p.FFMPEG, '-v', 'error', '-y', '-i', raw, '-vf',
        'select=' + '+'.join(f'eq(n\\,{i})' for i in chosen) + ',setpts=N*1001/(24000*TB),setsar=1',
        '-frames:v', frames, '-r', '24000/1001', '-an', '-c:v', 'libx264', '-crf', '12', '-pix_fmt', 'yuv420p', native])
    start, end = a0 * 1001 / 24000, b1 * 1001 / 24000
    preview = folder / 'review_with_audio.mp4'
    batch.p.run([batch.p.FFMPEG, '-v', 'error', '-y', '-i', native, '-i', PROD / 'original_audio_master.m4a',
        '-map', '0:v:0', '-map', '1:a:0', '-af', f'atrim=start={start:.12f}:end={end:.12f},asetpts=PTS-STARTPTS',
        '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-t', f'{frames * 1001 / 24000:.9f}', '-movflags', '+faststart',
        preview])
    validation = batch.validate_output(preview, frames, True)
    source = review.REVIEW / 'source' / f'{UNIT}.mp4'
    if not source.exists():
        batch.p.run([batch.p.FFMPEG, '-v', 'error', '-y', '-i', plan['source'], '-vf',
            f'trim=start_frame={a0}:end_frame={b1},setpts=N*1001/(24000*TB),scale=1024:576:flags=lanczos,setsar=1',
            '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '19', '-pix_fmt', 'yuv420p', '-r', '24000/1001', source])
    assert batch.p.video_info(source)['frames'] == frames
    batch.p.write_json(folder / 'validation.json', dict(video=str(preview), sha256=batch.p.sha(preview),
        validation=validation, full_decode_passed=True, generated_cut_model_frame=cut, planned_cut_model_frame=PARTS[0][2],
        model_frames=chosen, audio=f'original master {start:.6f}-{end:.6f}s, AAC 192k (same method as per-shot clips)',
        super_resolution=False, user_visual_review='pending', completed=batch.stamp()))
    with review.LOCK:
        manifest = review.read(review.REVIEW / 'manifest.json')
        if not any(s['id'] == UNIT for s in manifest['shots']):
            members = [s for s in manifest['shots'] if s['id'] in ('024', '025', '026')]
            first = next(x for x in plan['shots'] if x['id'] == '024')
            last = next(x for x in plan['shots'] if x['id'] == '026')
            unit = dict(id=UNIT, start=first['start_seconds'], end=last['end_seconds'], frames=frames,
                cast=['deepseek', 'gemini'], credits=first['credits'],
                description='024-026 合并单元：街道远景两人走近（原024–025），切到两人近景（原026）；企画七列字幕全程固定。',
                source_video=str(source), planned_prompt='（合并单元，没有原计划提示词；实际提示词见各版本。）',
                versions=[], merged_from=['024', '025', '026'], members=members, created=review.stamp())
            at = manifest['shots'].index(members[0])
            manifest['shots'] = [s for s in manifest['shots'] if s['id'] not in unit['merged_from']]
            manifest['shots'].insert(at, unit)
            review.write(review.REVIEW / 'manifest.json', manifest)
    review.register(UNIT, preview, JOB / 'workflow.json', UNIT_LABEL)
    return cut


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--prepare', action='store_true')
    ap.add_argument('--unit', action='store_true', help='build and register the merged 024-026 review unit')
    args = ap.parse_args()
    plan, sel, prepared = prepare()
    if args.prepare:
        print(json.dumps(batch.read(JOB / 'preparation.json'), ensure_ascii=False))
    elif args.unit:
        print('generated cut at model frame', merge_unit(plan))
    else:
        if not (JOB / 'validation.json').exists():
            runner.run(*prepared, label=LABEL)
        split(plan, sel)
