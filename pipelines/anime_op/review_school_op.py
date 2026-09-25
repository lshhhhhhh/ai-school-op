"""CPU-only, local shot review. Never submits generation or changes production records."""
import argparse
import datetime as dt
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[2]
PROD = ROOT / 'assets/anime_op/school_full_op_v1'
REVIEW = PROD / 'review'
DELIVERY = ROOT / 'deliverables/ai_school_op/完整OP'
BASE = ROOT / 'assets/anime_op/makeine_school_first12s_v2'
FF = r'C:\Program Files\ffmpeg\bin\ffmpeg.exe'
FP = r'C:\Program Files\ffmpeg\bin\ffprobe.exe'
PORT = 8766
LOCK = threading.RLock()

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def stamp():
    return dt.datetime.now().astimezone().isoformat()

def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    for i in range(31):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == 30:
                raise
            time.sleep(.1)

def actual_prompt(s):
    """Return executed H3 conditioning, never silently substitute the frozen plan."""
    folder = PROD / 'shots' / s['id'] / 'A'
    wf = folder / 'h3_workflow.json'
    note = '本轮整镜头 H3 生成；提示词读取自实际工作流。'
    if s['mode'] == 'copy':
        return dict(text='', source=None, kind='copy', refs=[], seed=None,
                    note='原片图形/道具镜头，本候选没有 H3 人物或文字编辑提示词。部分前段复用了已超分素材。')
    if s['a_reuse']:
        if s['id'] in {'011', '012'}:
            return dict(text='', source=None, kind='cpu_text', refs=[], seed=None,
                        note='复用早期文字修整：011 是 CPU 字体排版的 AI，012 保留原片“が”字并清除旧字。当前画面不由一份 H3 提示词直接生成；之后经过已接受的超分及本次整幅降采样。')
        if s['id'] == '007':
            wf = BASE / 'refinements/07_gemini_fullframe_v1/workflow.json'
        else:
            old_id = Path(s['a_reuse']).parent.name
            wf = BASE / old_id / 'workflow.json'
        note = '复用早期候选；下方是该候选实际 H3 工作流提示词。后续已做过 SeedVR2，本轮仅整幅降采样；不是本轮计划中的新提示词。'
    if not wf.exists():
        return dict(text='', source=None, kind='unavailable', refs=[], seed=None,
                    note='暂未定位实际工作流；不把计划提示词冒充实际输入。')
    data = read(wf)
    nodes = [n for n in data.values() if n.get('class_type') == 'MiniMaxH3ReferenceToVideo']
    if len(nodes) != 1:
        raise ValueError(f'Cannot uniquely locate actual H3 prompt: {wf}')
    inp = nodes[0]['inputs']
    refs = []
    for key, link in inp.items():
        if key.startswith('ref_images.ref_image_'):
            node = data[str(link[0])]
            name = node['inputs'].get('image')
            path = ROOT / 'ComfyUI/input' / name if name else None
            refs.append(dict(index=int(key.rsplit('_', 1)[1]) + 1,
                             path=str(path) if path and path.is_file() else None, name=name))
    seed = next((n['inputs'].get('noise_seed') for n in data.values()
                 if n.get('class_type') == 'RandomNoise'), None)
    return dict(text=inp['prompt'], source=str(wf), workflow_sha256=sha(wf),
                kind='executed', note=note, refs=sorted(refs, key=lambda x: x['index']), seed=seed)

def source_clips(plan):
    dest = REVIEW / 'source'
    dest.mkdir(parents=True, exist_ok=True)
    marker = dest / 'validation.json'
    if marker.exists() and read(marker).get('source_sha256') == plan['source_sha256']:
        if all((dest / f'{i:03d}.mp4').is_file() for i in range(len(plan['shots']))):
            return
    # Encode original once on CPU; forced cut keyframes make each segment exact.
    cuts = [s['end'] for s in plan['shots'][:-1]]
    times = ','.join(f'{x * 1001 / 24000:.9f}' for x in cuts)
    subprocess.run([FF, '-v', 'error', '-y', '-i', plan['source'], '-map', '0:v:0',
        '-vf', 'scale=1024:576:flags=lanczos,setsar=1', '-an', '-c:v', 'libx264',
        '-threads', '4', '-preset', 'fast', '-crf', '19', '-pix_fmt', 'yuv420p',
        '-r', '24000/1001', '-force_key_frames', times, '-f', 'segment',
        '-segment_frames', ','.join(map(str, cuts)), '-reset_timestamps', '1',
        str(dest / '%03d.mp4')], check=True)
    checks = []
    for i, s in enumerate(plan['shots']):
        clip = dest / f'{i:03d}.mp4'
        result = subprocess.run([FP, '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height,nb_frames,r_frame_rate', '-of', 'json', str(clip)],
            capture_output=True, text=True, check=True)
        v = json.loads(result.stdout)['streams'][0]
        if (v['width'], v['height'], int(v['nb_frames']), v['r_frame_rate']) != (1024, 576, s['frames'], '24000/1001'):
            raise ValueError(f'Source segment mismatch {s["id"]}: {v}')
        checks.append(dict(id=s['id'], **v))
    write(marker, dict(source_sha256=plan['source_sha256'], checked=stamp(), clips=checks))

def prepare():
    plan = read(PROD / 'full_plan.json')
    REVIEW.mkdir(parents=True, exist_ok=True)
    source_clips(plan)
    manifest_path = REVIEW / 'manifest.json'
    manifest = read(manifest_path) if manifest_path.exists() else dict(schema=1, shots=[])
    previous = {s['id']: s for s in manifest['shots']}
    previous.update({m['id']: m for s in manifest['shots'] for m in s.get('members', [])})
    shots = []
    for i, s in enumerate(plan['shots']):
        entry = dict(id=s['id'], start=s['start_seconds'], end=s['end_seconds'], frames=s['frames'],
                     cast=s['cast'], credits=s['credits'], description=s['description'],
                     source_video=str(REVIEW / 'source' / f'{i:03d}.mp4'),
                     planned_prompt=s['prompt'], versions=previous.get(s['id'], {}).get('versions', []))
        done = PROD / 'shots' / s['id'] / 'A/native_complete.json'
        if done.exists():
            record = read(done)
            clip = Path(record['review_video'])
            if sha(clip) != record['review_sha256']:
                raise ValueError(f'Review video changed: {clip}')
            prompt = actual_prompt(s)
            psha = hashlib.sha256(prompt['text'].encode()).hexdigest()
            vid = 'a-' + record['review_sha256'][:12] + '-' + psha[:8]
            if not any(v['id'] == vid for v in entry['versions']):
                archive = REVIEW / 'versions' / s['id'] / vid
                archive.mkdir(parents=True, exist_ok=True)
                shutil.copy2(clip, archive / 'clip.mp4')
                (archive / 'prompt.txt').write_text(prompt['text'], encoding='utf-8')
                if prompt['source']:
                    shutil.copy2(prompt['source'], archive / 'workflow.json')
                entry['versions'].append(dict(id=vid, label='A · 当前样片',
                    video=str(archive / 'clip.mp4'), video_sha256=record['review_sha256'],
                    prompt=prompt, prompt_sha256=psha, created=stamp()))
        shots.append(entry)
    # Merged review units (e.g. 024-026) are not in the frozen plan; they replace their member
    # shots in the list, and the members' versions are kept inside the unit.
    for unit in [e for e in manifest['shots'] if e.get('merged_from')]:
        members = [e for e in shots if e['id'] in unit['merged_from']]
        at = shots.index(members[0])
        unit['members'] = members
        shots = [e for e in shots if e['id'] not in unit['merged_from']]
        shots.insert(at, unit)
    manifest.update(shots=shots, updated=stamp(), production_plan_sha256=sha(PROD / 'full_plan.json'))
    write(manifest_path, manifest)
    if not (REVIEW / 'review_state.json').exists():
        state = dict(schema=1, revision=0, decisions={}, history=[])
        issues = {'009': '远景Claude未替换；左右凭空新增乱码演职员块。',
                  '019': '约15秒出现两张无关白底人物图。',
                  '021': '大字原作者姓名没有正确替换，新名字写入了旁边小字栏。'}
        for s in shots:
            if s['id'] in issues and s['versions']:
                v = s['versions'][-1]
                state['decisions'][s['id'] + '/' + v['id']] = dict(status='changes',
                    note=issues[s['id']], prompt_draft='', updated=stamp(),
                    origin='用户在对话中已指出；定点核实', video_sha256=v['video_sha256'],
                    prompt_sha256=v['prompt_sha256'])
        write(REVIEW / 'review_state.json', state)
    DELIVERY.mkdir(parents=True, exist_ok=True)
    (DELIVERY / '逐镜验收.html').write_text(f'''<!doctype html><meta charset="utf-8"><title>逐镜验收</title>
<meta http-equiv="refresh" content="0;url=http://127.0.0.1:{PORT}/">
<p><a href="http://127.0.0.1:{PORT}/">打开校园 AI OP 逐镜验收</a></p>
<p>如无法打开，请运行同目录“启动逐镜验收.cmd”。验收记录保存在项目文件中。</p>''', encoding='utf-8')
    print(json.dumps(dict(shots=len(shots), ready=sum(bool(s['versions']) for s in shots),
                         review=str(REVIEW), url=f'http://127.0.0.1:{PORT}/'), ensure_ascii=False))

def apply_decision(manifest, state, body):
    if body.get('revision') != state['revision']:
        raise ValueError('验收记录已在另一页面更新，请刷新后重试。')
    shot = next((s for s in manifest['shots'] if s['id'] == body.get('shot')), None)
    version = next((v for v in (shot or {}).get('versions', []) if v['id'] == body.get('version')), None)
    if not version:
        raise ValueError('该片段版本不存在，不能确认未生成的片段。')
    if body.get('status') not in {'pending', 'approved', 'changes'}:
        raise ValueError('未知验收状态。')
    note, draft = body.get('note', ''), body.get('prompt_draft', '')
    if not isinstance(note, str) or not isinstance(draft, str) or len(note) > 20000 or len(draft) > 50000:
        raise ValueError('意见或提示词过长。')
    if body['status'] == 'changes' and not note.strip():
        raise ValueError('请先填写具体问题。')
    if sha(version['video']) != version['video_sha256']:
        raise ValueError('视频文件发生变化，拒绝将确认记录绑定到不同内容。')
    key = shot['id'] + '/' + version['id']
    item = dict(status=body['status'], note=note, prompt_draft=draft, updated=stamp(),
                origin='用户在逐镜验收页保存', video_sha256=version['video_sha256'],
                prompt_sha256=version['prompt_sha256'])
    state['history'].append(dict(shot=shot['id'], version=version['id'],
                                 before=state['decisions'].get(key), after=item))
    state['decisions'][key] = item
    state['revision'] += 1
    return state

class Handler(BaseHTTPRequestHandler):
    def reply(self, value, code=200):
        payload = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)

    def allowed_host(self):
        return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

    def file(self, path):
        path = Path(path)
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        ranges = self.headers.get('Range')
        if ranges:
            m = re.fullmatch(r'bytes=(\d+)-(\d*)', ranges)
            if not m:
                self.send_error(416)
                return
            start, end = int(m[1]), min(int(m[2]) if m[2] else size-1, size-1)
            if start > end:
                self.send_error(416)
                return
            status = 206
        self.send_response(status)
        self.send_header('Content-Type', mimetypes.guess_type(str(path))[0] or 'application/octet-stream')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(end-start+1))
        self.send_header('Cache-Control', 'no-cache')
        if status == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if self.command == 'HEAD':
            return
        try:
            with path.open('rb') as f:
                f.seek(start)
                left = end-start+1
                while left:
                    block = f.read(min(left, 256*1024))
                    if not block:
                        break
                    self.wfile.write(block)
                    left -= len(block)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.allowed_host():
            self.send_error(403)
            return
        url = urlparse(self.path)
        if url.path == '/':
            self.file(Path(__file__).with_suffix('.html'))
        elif url.path == '/api/health':
            self.reply(dict(service='ai-school-shot-review', version=1, gpu_generation=False))
        elif url.path == '/api/review':
            with LOCK:
                self.reply(dict(manifest=read(REVIEW/'manifest.json'), state=read(REVIEW/'review_state.json')))
        elif url.path == '/media':
            q = parse_qs(url.query)
            manifest = read(REVIEW/'manifest.json')
            shot = next((s for s in manifest['shots'] if s['id'] == q.get('shot', [''])[0]), None)
            if not shot:
                self.send_error(404)
                return
            if q.get('kind') == ['source']:
                self.file(shot['source_video'])
                return
            v = next((v for v in shot['versions'] if v['id'] == q.get('version', [''])[0]), None)
            if v:
                if q.get('kind') == ['ref']:
                    ref = next((r for r in v['prompt']['refs'] if str(r['index']) == q.get('index', [''])[0]), None)
                    if ref and ref['path']:
                        self.file(ref['path'])
                        return
                elif q.get('kind') == ['output']:
                    self.file(v['video'])
                    return
            self.send_error(404)
        else:
            self.send_error(404)

    def do_POST(self):
        origin = self.headers.get('Origin')
        if (not self.allowed_host() or (origin and origin not in
            {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'})
            or self.headers.get('X-Shot-Review') != '1'):
            self.send_error(403)
            return
        if self.path != '/api/decision':
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 300000:
                raise ValueError('请求大小不合法。')
            body = json.loads(self.rfile.read(length))
            with LOCK:
                state = apply_decision(read(REVIEW/'manifest.json'), read(REVIEW/'review_state.json'), body)
                write(REVIEW/'review_state.json', state)
            self.reply(dict(state=state))
        except (ValueError, KeyError) as exc:
            self.reply(dict(error=str(exc)), 409)

def register(shot_id, video, workflow, label):
    """Explicitly import a new revision; existing media and approvals are immutable."""
    with LOCK:
        manifest = read(REVIEW/'manifest.json')
        shot = next(s for s in manifest['shots'] if s['id'] == shot_id)
        video, workflow = Path(video).resolve(), Path(workflow).resolve()
        if not video.is_relative_to(ROOT) or not workflow.is_relative_to(ROOT):
            raise ValueError('Revision must be a workspace artifact.')
        data = read(workflow)
        refs = []
        if data.get('kind') == 'seedance_api':
            from seedance_school_op import review_metadata
            prompt, kind, seed, note, refs = review_metadata(data, video)
        elif data.get('kind') == 'cpu_text':
            prompt = data['recipe_text']
            kind, seed = 'cpu_text', None
            note = 'CPU 字体排版修订，没有模型提示词；下方展示本次实际制作参数。'
            builder = Path(data['builder']).resolve()
            if not builder.is_relative_to(ROOT) or sha(builder) != data['builder_sha256']:
                raise ValueError('CPU 排版脚本与执行记录不一致。')
        elif data.get('kind') == 'original_copy':
            prompt = ''
            kind, seed = 'original_copy', None
            note = '原片镜头，没有生成；直接使用原片（1024×576 预览，原音轨）。'
        elif data.get('kind') == 'h3_image_to_video_pair':
            parts = data['parts']
            prompt = '\n\n'.join(f"【{name}】\n" + part['104']['inputs']['prompt'] for name, part in parts.items())
            kind, seed = 'executed', ' / '.join(str(part['15']['inputs']['noise_seed']) for part in parts.values())
            note = data['note']
            for i, (name, part) in enumerate(parts.items(), 1):
                image = part['200']['inputs']['image']
                path = ROOT/'ComfyUI/input'/image
                refs.append(dict(index=i, name=f'{name} 首帧 {image}', path=str(path) if path.is_file() else None))
        elif data.get('kind') == 'builtin_imagegen':
            prompt = data['prompt']
            kind, seed = 'builtin_imagegen', None
            note = '自带 imagegen 以原片字卡为参考生成整幅图片，再封装为5帧静止片段并复制原音轨；下方为实际生图提示词。'
            for field in ('reference_image', 'generated_image', 'builder'):
                artifact = Path(data[field]).resolve()
                if not artifact.is_relative_to(ROOT) or sha(artifact) != data[field + '_sha256']:
                    raise ValueError('生图素材与执行记录不一致：' + field)
            refs = [dict(index=1, name='原片字卡参考', path=data['reference_image'])]
        else:
            conditioning = next(n['inputs'] for n in data.values() if n.get('class_type') == 'MiniMaxH3ReferenceToVideo')
            prompt = conditioning['prompt']
            kind = 'executed'
            note = '新修订；从本版本实际工作流读取。'
            seed = next((n['inputs'].get('noise_seed') for n in data.values() if n.get('class_type') == 'RandomNoise'), None)
            for key, link in conditioning.items():
                if key.startswith('ref_images.ref_image_'):
                    name = data[str(link[0])]['inputs'].get('image')
                    path = ROOT/'ComfyUI/input'/name if name else None
                    refs.append(dict(index=int(key.rsplit('_',1)[1])+1, name=name,
                        path=str(path) if path and path.is_file() else None))
        vh, ph = sha(video), hashlib.sha256(prompt.encode()).hexdigest()
        vid = 'revision-' + vh[:12] + '-' + ph[:8]
        if any(v['id'] == vid for v in shot['versions']):
            raise ValueError('该版本已存在。')
        result = subprocess.run([FP, '-v', 'error', '-show_streams', '-of', 'json', str(video)], capture_output=True, text=True, check=True)
        streams = json.loads(result.stdout)['streams']
        v = next(x for x in streams if x['codec_type'] == 'video')
        if (int(v['nb_frames']), v['width'], v['height'], v['r_frame_rate']) != (shot['frames'],1024,576,'24000/1001') or not any(x['codec_type']=='audio' for x in streams):
            raise ValueError('新版本必须保持本镜头原帧数、1024×576、24000/1001并包含音轨。')
        archive = REVIEW/'versions'/shot_id/vid
        archive.mkdir(parents=True, exist_ok=False)
        shutil.copy2(video, archive/'clip.mp4')
        shutil.copy2(workflow, archive/'workflow.json')
        if kind == 'cpu_text':
            shutil.copy2(builder, archive/'builder.py')
        elif kind == 'builtin_imagegen':
            shutil.copy2(data['builder'], archive/'builder.py')
            shutil.copy2(data['reference_image'], archive/'reference_image.png')
            shutil.copy2(data['generated_image'], archive/'generated_image.png')
            refs[0]['path'] = str(archive/'reference_image.png')
        (archive/'prompt.txt').write_text(prompt, encoding='utf-8')
        shot['versions'].append(dict(id=vid,label=label,video=str(archive/'clip.mp4'),
            video_sha256=vh,prompt_sha256=ph,created=stamp(),prompt=dict(text=prompt,
            source=str(workflow),workflow_sha256=sha(workflow),kind=kind,
            note=note,refs=sorted(refs,key=lambda r:r['index']),seed=seed)))
        manifest['updated']=stamp()
        write(REVIEW/'manifest.json',manifest)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--prepare', action='store_true')
    ap.add_argument('--serve', action='store_true')
    ap.add_argument('--port', type=int, default=PORT)
    ap.add_argument('--register')
    ap.add_argument('--video')
    ap.add_argument('--workflow')
    ap.add_argument('--label', default='修订版')
    args = ap.parse_args()
    if args.prepare:
        prepare()
    if args.register:
        register(args.register, args.video, args.workflow, args.label)
    if args.serve:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
        write(REVIEW/'server.json',dict(pid=os.getpid(),port=args.port,started=stamp(),url=f'http://127.0.0.1:{args.port}/'))
        print(f'Shot review: http://127.0.0.1:{args.port}/', flush=True)
        server.serve_forever()

if __name__ == '__main__':
    main()
