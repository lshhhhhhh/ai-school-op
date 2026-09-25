"""041_043 revision 6b: true image-to-video from the user's two GPT keyframes, no source video input.

Every edit mode that fed the source video reproduced the silhouetted Yanami, so the wide shot and the
close-up are each generated from their keyframe as the first frame (MiniMaxH3ImageToVideo). Assembly:
wide = first 36 generated frames, close-up = first 41 generated frames (source frames 36-76), a linear
dissolve over source frames 43-75 into the first pure book frame, then the original book frames 76-88.
"""
import copy
import time

import build_teaser_001_020 as teaser
import overnight_queue as oq
import review_school_op as review

batch = oq.batch
PROD, ROOT = oq.PROD, oq.ROOT
JOB = PROD / 'revisions/041_043_i2v_v6b'
KEYS = ROOT / 'deliverables/ai_school_op/完整OP/生图参考'
STYLE = ('2D-animated television anime with flat cel shading, two-tone shadows and clean dark linework, '
         'matching the drawing style and colors of <Picture 1>')
PARTS = [
    dict(name='wide', image=KEYS / '041_043_远景.png', keep=36, seed=2609234141, prompt=(
        'For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n'
        f'integrated_multimodal_description: [Shot 1] {STYLE}, the shot begins from <Picture 1>: inside a picture shaped like '
        'the curved top edge of an open book\'s pages over a black background, a low-angle view of a bright blue sky with '
        'white clouds above a white railing, with flat graphic shapes floating over it. The girl with long wavy dark-blue '
        'hair, whale-fin ears and a whale tail stands small by the railing while her long hair and skirt blow gently in '
        'the wind, and the graphic shapes drift slightly. The camera holds a static shot; the page-shaped frame, the '
        'composition and the girl\'s appearance stay as in <Picture 1>.\n\n'
        'overall_soundscape: N/A\n\nnon_diegetic_music: N/A')),
    dict(name='close', image=KEYS / '041_043_特写.png', keep=41, seed=2609234142, prompt=(
        'For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n'
        f'integrated_multimodal_description: [Shot 1] {STYLE}, the shot begins from <Picture 1>: a low-angle close-up of '
        'the girl with long wavy dark-blue hair, a blue hairband and whale-fin ears against the sky, inside the same '
        'page-shaped frame, with flat graphic shapes floating over the picture. Her long hair blows across the frame in '
        'the wind and one hand stays near her collar; she moves only slightly. The camera holds a static shot; the '
        'composition and her appearance stay as in <Picture 1>.\n\n'
        'overall_soundscape: N/A\n\nnon_diegetic_music: N/A')),
]
LENGTH, DISSOLVE, BOOK_FROM = 56, (43, 76), 76
FPS = 24000 / 1001


def workflow_for(part, old):
    wf = {k: copy.deepcopy(v) for k, v in old.items() if k not in ('104', '210', '211') and v['class_type'] != 'LoadImage'}
    wf['200'] = dict(class_type='LoadImage', inputs=dict(image=batch.p.stage(part['image'])))
    wf['104'] = dict(class_type='MiniMaxH3ImageToVideo', inputs=dict(clip=['13', 0], vae=['11', 0], prompt=part['prompt'],
        width=1024, height=576, length=LENGTH, first_frame=['200', 0]))
    wf['15']['inputs']['noise_seed'] = part['seed']
    wf['92']['inputs']['filename_prefix'] = f"video/anime_op/school_full_op_v1/revisions/041_043_i2v_v6b/{part['name']}"
    return wf


def main():
    JOB.mkdir(parents=True, exist_ok=True)
    plan = batch.read(PROD / 'full_plan.json')
    shot = next(s for s in plan['shots'] if s['id'] == '041_043')
    n = shot['frames']
    old = batch.read(PROD / 'shots/041_043/A/h3_workflow.json')
    assert batch.p.sha(PARTS[0]['image']) != batch.p.sha(PARTS[1]['image']), 'the two keyframes are identical'
    flows = {p['name']: workflow_for(p, old) for p in PARTS}
    batch.p.write_json(JOB / 'workflow.json', dict(kind='h3_image_to_video_pair', parts=flows, note=(
        '图生动画：远景、特写各以答案图为首帧单独生成（不输入原片），取远景前36帧+特写前41帧；'
        '第43–76帧后期溶解到原片书本，第76帧起为原片。')))
    for p in PARTS:
        batch.p.write_json(JOB / f"workflow_{p['name']}.json", flows[p['name']])
        (JOB / f"prompt_{p['name']}.txt").write_text(p['prompt'], encoding='utf-8')
    while oq.gpu_temperature() > oq.COOL_C:
        time.sleep(20)
    raws = {}
    with teaser.guarded(JOB):
        for p in PARTS:
            raws[p['name']] = batch.gpu_job(flows[p['name']], JOB, 'h3_' + p['name'], flows[p['name']]['92']['inputs']['filename_prefix'])
    # Generated head: wide then close-up, at natural speed.
    head = JOB / 'generated_head.mp4'
    batch.p.run([batch.p.FFMPEG, '-v', 'error', '-y', '-i', raws['wide'], '-i', raws['close'], '-filter_complex',
        f"[0:v]trim=end_frame={PARTS[0]['keep']},setpts=N/(24*TB)[a];[1:v]trim=end_frame={PARTS[1]['keep']},setpts=N/(24*TB)[b];"
        '[a][b]concat=n=2:v=1:a=0,scale=1024:576:flags=lanczos,setsar=1,setpts=N*1001/(24000*TB)',
        '-r', '24000/1001', '-an', '-c:v', 'libx264', '-crf', '12', '-pix_fmt', 'yuv420p', head])
    assert batch.p.video_info(head)['frames'] == PARTS[0]['keep'] + PARTS[1]['keep'] == BOOK_FROM + 1
    # Book: first pure book frame held through the dissolve, then the original book frames.
    source_clip = next(s for s in review.read(review.REVIEW / 'manifest.json')['shots'] if s['id'] == '041_043')['source_video']
    held = DISSOLVE[1] - DISSOLVE[0]
    book = JOB / 'original_book.mp4'
    batch.p.run([batch.p.FFMPEG, '-v', 'error', '-y', '-i', source_clip, '-vf',
        f'trim=start_frame={BOOK_FROM}:end_frame={n},setpts=N/(24*TB),loop=loop={held}:size=1:start=0,'
        'setpts=N*1001/(24000*TB)', '-r', '24000/1001', '-an', '-c:v', 'libx264', '-crf', '12', '-pix_fmt', 'yuv420p', book])
    assert batch.p.video_info(book)['frames'] == held + n - BOOK_FROM
    native = JOB / 'native_fullframe.mp4'
    batch.p.run([batch.p.FFMPEG, '-v', 'error', '-y', '-i', head, '-i', book, '-filter_complex',
        f'[0:v][1:v]xfade=transition=fade:duration={held / FPS:.6f}:offset={DISSOLVE[0] / FPS:.6f},setsar=1',
        '-frames:v', n, '-r', '24000/1001', '-an', '-c:v', 'libx264', '-crf', '12', '-pix_fmt', 'yuv420p', native])
    assert batch.p.video_info(native)['frames'] == n, batch.p.video_info(native)
    old_audio = PROD / 'shots/041_043/A/native_review_with_audio.mp4'
    clip = JOB / 'review_with_audio.mp4'
    batch.p.run([batch.p.FFMPEG, '-v', 'error', '-y', '-i', native, '-i', old_audio, '-map', '0:v:0', '-map', '1:a:0',
                 '-c', 'copy', '-movflags', '+faststart', clip])
    batch.NATIVE_ONLY = True
    validation = batch.validate_output(clip, n, True)
    assert batch.audio_hash(clip) == batch.audio_hash(old_audio)
    batch.p.write_json(JOB / 'validation.json', dict(video=str(clip), sha256=batch.p.sha(clip), validation=validation,
        parts={p['name']: dict(keep=p['keep'], raw=str(raws[p['name']])) for p in PARTS}, dissolve=list(DISSOLVE),
        original_book_from=BOOK_FROM, completed=batch.stamp()))
    review.register('041_043', clip, JOB / 'workflow.json', '修订6·图生动画（答案图首帧）+后期溶解')
    print('READY', clip, flush=True)


if __name__ == '__main__':
    main()
