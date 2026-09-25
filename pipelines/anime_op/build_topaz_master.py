"""1080p ProRes master of the full OP as input for Topaz (CPU only).

Same units and versions as assemble_full_op_native.py. Unedited original shots come from the 1080p BDRip,
imagegen cards from their generated images, everything else (H3 output and CPU text) is the approved
1024x576 clip scaled with lanczos only, so no second AI pass sits under Topaz. Audio: the full OP's track.
"""
import shutil

import assemble_full_op_native as native
import overnight_queue as oq

batch = oq.batch
p = batch.p
OUT = oq.PROD / 'topaz_master'
DELIVERY = native.DELIVERY.with_name('AI校园OP_完整版_Topaz输入_1080p_ProRes.mov')
PRORES = ['-c:v', 'prores_ks', '-profile:v', '3', '-pix_fmt', 'yuv422p10le', '-vendor', 'apl0']


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    plan = batch.read(oq.PROD / 'full_plan.json')
    audio_from = native.OUT / native.DELIVERY.name
    cursor, parts, records = 0, [], []
    for unit, v, how in native.chosen():
        n, kind = unit['frames'], v['prompt'].get('kind')
        target = OUT / f"{unit['id']}.mov"
        post = f",scale=1920:1080:flags=lanczos,setsar=1,setpts=N*1001/(24000*TB)"
        if kind in ('copy', 'original_copy'):
            args, method = ['-i', plan['source'], '-vf', f'trim=start_frame={cursor}:end_frame={cursor + n}' + post], '1080p BDRip'
        elif kind == 'builtin_imagegen':
            image = batch.read(v['prompt']['source'])['generated_image']
            args, method = ['-loop', '1', '-i', image, '-vf', 'null' + post], 'generated image'
        else:
            args, method = ['-i', v['video'], '-vf', 'null' + post], 'approved 1024x576 clip, lanczos'
        if not target.exists():
            p.run([p.FFMPEG, '-v', 'error', '-y', *args, '-frames:v', n, '-r', '24000/1001', '-an', *PRORES, target])
        info = p.video_info(target)
        assert (info['frames'], info['width'], info['height']) == (n, 1920, 1080), (unit['id'], info)
        parts.append(target)
        records.append(dict(unit=unit['id'], start_frame=cursor, frames=n, version=v['id'], kind=kind, method=method))
        cursor += n
    assert cursor == plan['frames']
    listing = OUT / 'concat.txt'
    listing.write_text(''.join(f"file '{t.as_posix()}'\n" for t in parts), encoding='utf-8')
    final = OUT / DELIVERY.name
    p.run([p.FFMPEG, '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', listing, '-i', audio_from,
           '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy', '-r', '24000/1001', final])
    info = p.video_info(final)
    assert (info['frames'], info['width'], info['height'], info['fps'], info['has_audio']) == \
        (cursor, 1920, 1080, '24000/1001', True), info
    assert batch.audio_hash(final) == batch.audio_hash(audio_from)
    p.run([p.FFMPEG, '-v', 'error', '-i', final, '-f', 'null', '-'])
    shutil.copy2(final, DELIVERY)
    p.write_json(OUT / 'topaz_master_manifest.json', dict(created=batch.stamp(), video=str(DELIVERY), sha256=p.sha(DELIVERY),
        validation=info, units=records))
    print('MASTER', DELIVERY.name, info, round(DELIVERY.stat().st_size / 2**30, 2), 'GiB', flush=True)


if __name__ == '__main__':
    main()
