"""Side-by-side comparison for Bilibili: original NCOP (1080p BDRip) left, final AI version right, 3840x2160 frame.

Each half is a full 1920x1080 picture; the 540-pixel bars above and below carry the labels and the title.
Audio is the final version's track (the original OP audio).
"""
import overnight_queue as oq

p = oq.batch.p
DELIVERY = oq.ROOT / 'deliverables/ai_school_op/完整OP'
FINAL = DELIVERY / 'final_version.mp4'
TARGET = DELIVERY / 'AI校园OP_原版对比_4K.mp4'
WORK = oq.PROD / 'comparison'
FONT = 'C\\:/Windows/Fonts/msyhbd.ttc'
TEXTS = dict(left='原版', right='AI校园版', title='《败犬女主太多了！》NCOP　原版 vs AI校园版')


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    plan = oq.batch.read(oq.PROD / 'full_plan.json')
    n = plan['frames']
    assert p.video_info(FINAL)['frames'] == n
    files = {}
    for key, text in TEXTS.items():
        files[key] = WORK / f'{key}.txt'
        files[key].write_text(text, encoding='utf-8')
    def text(key, size, x, y, color='white'):
        return (f"drawtext=fontfile='{FONT}':textfile='{files[key].as_posix().replace(':', chr(92) + ':')}':"
                f'fontsize={size}:fontcolor={color}:x={x}:y={y}')
    graph = ';'.join([
        f'[0:v]trim=end_frame={n},setpts=N*1001/(24000*TB),format=yuv420p,setsar=1[l]',
        f'[1:v]trim=end_frame={n},setpts=N*1001/(24000*TB),scale=1920:1080:flags=lanczos,format=yuv420p,setsar=1[r]',
        '[l][r]hstack=inputs=2,pad=3840:2160:0:540:color=0x101010,'
        'drawbox=x=1917:y=540:w=6:h=1080:color=white@0.85:t=fill,'
        + ','.join([text('left', 120, '960-text_w/2', '540-text_h-110'),
                    text('right', 120, '2880-text_w/2', '540-text_h-110', color='0x7FD4FF'),
                    text('title', 72, '(w-text_w)/2', '1620+(540-text_h)/2', color='0xDDDDDD')]) + '[v]',
    ])
    p.run([p.FFMPEG, '-v', 'error', '-y', '-i', plan['source'], '-i', FINAL, '-filter_complex', graph,
           '-map', '[v]', '-map', '1:a:0', '-frames:v', n, '-r', '24000/1001', '-c:v', 'libx264', '-preset', 'slow',
           '-crf', '16', '-pix_fmt', 'yuv420p', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709',
           '-c:a', 'copy', '-movflags', '+faststart', TARGET])
    info = p.video_info(TARGET)
    assert (info['frames'], info['width'], info['height'], info['fps'], info['has_audio']) == (n, 3840, 2160, '24000/1001', True), info
    audio = [p.run([p.FFMPEG, '-v', 'error', '-i', f, '-map', '0:a:0', '-c:a', 'copy', '-f', 'hash', '-hash', 'sha256', '-']).stdout
             for f in (FINAL, TARGET)]
    assert audio[0] == audio[1]
    p.run([p.FFMPEG, '-v', 'error', '-i', TARGET, '-f', 'null', '-'])
    print('READY', TARGET.name, info, round(TARGET.stat().st_size / 2**20), 'MiB', flush=True)


if __name__ == '__main__':
    main()
