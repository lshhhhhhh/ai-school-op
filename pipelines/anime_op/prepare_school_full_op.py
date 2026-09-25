"""CPU-only shot inventory; one source thumbnail per candidate shot for planning."""
import json
import re
import subprocess
import sys
from pathlib import Path

import op_pipeline as p
from PIL import Image, ImageDraw, ImageFont

ROOT = p.ROOT
OUT = ROOT / 'assets/anime_op/school_full_op_v1'
OUT.mkdir(parents=True, exist_ok=True)
PLAN = json.loads((ROOT / 'assets/anime_op/makeine_school_first12s_v2/delivery_plan_gemini_motion_v1.json').read_text(encoding='utf-8-sig'))
SOURCE = Path(PLAN['source'])
FPS = 24000 / 1001


def main():
    inventory = OUT / 'source_inventory'
    inventory.mkdir(exist_ok=True)
    probe = json.loads(p.run([p.FFPROBE, '-v', 'error', '-select_streams', 'v:0', '-count_frames',
        '-show_entries', 'stream=nb_read_frames,r_frame_rate,width,height', '-of', 'json', SOURCE]).stdout)['streams'][0]
    total = int(probe['nb_read_frames'])
    scores_path = inventory / 'scene_scores.txt'
    if not scores_path.exists():
        # Metadata contains numerical scene-change scores, not a frame dump.
        proc = p.run([p.FFMPEG, '-hide_banner', '-i', SOURCE, '-vf',
            'scale=480:270,select=gte(scene\\,0),metadata=print', '-an', '-f', 'null', '-'])
        scores_path.write_text(proc.stderr, encoding='utf-8')
    data = scores_path.read_text(encoding='utf-8')
    pairs = re.findall(r'frame:\d+\s+pts:\d+\s+pts_time:([0-9.]+).*?lavfi.scene_score=([0-9.]+)', data, re.S)
    scores = {round(float(t) * FPS): float(score) for t, score in pairs}
    candidates = sorted((f for f, score in scores.items() if f > 288 and score >= 0.20), key=lambda f: -scores[f])
    selected = []
    for frame in candidates:
        if frame < total - 4 and all(abs(frame - other) >= 5 for other in selected):
            selected.append(frame)
    cuts = sorted(set(PLAN['cuts'] + selected + [total]))
    shots = []
    for index, (start, end) in enumerate(zip(cuts, cuts[1:]), 1):
        middle = (start + end - 1) // 2
        shots.append(dict(id=f'{index:03d}', start=start, end=end, frames=end-start,
            start_seconds=start/FPS, end_seconds=end/FPS, representative_frame=middle,
            cut_score=scores.get(start), action='plan_pending', cast=[], credits=[]))
    frames = [s['representative_frame'] for s in shots]
    select = '+'.join(f'eq(n\\,{i})' for i in frames)
    thumb = inventory / 'thumbnail_%03d.jpg'
    p.run([p.FFMPEG, '-v', 'error', '-y', '-i', SOURCE, '-vf', f'select={select},scale=384:216',
        '-fps_mode', 'vfr', '-q:v', '3', thumb])
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 16)
    for page in range((len(shots) + 19) // 20):
        sheet = Image.new('RGB', (1536, 1200), '#202020')
        draw = ImageDraw.Draw(sheet)
        for local, shot in enumerate(shots[page*20:(page+1)*20]):
            x, y = (local % 4) * 384, (local // 4) * 240
            im = Image.open(inventory / f'thumbnail_{page*20+local+1:03d}.jpg')
            sheet.paste(im, (x, y))
            draw.text((x+6, y+218), f"{shot['id']}  {shot['start_seconds']:.2f}-{shot['end_seconds']:.2f}s  {shot['start']}:{shot['end']}", font=font, fill='white')
        sheet.save(inventory / f'shot_sheet_{page+1:02d}.jpg', quality=90)
    p.write_json(OUT / 'source_inventory.json', dict(source=str(SOURCE), source_sha256=p.sha(SOURCE),
        fps='24000/1001', frames=total, duration=total/FPS, scene_threshold=0.20, cuts=cuts, shots=shots,
        purpose='One thumbnail per candidate shot for cast/text planning, not per-frame aesthetic QA'))
    print(json.dumps(dict(frames=total, duration=total/FPS, candidate_shots=len(shots), sheets=(len(shots)+19)//20,
        cuts=cuts), ensure_ascii=False))


if __name__ == '__main__':
    main()
