"""Full OP at native 1024x576 from the review page (CPU only).

Per review unit: the approved version decided last (009: revision 8 over revision 6); a unit with no approval
may only use its registered original_copy version (085). The approved clip itself is concatenated, so the film
is exactly what was reviewed; the original audio master is laid under the whole 2181 frames.
"""
import shutil

import review_school_op as review
import overnight_queue as oq

batch = oq.batch
p = batch.p
OUT = oq.PROD / 'full_op_native'
DELIVERY = oq.ROOT / 'deliverables/ai_school_op/完整OP/AI校园OP_完整版_1024x576.mp4'


def chosen():
    manifest = review.read(review.REVIEW / 'manifest.json')
    decisions = review.read(review.REVIEW / 'review_state.json')['decisions']
    picks = []
    for unit in manifest['shots']:
        approved = [(decisions[f"{unit['id']}/{v['id']}"]['updated'], v) for v in unit['versions']
                    if decisions.get(f"{unit['id']}/{v['id']}", {}).get('status') == 'approved']
        if approved:
            v, how = max(approved, key=lambda x: x[0])[1], 'approved'
        else:
            copies = [v for v in unit['versions'] if v['prompt'].get('kind') == 'original_copy']
            assert len(copies) == 1, f"{unit['id']}: no approved version"
            v, how = copies[0], 'original_copy (not marked approved)'
        picks.append((unit, v, how))
    return picks


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    plan = batch.read(oq.PROD / 'full_plan.json')
    picks, cursor, records = chosen(), 0, []
    for unit, v, how in picks:
        info = p.video_info(v['video'])
        assert (info['width'], info['height'], info['frames'], info['fps']) == (1024, 576, unit['frames'], '24000/1001'), (unit['id'], info)
        assert p.sha(v['video']) == v['video_sha256'], unit['id']
        records.append(dict(unit=unit['id'], start_frame=cursor, frames=unit['frames'], version=v['id'],
                            label=v.get('label'), choice=how, clip=v['video']))
        cursor += unit['frames']
    assert cursor == plan['frames'] == 2181
    listing = OUT / 'concat.txt'
    listing.write_text(''.join(f"file '{r['clip'].replace(chr(92), '/')}'\n" for r in records), encoding='utf-8')
    video = OUT / 'video_1024x576.mp4'
    p.run([p.FFMPEG, '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', listing, '-map', '0:v:0',
           '-vf', 'setpts=N*1001/(24000*TB),setsar=1', '-frames:v', cursor, '-r', '24000/1001', '-an',
           '-c:v', 'libx264', '-preset', 'slow', '-crf', '14', '-pix_fmt', 'yuv420p', video])
    final = OUT / DELIVERY.name
    p.run([p.FFMPEG, '-v', 'error', '-y', '-i', video, '-i', oq.PROD / 'original_audio_master.m4a',
           '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '256k',
           '-t', f'{cursor * 1001 / 24000:.9f}', '-movflags', '+faststart', final])
    info = p.video_info(final)
    assert (info['width'], info['height'], info['frames'], info['has_audio']) == (1024, 576, cursor, True), info
    p.run([p.FFMPEG, '-v', 'error', '-i', final, '-f', 'null', '-'])
    shutil.copy2(final, DELIVERY)
    p.write_json(OUT / 'full_op_manifest.json', dict(created=batch.stamp(), video=str(DELIVERY), sha256=p.sha(DELIVERY),
        validation=info, audio='original master, AAC 256k', units=records))
    print('FULL OP', DELIVERY.name, info, flush=True)
    for r in records:
        if r['choice'] != 'approved' or r['unit'] == '009':
            print(r['unit'], r['label'], r['choice'], flush=True)


if __name__ == '__main__':
    main()
