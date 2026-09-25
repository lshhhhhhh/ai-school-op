"""4K test of two free anime upscalers on the 001-020 teaser (1080p -> 2160p, 2x).

  AnimeJaNai HD V3 Compact  (PyTorch through spandrel; CC BY-NC-SA 4.0, non-commercial)
  Real-CUGAN pro up2x-conservative (bilibili, via nihui's realcugan-ncnn-vulkan)

Runs on the GPU only after the GPU is cool and no safety STOP exists; writes one 4K file per model
with the teaser's audio next to the 1080p teasers.
"""
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import spandrel
import torch
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
PROD = ROOT / 'assets/anime_op/school_full_op_v1'
TOOLS = ROOT / 'tools/upscalers'
OUT = PROD / 'upscale_4k_test'
DELIVERY = ROOT / 'deliverables/ai_school_op/完整OP'
TEASER = DELIVERY / 'AI校园OP_001-020_1080p_预热样片_云端片头.mp4'
FF = r'C:\Program Files\ffmpeg\bin\ffmpeg.exe'
FPS = '24000/1001'
ENCODE = ['-c:v', 'libx264', '-preset', 'slow', '-crf', '16', '-pix_fmt', 'yuv420p']


def wait_cool(limit=57):
    if (PROD / 'runtime/STOP_GPU_GUARD.json').exists():
        sys.exit('GPU safety STOP present; not running')
    while int(subprocess.run(['nvidia-smi', '--query-gpu=temperature.gpu', '--format=csv,noheader,nounits'],
                             capture_output=True, text=True).stdout.split()[0]) > limit:
        time.sleep(20)


def mux(video, name):
    final = DELIVERY / name
    subprocess.run([FF, '-v', 'error', '-y', '-i', video, '-i', TEASER, '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy',
                    '-movflags', '+faststart', final], check=True)
    print('READY', final, flush=True)


def frames():
    folder = OUT / 'frames_1080p'
    if not folder.exists():
        folder.mkdir(parents=True)
        subprocess.run([FF, '-v', 'error', '-i', TEASER, '-vsync', '0', folder / '%04d.png'], check=True)
    return sorted(folder.glob('*.png'))


def animejanai(pngs):
    model = spandrel.ModelLoader().load_from_file(TOOLS / '2x_AnimeJaNai_HD_V3_ModelsOnly/2x_AnimeJaNai_HD_V3_Compact.pth')
    net = model.model.cuda().half().eval()
    video = OUT / 'animejanai_4k_video.mp4'
    enc = subprocess.Popen([FF, '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '3840x2160', '-r', FPS,
                            '-i', '-', *ENCODE, '-r', FPS, video], stdin=subprocess.PIPE)
    with torch.inference_mode():
        for png in pngs:
            x = torch.from_numpy(np.asarray(Image.open(png).convert('RGB'))).cuda().half().permute(2, 0, 1)[None] / 255
            y = net(x).clamp(0, 1)[0].permute(1, 2, 0).mul(255).round().byte().cpu().numpy()
            enc.stdin.write(y.tobytes())
    enc.stdin.close()
    enc.wait()
    mux(video, 'AI校园OP_001-020_4K_AnimeJaNai.mp4')


def realcugan():
    out = OUT / 'realcugan_4k_frames'
    out.mkdir(exist_ok=True)
    exe = TOOLS / 'realcugan-ncnn-vulkan-20220728-windows/realcugan-ncnn-vulkan.exe'
    subprocess.run([exe, '-i', OUT / 'frames_1080p', '-o', out, '-s', '2', '-n', '-1', '-m', exe.parent / 'models-pro',
                    '-f', 'png'], check=True)
    video = OUT / 'realcugan_4k_video.mp4'
    subprocess.run([FF, '-v', 'error', '-y', '-framerate', FPS, '-i', out / '%04d.png', *ENCODE, '-r', FPS, video], check=True)
    mux(video, 'AI校园OP_001-020_4K_RealCUGAN.mp4')


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    pngs = frames()
    wait_cool()
    animejanai(pngs)
    wait_cool()
    realcugan()
