"""Optional CPU/cloud lane. prepare is offline; submit is explicitly gated.

Does not import a GPU runner or touch its queue, prompt, guard or completion files.
OpenRouter's video-reference generation is NOT a dedicated frame-locked edit API.
"""
import argparse
import base64
import contextlib
import datetime as dt
from decimal import Decimal, ROUND_UP
from fractions import Fraction
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
PROD = ROOT / 'assets/anime_op/school_full_op_v1'
DEFAULT_CONFIG = Path(__file__).with_name('seedance_trial_001_020.json')
DEFAULT_OUT = PROD / 'seedance_trial_001_020'
FF = r'C:\Program Files\ffmpeg\bin\ffmpeg.exe'
FP = r'C:\Program Files\ffmpeg\bin\ffprobe.exe'
FPS = Fraction(24000, 1001)
BASE = 'https://openrouter.ai/api/v1'
MODEL = 'bytedance/seedance-2.5'
SIZES = {'480p': (854, 480), '720p': (1280, 720)}
TERMINAL = {'completed', 'failed', 'cancelled', 'expired'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    for n in range(31):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if n == 30:
                raise
            time.sleep(.1)


def run(args):
    result = subprocess.run([str(x) for x in args], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:])
    return result.stdout


def probe(path):
    return json.loads(run([FP, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path]))


def money(value):
    return str(Decimal(str(value)).quantize(Decimal('.000001'), rounding=ROUND_UP))


def plan_fingerprint(plan):
    frozen = dict(plan)
    frozen.pop('paid_enabled', None)
    return hashlib.sha256(json.dumps(frozen, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def estimate(seconds, resolution, catalog):
    """Provider formula estimate, NOT a quote. Input video also consumes tokens.

    Reserve 2x the formula estimate because the upstream applies minimum token
    charges not exposed by the model catalog. Server/key limits remain necessary
    for a hard account spending cap; do not represent this margin as a guarantee.
    """
    width, height = SIZES[resolution]
    rate = Decimal(catalog['pricing_skus']['video_tokens_with_video_input'])
    if not rate.is_finite() or rate <= 0:
        raise ValueError('Invalid video-input token price')
    tokens = Decimal(2 * seconds * width * height * 24) / 1024
    cost = tokens * rate
    return dict(formula_tokens=str(tokens), formula_estimate_usd=money(cost),
                reserve_usd=money(cost * 2), rate_per_token=str(rate),
                caveat='Estimate only; provider minimum-token billing and final usage can differ. Reserve is 2x estimate, not a guaranteed quote.')


def source_version(entry, state, pinned):
    versions = entry['versions']
    if entry['id'] in pinned:
        return next(v for v in versions if v['id'] == pinned[entry['id']])
    accepted = [v for v in versions if state['decisions'].get(entry['id']+'/'+v['id'], {}).get('status') == 'approved']
    return (accepted or versions or [None])[-1]


def build_prompt(group, shots, plan, edits):
    lines = ['Edit the supplied video, preserving its original shot sequence, timing, framing, animation and graphic style. '
             'Change only the people and existing text specified below. The reference images define character appearance and school uniforms; '
             'the video defines performance, screen size and position. Do not insert reference sheets or new shots.']
    for i, name in enumerate(group['cast'], 1):
        c = plan['cast'][name]
        lines.append(f'Image {i}: {name.upper()}, replacing {c["original"]} ({c["source_description"]}) only where visible.')
    start = shots[0]['start']
    for shot in shots:
        a, b = float(Fraction(shot['start']-start, 1)/FPS), float(Fraction(shot['end']-start, 1)/FPS)
        lines.append(f'{a:.3f}–{b:.3f}s, shot {shot["id"]}: {edits[shot["id"]]}')
    lines.append('Replace text within the original lettering design and animation. Preserve all unlisted elements. '
                 'The brief hold at the very end is padding and must not introduce a new action. No generated soundtrack is needed.')
    return '\n\n'.join(lines) + '\n'


def prepare(config_path, out, catalog_path):
    """Freeze provenance + original-speed source intervals locally. No HTTP."""
    config, catalog, plan = read(config_path), read(catalog_path), read(PROD/'full_plan.json')
    if config['model'] != MODEL or config['provider'] != 'openrouter' or catalog['id'] != MODEL:
        raise ValueError('Only the verified OpenRouter Seedance 2.5 adapter is supported')
    if config['resolution'] not in catalog['supported_resolutions']:
        raise ValueError('Unsupported resolution')
    budget = Decimal(config['budget_usd'])
    if not budget.is_finite() or not 0 < budget <= 10:
        raise ValueError('This trial is capped at $10')
    out = Path(out).resolve()
    if (out/'plan.json').exists():
        raise ValueError('Prepared runs are immutable; use a new --out to change prompts or settings')
    out.mkdir(parents=True, exist_ok=True)
    manifest, state = read(PROD/'review/manifest.json'), read(PROD/'review/review_state.json')
    entries = {s['id']: s for s in manifest['shots']}
    ids = [s['id'] for s in plan['shots']]
    shutil.copy2(catalog_path, out/'catalog.json')
    shutil.copy2(config_path, out/'config_snapshot.json')
    write(out/'review_state_snapshot.json', state)
    write(out/'manifest_snapshot.json', manifest)
    source = Path(plan['source'])
    if sha(source) != plan['source_sha256']:
        raise ValueError('Original source hash changed')
    result = dict(schema=1, created=stamp(), provider='openrouter', model=MODEL,
                  mode='video_reference_generation', paid_enabled=False,
                  budget_usd=str(budget), resolution=config['resolution'], seed=config['seed'],
                  fps=str(FPS), source=str(source), source_sha256=plan['source_sha256'],
                  catalog_sha256=sha(out/'catalog.json'), jobs=[])
    used = set()
    for group in config['groups']:
        if not re.fullmatch(r'[0-9_-]+', group['id']):
            raise ValueError('Invalid group id')
        a, b = ids.index(group['first']), ids.index(group['last'])
        shots = plan['shots'][a:b+1]
        if not shots or any(s['id'] in used for s in shots):
            raise ValueError('Overlapping/empty groups')
        used.update(s['id'] for s in shots)
        for left, right in zip(shots, shots[1:]):
            if left['end'] != right['start']:
                raise ValueError('Source interval has a gap')
        start, end = shots[0]['start'], shots[-1]['end']
        real_seconds = Fraction(end-start, 1)/FPS
        duration = math.ceil(real_seconds)
        if real_seconds < 4 or duration not in catalog['supported_durations']:
            raise ValueError('Combine adjacent original shots into a 4–30 second interval; do not slow them down')
        folder = out/group['id']
        folder.mkdir(exist_ok=True)
        prompt = build_prompt(group, shots, plan, config['edits'])
        (folder/'prompt.txt').write_text(prompt, encoding='utf-8')
        ref_video = folder/'source.mp4'
        # FPS resampling leaves wall-clock movement unchanged. Pad only the end
        # to the integer API duration, with at most one second of final-frame hold.
        vf = f'trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS,scale=1024:576,fps=24,tpad=stop_mode=clone:stop_duration=1'
        run([FF, '-v','error','-y','-threads','2','-i',source,'-vf',vf,'-frames:v',duration*24,
             '-an','-c:v','libx264','-crf','16','-preset','fast','-threads','2','-filter_threads','1','-pix_fmt','yuv420p','-movflags','+faststart',ref_video])
        assets = [dict(id=group['id']+'/source', kind='video', path=str(ref_video), sha256=sha(ref_video))]
        for i, name in enumerate(group['cast'], 1):
            original = Path(plan['cast'][name]['reference'])
            target = folder/(f'image_{i}_{name}'+original.suffix)
            shutil.copy2(original, target)
            assets.append(dict(id=group['id']+'/'+name, kind='image', index=i, name=name, path=str(target), sha256=sha(target)))
        members = []
        for s in shots:
            entry = entries[s['id']]
            chosen = source_version(entry, state, config.get('pinned_versions', {}))
            queued = entry.get('queued')
            provenance = dict(selected_version=chosen, queued=queued,
                              note='Comparison provenance only. Actual Seedance prompt is the group prompt.txt; H3 text is not silently sent.')
            write(folder/(s['id']+'_local_prompt.json'), provenance)
            audio_source = Path(entry['source_video'])
            # Snapshot source clip including its existing original AAC packets.
            local_source = folder/(s['id']+'_original.mp4')
            shutil.copy2(audio_source, local_source)
            members.append(dict(id=s['id'], frames=s['frames'], mode=s['mode'], start=s['start'], end=s['end'],
                                offset_seconds=float(Fraction(s['start']-start, 1)/FPS),
                                source=str(local_source), source_sha256=sha(local_source)))
        cost = estimate(duration, config['resolution'], catalog)
        job = dict(id=group['id'], start_frame=start, end_frame=end, source_seconds=float(real_seconds),
                   duration=duration, padding_seconds=duration-float(real_seconds), assets=assets, shots=members,
                   prompt_path=str(folder/'prompt.txt'), prompt_sha256=sha(folder/'prompt.txt'), pricing=cost)
        result['jobs'].append(job)
        write(folder/'request_template.json', request_body(result, job, None))
    result['formula_estimate_usd'] = money(sum(Decimal(j['pricing']['formula_estimate_usd']) for j in result['jobs']))
    result['reserved_total_usd'] = money(sum(Decimal(j['pricing']['reserve_usd']) for j in result['jobs']))
    write(out/'plan.json', result)
    write(out/'asset_urls.example.json', {asset['id']: {'url':'', 'sha256':asset['sha256']} for j in result['jobs'] for asset in j['assets']})
    write(out/'ledger.json', dict(schema=1, budget_usd=str(budget), plan_sha256=plan_fingerprint(result), jobs={}))
    preview(out, result)
    return result


def verify_assets(job):
    if sha(job['prompt_path']) != job['prompt_sha256']:
        raise ValueError('Prompt changed after preparation; prepare a new run')
    for asset in job['assets']:
        if sha(asset['path']) != asset['sha256']:
            raise ValueError('Prepared asset changed: '+asset['id'])


def request_body(plan, job, urls, inline=False):
    if not isinstance(plan['seed'],int) or not 0 <= plan['seed'] <= 2147483647:
        raise ValueError('Seedance r2v seed must fit the verified signed 32-bit range')
    refs = []
    for asset in job['assets']:
        if inline:
            path = Path(asset['path'])
            mime = {'video': 'video/mp4', 'image': 'image/png'}[asset['kind']]
            if asset['kind']=='image' and path.suffix.lower() in ('.jpg','.jpeg'):
                mime = 'image/jpeg'
            if path.stat().st_size > 25*1024*1024:
                raise ValueError('Reference too large for the inline trial transport')
            url = 'data:'+mime+';base64,'+base64.b64encode(path.read_bytes()).decode('ascii')
        elif urls is None:
            url = 'ASSET_URL:'+asset['id']
        else:
            binding = urls[asset['id']]
            if binding['sha256'] != asset['sha256']:
                raise ValueError('Hosted asset hash binding does not match preparation')
            url = binding['url']
            parts = urllib.parse.urlsplit(url)
            if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
                raise ValueError('Provide HTTPS URLs for exactly the prepared assets')
        key = asset['kind']+'_url'
        refs.append(dict(type=key, **{key: dict(url=url)}))
    result = dict(model=plan['model'], prompt=Path(job['prompt_path']).read_text(encoding='utf-8'),
                duration=job['duration'], resolution=plan['resolution'], aspect_ratio='16:9',
                input_references=refs, generate_audio=False, seed=plan['seed'])
    if plan.get('mode')=='video_editing':
        # OpenRouter rejects explicit native -1/adaptive values in its schema.
        # Omit these OPTIONAL controls so the upstream may use input-following defaults.
        result.pop('duration')
        result.pop('aspect_ratio')
    return result


@contextlib.contextmanager
def ledger_lock(out):
    """OS lock released on crash; never remove a stale lock file to recover."""
    import msvcrt
    with (Path(out)/'api.lock').open('a+b') as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError('Another API command owns this run') from exc
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class APIHTTPError(RuntimeError):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail
        super().__init__(f'OpenRouter HTTP {status}: {detail}')


class Client:
    def __init__(self, key=None):
        self.key = key or os.environ.get('OPENROUTER_API_KEY')
        if not self.key:
            raise ValueError('Set OPENROUTER_API_KEY in the environment; never store it in the plan')
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, path, body=None):
        if not (path.startswith('/videos') or (method=='GET' and path=='/key')) or '..' in path:
            raise ValueError('Only the fixed OpenRouter video endpoint is allowed')
        req = urllib.request.Request(BASE+path, method=method,
            headers={'Authorization':'Bearer '+self.key, 'Content-Type':'application/json'},
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None)
        try:
            with self.opener.open(req, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # Redact echoed asset data, signed URLs and credentials from diagnostics.
            detail=exc.read(65536).decode('utf-8',errors='replace')
            detail=detail.replace(self.key,'[REDACTED]')
            detail=re.sub(r'data:[^\s"<>]+','[ASSET DATA]',detail)
            detail=re.sub(r'https?://[^\s"<>]+','[URL]',detail)
            detail=re.sub(r'sk-or-v1-[A-Za-z0-9_-]+','[REDACTED]',detail)
            raise APIHTTPError(exc.code,detail[:3000]) from None

    def check_budget(self, required):
        data = self.request('GET','/key')['data']
        limit, remaining = data.get('limit'), data.get('limit_remaining')
        if limit is None or remaining is None or not 0 < Decimal(str(limit)) <= 10:
            raise ValueError('This trial requires a dedicated API key limited to $10 or less')
        if Decimal(str(remaining)) < Decimal(required):
            raise ValueError('Key remaining budget is below the job reservation')
        return {name:data.get(name) for name in ('limit','limit_remaining','usage','limit_reset')}

    def download(self, job_id, target):
        # Do not forward Bearer auth to a provider CDN or a response-supplied URL.
        url = BASE+'/videos/'+safe_id(job_id)+'/content?index=0'
        req = urllib.request.Request(url, headers={'Authorization':'Bearer '+self.key})
        tmp = Path(target).with_suffix('.part')
        try:
            with self.opener.open(req, timeout=180) as response, tmp.open('wb') as dest:
                shutil.copyfileobj(response, dest)
            os.replace(tmp, target)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'Download HTTP {exc.code}; no credentials forwarded to redirect hosts') from None


def safe_id(value):
    if not re.fullmatch('[A-Za-z0-9_-]+', value):
        raise ValueError('Invalid API job id')
    return value


def commitment(ledger):
    return sum(max(Decimal(v['reserve_usd']), Decimal(v.get('actual_cost_usd') or '0')) for v in ledger['jobs'].values())


def reserve(ledger, job):
    if job['id'] in ledger['jobs']:
        raise ValueError('Already attempted. Poll the recorded job; never automatically resubmit an uncertain POST')
    if any(v['state'] not in TERMINAL for v in ledger['jobs'].values()):
        raise ValueError('An existing job is pending or uncertain; resolve it first')
    amount = Decimal(job['pricing']['reserve_usd'])
    budget = Decimal(ledger['budget_usd'])
    if not budget.is_finite() or not 0 < budget <= 10 or amount <= 0 or commitment(ledger)+amount > budget:
        raise ValueError('The $10 trial reservation budget would be exceeded')
    ledger['jobs'][job['id']] = dict(state='submitting', reserve_usd=str(amount), started=stamp())


def review_metadata(data, video):
    """Validate true API provenance before the existing reviewer imports it."""
    if data['provider'] != 'openrouter' or data['model'] != MODEL:
        raise ValueError('Unexpected API provider/model')
    safe_id(data['api_id'])
    if sha(video) != data['validation']['sha256']:
        raise ValueError('Candidate changed after technical validation')
    raw = Path(data['raw']).resolve()
    if not raw.is_relative_to(ROOT) or sha(raw) != data['raw_sha256']:
        raise ValueError('Provider raw output is missing or changed')
    refs = []
    for asset in data['assets']:
        path = Path(asset['path']).resolve()
        if not path.is_relative_to(ROOT) or sha(path) != asset['sha256']:
            raise ValueError('API reference changed')
        if asset['kind']=='image':
            refs.append(dict(index=asset['index'],name=asset['name'],path=str(path)))
    note = (f"Seedance 2.5 / OpenRouter，真实任务 {data['api_id']}；整幅视频参考生成，原始尺寸 "
            f"{data['generated_size'][0]}×{data['generated_size'][1]}。按原切点拆分、恢复原AAC；内容待用户验收。下方显示整个连续组的实际提示词。")
    return data['prompt'], 'seedance_api', data['seed'], note, refs


def import_review(out, job_id):
    # The existing H3 worker uses an in-process manifest lock. Wait for it to exit
    # before external registration; no restart or patching of its running process.
    query = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\\.exe$' } | "
             "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
    raw = run(['powershell','-NoProfile','-NonInteractive','-Command',query])
    processes = json.loads(raw or '[]')
    if isinstance(processes, dict):
        processes = [processes]
    active = ('overnight_queue.py','revise_school_shot','render_school_full_op.py','build_teaser_001_020.py')
    if any(any(name in (p.get('CommandLine') or '') for name in active) for p in processes):
        raise ValueError('Local production worker still active; candidates remain staged, import after it exits')
    import review_school_op as review
    with ledger_lock(out):
        pending = read(out/job_id/'pending_review_imports.json')
        for item in pending:
            # Idempotent import: a rerun after partial success never duplicates.
            workflow = read(item['workflow'])
            vid = 'revision-'+sha(item['video'])[:12]+'-'+hashlib.sha256(workflow['prompt'].encode()).hexdigest()[:8]
            manifest = read(review.REVIEW/'manifest.json')
            entry = next(s for s in manifest['shots'] if s['id']==item['shot'])
            if any(v['id']==vid for v in entry['versions']):
                continue
            review.register(item['shot'],item['video'],item['workflow'],'Seedance 2.5 · API 对照')
        write(out/job_id/'review_imported.json',dict(imported=stamp(),shots=[i['shot'] for i in pending]))
    return dict(imported=len(pending),user_approved=False)


def submit(out, job_id, urls_path, allow_paid=False, client=None, inline=False, retry_rejected=False):
    with ledger_lock(out):
        plan = read(out/'plan.json')
        if not allow_paid or plan.get('paid_enabled') is not True:
            raise ValueError('Paid submission disabled. Future authorization requires paid_enabled=true AND --allow-paid')
        ledger = read(out/'ledger.json')
        if ledger.get('plan_sha256') != plan_fingerprint(plan):
            raise ValueError('Prepared plan changed; use a new run instead of changing its model, durations or prices')
        if plan['model'] != MODEL or plan['provider'] != 'openrouter':
            raise ValueError('Unsupported model/provider')
        job = next(j for j in plan['jobs'] if j['id'] == job_id)
        verify_assets(job)
        # Public GET only. Refuse changed rates/capabilities instead of spending
        # against a stale quote. Does not upload any project assets.
        live = refresh_catalog()
        if live['pricing_skus'] != read(out/'catalog.json')['pricing_skus']:
            raise ValueError('Public price changed; prepare/review a new run')
        if job['duration'] not in live['supported_durations'] or plan['resolution'] not in live['supported_resolutions']:
            raise ValueError('Requested capability is no longer available')
        cost = estimate(job['duration'], plan['resolution'], live)
        if job['pricing'] != cost:
            raise ValueError('Pricing record changed; prepare a new run')
        body = request_body(plan, job, None if inline else read(urls_path), inline=inline)
        api = client or Client()
        if Decimal(ledger['budget_usd']) != Decimal(plan['budget_usd']):
            raise ValueError('Budget records disagree')
        previous=ledger['jobs'].get(job_id)
        if previous and retry_rejected:
            if previous['state']!='rejected' or previous.get('http_status') not in (400,401,402,403,413,422,429) or previous.get('api_id'):
                raise ValueError('Only an explicit pre-acceptance HTTP rejection can be retried')
            ledger.setdefault('rejected_attempts',[]).append(dict(job_id=job_id,**previous))
            del ledger['jobs'][job_id]
        reserve(ledger, job)
        entry = ledger['jobs'][job_id]
        if isinstance(api, Client):
            entry['key_budget_before'] = api.check_budget(job['pricing']['reserve_usd'])
        entry['transport'] = 'inline_data_urls' if inline else 'https_urls'
        # Store redacted URL bindings, exact prompt and asset hashes before POST.
        entry['request_sha256'] = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        write(out/'ledger.json', ledger)
        try:
            response = api.request('POST', '/videos', body)
            entry['api_id'] = safe_id(response['id'])
            entry['state'] = response['status']
            entry['submitted'] = stamp()
            write(out/'ledger.json', ledger)
        except Exception as exc:
            if isinstance(exc,APIHTTPError) and exc.status in (400,401,402,403,413,422,429):
                entry.update(state='rejected',http_status=exc.status,error=exc.detail)
            else:
                entry['state'] = 'submission_unknown'
                if isinstance(exc, APIHTTPError):
                    entry.update(http_status=exc.status, error=exc.detail)
            write(out/'ledger.json', ledger)
            raise
        return entry


def poll(out, job_id, client=None):
    with ledger_lock(out):
        ledger = read(out/'ledger.json')
        entry = ledger['jobs'][job_id]
        if not entry.get('api_id'):
            raise ValueError('No known task id. Reconcile the provider dashboard; do not resubmit')
        response = (client or Client()).request('GET','/videos/'+safe_id(entry['api_id']))
        if response['id'] != entry['api_id']:
            raise ValueError('Provider returned the wrong task')
        state = response['status']
        if state not in TERMINAL | {'pending','in_progress'}:
            raise ValueError('Unknown task state; reservation retained')
        entry['state'], entry['checked'] = state, stamp()
        cost = (response.get('usage') or {}).get('cost')
        if cost is not None:
            cost = Decimal(str(cost))
            if not cost.is_finite() or cost < 0:
                raise ValueError('Invalid provider cost')
            entry['actual_cost_usd'] = str(cost)
        # Keep reservations for failed/unknown-cost jobs; no speculative refunds.
        write(out/'ledger.json', ledger)
        return entry


def audio_hash(path):
    text = run([FP,'-v','error','-select_streams','a:0','-show_packets','-show_entries',
                'packet=data_hash','-show_data_hash','sha256','-of','json',path])
    packets = json.loads(text)['packets']
    return [p['data_hash'] for p in packets]


def validate_clip(path, frames, source):
    data = probe(path)
    v = next(s for s in data['streams'] if s['codec_type']=='video')
    if (int(v['nb_frames']),v['width'],v['height'],v['r_frame_rate']) != (frames,1024,576,str(FPS)):
        raise ValueError('Clip timeline/format mismatch')
    packets = audio_hash(source)
    if not packets or packets != audio_hash(path):
        raise ValueError('Original AAC packet hashes differ')
    run([FF,'-v','error','-xerror','-threads','2','-i',path,'-f','null','-'])
    return dict(frames=frames, size='1024x576', fps=str(FPS), decode=True, original_aac_packets=True, sha256=sha(path))


def collect(out, job_id, download=False, client=None):
    with ledger_lock(out):
        plan, ledger = read(out/'plan.json'), read(out/'ledger.json')
        job = next(j for j in plan['jobs'] if j['id']==job_id)
        entry = ledger['jobs'][job_id]
        if entry['state'] != 'completed' or not entry.get('api_id'):
            raise ValueError('Only a real completed provider job may be collected')
        folder = out/job_id
        raw = folder/'provider_raw.mp4'
        if download and not raw.exists():
            (client or Client()).download(entry['api_id'], raw)
        if not raw.exists():
            raise ValueError('Missing provider_raw.mp4. Use --download to retrieve the completed task')
        verify_assets(job)
        data = probe(raw)
        v = next(s for s in data['streams'] if s['codec_type']=='video')
        duration = float(v.get('duration',data['format']['duration']))
        if abs(duration-job['duration']) > .1:
            raise ValueError('API duration differs; inspect timing instead of silently stretching/truncating it')
        if (v['width'],v['height']) != SIZES[plan['resolution']]:
            raise ValueError('Unexpected provider resolution; inspect before normalizing')
        result = []
        for s in job['shots']:
            if s['mode']=='copy':
                continue
            if sha(s['source']) != s['source_sha256']:
                raise ValueError('Source audio snapshot changed')
            video = folder/(s['id']+'_review.mp4')
            vf = f"trim=start={s['offset_seconds']:.9f},setpts=PTS-STARTPTS,fps={FPS},scale=1024:576,setsar=1"
            run([FF,'-v','error','-y','-threads','2','-i',raw,'-i',s['source'],
                 '-map','0:v:0','-map','1:a:0','-vf',vf,'-frames:v',s['frames'],
                 '-c:v','libx264','-crf','16','-threads','2','-filter_threads','1','-pix_fmt','yuv420p',
                 '-c:a','copy','-movflags','+faststart',video])
            validation = validate_clip(video,s['frames'],s['source'])
            workflow = folder/(s['id']+'_workflow.json')
            write(workflow, dict(kind='seedance_api', model=plan['model'], provider=plan['provider'],
                  prompt=Path(job['prompt_path']).read_text(encoding='utf-8'), seed=plan['seed'],
                  api_id=entry['api_id'], raw=str(raw), raw_sha256=sha(raw), assets=job['assets'],
                  group=job_id, shot=s, generated_size=[v['width'],v['height']],
                  actual_cost_usd=entry.get('actual_cost_usd'), request_sha256=entry['request_sha256'],
                  validation=validation, note='Whole-frame video-reference generation; split at original cut times, original AAC restored. Content/cut accuracy awaits user review.'))
            result.append(dict(shot=s['id'], video=str(video), workflow=str(workflow), validation=validation))
        write(folder/'pending_review_imports.json', result)
        preview(out, plan)
        return result


def preview(out, plan):
    esc = html.escape
    ledger=read(out/'ledger.json')
    activity='；'.join(f"{key}: {value['state']}"+(f"，实际费用 ${value['actual_cost_usd']}" if value.get('actual_cost_usd') is not None else '') for key,value in ledger['jobs'].items()) or '尚无云端任务'
    content = ['<!doctype html><meta charset="utf-8"><title>Seedance 2.5 · 离线试验</title>',
               '<style>body{background:#111820;color:#ddd;font:16px system-ui;max-width:1100px;margin:30px auto}pre{white-space:pre-wrap}video{width:100%;max-height:420px}a{color:#9cf}section{padding:20px;background:#1c2630;margin:18px 0}details{margin:16px 0}</style>',
               '<h1>Seedance 2.5 · 001–020 试验</h1>',
               f'<p>{"本次试验已获付费授权" if plan["paid_enabled"] else "默认未启用付费"}。预算 ${esc(plan["budget_usd"])}；公式估算 ${esc(plan["formula_estimate_usd"])}，本地预留 ${esc(plan["reserved_total_usd"])}。估算不是最终报价；视频输入最低计费可能影响实际费用。</p>',
               '<p>任务状态：'+esc(activity)+'</p>',
               '<p>源片原速；仅尾部补短暂停帧至整数秒。OpenRouter 视频参考生成不等于严格视频编辑。当前本地 H3 任务独立运行。</p>']
    for job in plan['jobs']:
        folder = out/job['id']
        content += [f'<section><h2>{esc(job["id"])} · {job["duration"]}秒 · {esc(plan["resolution"])}</h2>',
                    f'<p>原片 {job["source_seconds"]:.3f}秒，末尾补 {job["padding_seconds"]:.3f}秒。预留 ${job["pricing"]["reserve_usd"]}。</p>',
                    f'<video controls preload="metadata" src="{job["id"]}/source.mp4"></video>',
                    '<details open><summary>将实际发送的 API 提示词</summary><pre>'+esc(Path(job['prompt_path']).read_text(encoding='utf-8'))+'</pre></details>']
        if (folder/'provider_raw.mp4').exists():
            content.append(f'<h3>Seedance原始返回整组视频</h3><video controls preload="metadata" src="{job["id"]}/provider_raw.mp4"></video>')
        for s in job['shots']:
            provenance = read(folder/(s['id']+'_local_prompt.json'))
            version = provenance['selected_version'] or {}
            text = version.get('prompt',{}).get('text','原片/CPU制作，无 H3 提示词')
            content.append(f'<details><summary>{s["id"]} 本地对照提示词（{esc(version.get("label","原片"))}）</summary><pre>{esc(text)}</pre></details>')
        pending = folder/'pending_review_imports.json'
        if pending.exists():
            for item in read(pending):
                content.append(f'<p>Seedance 候选 {item["shot"]}，未验收</p><video controls preload="metadata" src="{job["id"]}/{Path(item["video"]).name}"></video>')
        content.append('</section>')
    (out/'index.html').write_text('\n'.join(content),encoding='utf-8')


def refresh_catalog():
    with urllib.request.urlopen(BASE+'/videos/models',timeout=30) as response:
        data = json.load(response)
    return next(m for m in data['data'] if m['id']==MODEL)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command', choices=['prepare','estimate','catalog','submit','status','collect','import-review'])
    ap.add_argument('--out',type=Path,default=DEFAULT_OUT)
    ap.add_argument('--config',type=Path,default=DEFAULT_CONFIG)
    ap.add_argument('--catalog',type=Path,default=Path(__file__).with_name('seedance_catalog_20260924.json'))
    ap.add_argument('--job')
    ap.add_argument('--asset-urls',type=Path)
    ap.add_argument('--allow-paid',action='store_true')
    ap.add_argument('--download',action='store_true')
    ap.add_argument('--inline-assets',action='store_true',help='Send prepared assets directly as data URLs; no third-party storage')
    ap.add_argument('--retry-rejected',action='store_true',help='Explicit recovery only after a known pre-acceptance HTTP rejection')
    ap.add_argument('--secret-file',type=Path,help='Read one OpenRouter key from a local file without logging it')
    args=ap.parse_args()
    if args.secret_file:
        keys=re.findall(r'sk-or-v1-[A-Za-z0-9_-]+',args.secret_file.read_text(encoding='utf-8-sig'))
        if len(keys)!=1:
            raise ValueError('Secret file must contain exactly one OpenRouter key')
        os.environ['OPENROUTER_API_KEY']=keys[0]
    out=args.out.resolve()
    if args.command=='catalog':
        data=refresh_catalog(); write(args.catalog,data); print('Saved public catalog; no paid request.')
    elif args.command=='prepare':
        result=prepare(args.config,out,args.catalog)
        print(json.dumps({k:result[k] for k in ('formula_estimate_usd','reserved_total_usd','paid_enabled')},ensure_ascii=False))
    elif args.command=='estimate':
        plan=read(out/'plan.json')
        print(json.dumps({k:plan[k] for k in ('formula_estimate_usd','reserved_total_usd','budget_usd','paid_enabled')},ensure_ascii=False))
    else:
        if not args.job:
            ap.error('--job is required')
        if args.command=='submit':
            result=submit(out,args.job,args.asset_urls,args.allow_paid,inline=args.inline_assets,retry_rejected=args.retry_rejected)
        elif args.command=='status':
            result=poll(out,args.job)
        elif args.command=='import-review':
            result=import_review(out,args.job)
        else:
            result=collect(out,args.job,args.download)
        print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
