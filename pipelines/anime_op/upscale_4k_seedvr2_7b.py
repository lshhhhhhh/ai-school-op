"""4K test of SeedVR2 7B on the 001-020 teaser (the same 1080p input as the AnimeJaNai / Real-CUGAN 4K tests).

Each shot is upscaled on its own so no diffusion chunk spans a cut; shots longer than MAX frames are cut into
overlapping pieces that are crossfaded over OV frames. Runs inside the shared GPU safety session; every piece
is a resumable gpu_job, so a rerun continues where it stopped.
"""
import subprocess
import sys
import time
from types import SimpleNamespace

import build_teaser_001_020 as teaser
import overnight_queue as oq

batch = oq.batch
p = batch.p


def wait_cool(limit):
    if (oq.PROD / 'runtime/STOP_GPU_GUARD.json').exists():
        sys.exit('GPU safety STOP present; not running')
    while oq.gpu_temperature() > limit:
        time.sleep(20)


def mux(video, name):
    final = free.DELIVERY / name
    subprocess.run([p.FFMPEG, '-v', 'error', '-y', '-i', video, '-i', free.TEASER, '-map', '0:v:0', '-map', '1:a:0',
                    '-c', 'copy', '-movflags', '+faststart', final], check=True)
    print('READY', final, flush=True)


# The settings of upscale_4k_test.py (which needs ComfyUI's Python for spandrel), shared so the tests compare.
_delivery = oq.ROOT / 'deliverables/ai_school_op/完整OP'
free = SimpleNamespace(OUT=oq.PROD / 'upscale_4k_test', DELIVERY=_delivery, FPS='24000/1001',
                       TEASER=_delivery / 'AI校园OP_001-020_1080p_预热样片_云端片头.mp4',
                       ENCODE=['-c:v', 'libx264', '-preset', 'slow', '-crf', '16', '-pix_fmt', 'yuv420p'],
                       wait_cool=wait_cool, mux=mux)
OUT = free.OUT / 'seedvr2_7b'
FINAL = free.DELIVERY / 'AI校园OP_001-020_4K_SeedVR2_7B.mp4'
MODEL, VAE = 'seedvr2_7b_fp16.safetensors', 'seedvr2_ema_vae_fp16.safetensors'
W, H, MAX, OV = 3840, 2160, 33, 8
CHUNK = 13  # frames per diffusion chunk: 33 ran out of 32 GB VRAM at 4K (7B), 13 fit
FPS = 24000 / 1001
SEED = 2609240700


def node(kind, **inputs):
    return dict(class_type=kind, inputs=inputs)


def workflow(source, prefix, seed, frames):
    # Pieces up to CHUNK frames are one chunk either way; they keep the graph they were first rendered with.
    chunk, overlap = (MAX, 2) if frames <= CHUNK else (CHUNK, 1)
    return {
        '1': node('LoadVideo', file=p.stage(source)),
        '2': node('GetVideoComponents', video=['1', 0]),
        '3': node('ImageScale', image=['2', 0], upscale_method='lanczos', width=W, height=H, crop='disabled'),
        '4': node('SeedVR2Preprocess', resized_images=['3', 0]),
        '5': node('VAEEncodeTiled', pixels=['4', 0], vae=['21', 0], tile_size=768, overlap=128, temporal_size=32, temporal_overlap=8),
        '6': node('SeedVR2TemporalChunk', latent=['5', 0], temporal_overlap=overlap, chunking_mode='manual',
                  **{'chunking_mode.frames_per_chunk': chunk}),
        '7': node('SeedVR2Conditioning', model=['20', 0], vae_conditioning=['6', 0]),
        '8': node('KSampler', model=['20', 0], seed=seed, steps=1, cfg=1.0, sampler_name='euler', scheduler='simple',
                  positive=['7', 0], negative=['7', 1], latent_image=['6', 0], denoise=1.0),
        '9': node('SeedVR2TemporalMerge', latents=['8', 0], temporal_overlap=['6', 1]),
        '10': node('VAEDecodeTiled', samples=['9', 0], vae=['21', 0], tile_size=768, overlap=128, temporal_size=32, temporal_overlap=8),
        '11': node('SeedVR2PostProcessing', images=['10', 0], original_resized_images=['3', 0], color_correction_method='lab'),
        '12': node('CreateVideo', images=['11', 0], fps=24, bit_depth=8),
        '13': node('SaveVideo', video=['12', 0], filename_prefix=prefix, format='auto', codec='auto'),
        '20': node('UNETLoader', unet_name=MODEL, weight_dtype='default'),
        '21': node('VAELoader', vae_name=VAE),
    }


def pieces(n):
    starts = list(range(0, max(n - OV, 1), MAX - OV))
    return [(a, min(a + MAX, n)) for a in starts]


def encode(args, target, crf='12'):
    p.run([p.FFMPEG, '-v', 'error', '-y', *args, '-r', '24000/1001', '-an', '-c:v', 'libx264', '-crf', crf,
           '-pix_fmt', 'yuv420p', target])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    plan = batch.read(oq.PROD / 'full_plan.json')
    ids = [s['id'] for s in plan['shots']]
    shots = plan['shots'][:ids.index('020') + 1]
    assert sum(s['frames'] for s in shots) == p.video_info(free.TEASER)['frames']
    jobs = []
    for k, s in enumerate(shots):
        for i, (a, b) in enumerate(pieces(s['frames'])):
            name = f"{s['id']}_{i}"
            source = OUT / f'{name}_input_1080p.mp4'
            if not source.exists():
                encode(['-i', free.TEASER, '-vf', f"trim=start_frame={s['start'] + a}:end_frame={s['start'] + b},"
                        'setpts=N*1001/(24000*TB)', '-frames:v', b - a], source)
            prefix = 'video/anime_op/school_full_op_v1/upscale_4k_test/seedvr2_7b/' + name
            jobs.append((s, i, a, b, name, workflow(source, prefix, SEED + 100 * k + i, b - a), prefix))
    print(len(jobs), 'pieces for', len(shots), 'shots', flush=True)
    free.wait_cool(oq.COOL_C)
    raws = {}
    with teaser.guarded(OUT):
        for s, i, a, b, name, wf, prefix in jobs:
            p.write_json(OUT / f'{name}_workflow.json', wf)
            raws[name] = batch.gpu_job(wf, OUT, 'seedvr2_7b_' + name, prefix)
            r = batch.read(OUT / f'seedvr2_7b_{name}_result.json')
            print(f"piece {name} ({b - a} frames): {r['seconds']:.0f}s, peak {r['peak_vram_mb'] / 1024:.1f} GiB", flush=True)
    clips = []
    for s in shots:
        parts = [(i, a, b, name) for t, i, a, b, name, _, _ in jobs if t is s]
        for i, a, b, name in parts:
            piece = OUT / f'{name}_4k.mp4'
            if not piece.exists():
                encode(['-i', raws[name], '-vf', f'trim=end_frame={b - a},setpts=N*1001/(24000*TB),setsar=1',
                        '-frames:v', b - a], piece)
            assert p.video_info(piece)['frames'] == b - a and p.video_info(piece)['width'] == W
        clip = OUT / f"{s['id']}_4k.mp4"
        if len(parts) == 1:
            clip = OUT / f'{parts[0][3]}_4k.mp4'
        elif not clip.exists():
            inputs, chain, last = [], [], '0:v'
            for j, (i, a, b, name) in enumerate(parts):
                inputs += ['-i', OUT / f'{name}_4k.mp4']
                if j:
                    chain.append(f'[{last}][{j}:v]xfade=transition=fade:duration={OV / FPS:.6f}:offset={a / FPS:.6f}[x{j}]')
                    last = f'x{j}'
            encode([*inputs, '-filter_complex', ';'.join(chain), '-map', f'[{last}]', '-frames:v', s['frames']], clip)
        assert p.video_info(clip)['frames'] == s['frames'], (s['id'], p.video_info(clip))
        clips.append(clip)
    listing = OUT / 'concat.txt'
    listing.write_text(''.join(f"file '{c.as_posix()}'\n" for c in clips), encoding='utf-8')
    video = OUT / 'seedvr2_7b_4k_video.mp4'
    p.run([p.FFMPEG, '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', listing, '-an', *free.ENCODE, '-r', free.FPS, video])
    info = p.video_info(video)
    assert (info['frames'], info['width'], info['height']) == (396, W, H), info
    free.mux(video, FINAL.name)
    assert batch.audio_hash(FINAL) == batch.audio_hash(free.TEASER)
    p.write_json(OUT / 'validation.json', dict(video=str(FINAL), sha256=p.sha(FINAL), info=p.video_info(FINAL), model=MODEL,
        input=str(free.TEASER), pieces=len(jobs), max_frames=MAX, crossfade_frames=OV, completed=batch.stamp()))


if __name__ == '__main__':
    main()
