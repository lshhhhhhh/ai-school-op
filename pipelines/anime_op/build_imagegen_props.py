"""081-083 prop cards remade as AI-school versions from user-made GPT images (the 011 imagegen route).

Each generated image becomes a still clip with the shot's frame count and original audio, registered
for review with its reference frame, generated image and the prompt the user ran.
"""
import json
import shutil
from pathlib import Path

import overnight_queue as oq
import review_school_op as review

PROD = review.PROD
IMAGES = review.ROOT / 'deliverables/ai_school_op/完整OP/生图参考'
PROMPTS = {
    '081': '学生食堂大菜单（DeepSeek）：标题「深度求索 学生食堂」；「R1 长考拉面（附思考时间）」每百万Token ¥2；'
           '「V3 特盛 MoE 定食（256位专家）」每百万Token ¥1；「开源盖饭 免费续碗」¥0；「小鲸鱼布丁」¥0.5；角落蓝色鲸鱼图标。'
           '以原片截图为基础，保持构图、版式、配色和平涂赛璐璐画风，只替换文字和菜品插图，简体中文。',
    '082': 'Gemini 主题竞速跑鞋：蓝紫粉渐变鞋身、白鞋带、鞋侧闪电和「FLASH」、标签「Gemini 3.8 Flash」、可选香蕉挂饰。'
           '以原片截图为基础，保持构图、鞋子造型摆放、背景和平涂赛璐璐画风，只改配色和标识。',
    '083': '文艺部招新海报（Claude）：「文艺部 招新啦！」「长文阅读·写作 来者不拒」「截止：直到上下文用完为止」「部长：Claude」，'
           '一角橙色星芒图案。以原片截图为基础，保持构图、墙面、告示位置大小和平涂赛璐璐画风，只替换告示内容，简体中文。',
}


def build(sid, plan):
    s = next(x for x in plan['shots'] if x['id'] == sid)
    job = PROD / 'revisions' / f'{sid}_imagegen_v1'
    job.mkdir(parents=True, exist_ok=True)
    generated, reference = job / 'generated_frame.png', job / 'reference_frame.png'
    shutil.copy2(IMAGES / f'{sid}.png', generated)
    shutil.copy2(IMAGES / f'{sid}_原片参考.png', reference)
    audio = job / 'original_audio.m4a'
    oq.master_audio(s['start'], s['end'], audio)
    clip = job / 'review_with_audio.mp4'
    oq.batch.p.run([oq.batch.p.FFMPEG, '-v', 'error', '-y', '-loop', '1', '-i', generated, '-i', audio,
        '-map', '0:v:0', '-map', '1:a:0', '-frames:v', s['frames'], '-vf', 'scale=1024:576:flags=lanczos,setsar=1',
        '-r', '24000/1001', '-c:v', 'libx264', '-crf', '12', '-pix_fmt', 'yuv420p', '-c:a', 'copy',
        '-movflags', '+faststart', clip])
    info = oq.batch.p.video_info(clip)
    assert (info['frames'], info['width'], info['height'], info['has_audio']) == (s['frames'], 1024, 576, True), info
    recipe = dict(kind='builtin_imagegen', prompt=PROMPTS[sid], tool='GPT image generation, run by the user',
        reference_image=str(reference), reference_image_sha256=oq.batch.p.sha(reference),
        generated_image=str(generated), generated_image_sha256=oq.batch.p.sha(generated),
        builder=str(Path(__file__).resolve()), builder_sha256=oq.batch.p.sha(Path(__file__).resolve()),
        source_frames=[s['start'], s['end']], output_frames=s['frames'], fps='24000/1001',
        postprocessing=f"Whole-frame resize to 1024x576 for review, {s['frames']}-frame still hold, original audio.",
        super_resolution=False, local_character_composite=False)
    (job / 'workflow.json').write_text(json.dumps(recipe, ensure_ascii=False, indent=1), encoding='utf-8')
    review.register(sid, clip, job / 'workflow.json', '生图·AI校园版道具')
    print(sid, info['frames'], 'frames registered')


if __name__ == '__main__':
    plan = json.loads((PROD / 'full_plan.json').read_text(encoding='utf-8-sig'))
    for sid in PROMPTS:
        build(sid, plan)
