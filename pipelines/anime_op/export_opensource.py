"""Build the open-source release folder of the 校园 AI OP (CPU only, rebuildable).

Copies the pipeline code, the 18 AI-girl designs with their prompts, every approved per-shot prompt, three
ComfyUI workflow templates and the plan data. Excludes all video, original frames, reference images of other
artists or the official site, logs and secrets. Absolute workspace paths are rewritten repo-relative, and the
build fails if any local path, username, key or signed URL is left in a text file.
"""
import csv
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROD = ROOT / 'assets/anime_op/school_full_op_v1'
DESIGNS = ROOT / 'assets/character_designs/school_v1/outputs'
OUT = ROOT / 'opensource/ai-school-op'
CODE = ['assemble_full_op_native.py', 'build_comparison_video.py', 'build_imagegen_props.py', 'build_teaser_001_020.py',
        'build_topaz_master.py', 'gpu_guard.py', 'op_graph.py', 'op_pipeline.py', 'overnight_collect.py',
        'overnight_queue.py', 'plan_school_full_op.py', 'prepare_school_full_op.py', 'render_school_full_op.py',
        'review_school_op.py', 'review_school_op.html', 'revise_041_043_i2v.py', 'revise_school_shot_prompt.py',
        'revise_school_shots_024_026.py', 'seedance_school_op.py', 'try_seedvr2_1080p.py', 'upscale_4k_seedvr2_7b.py',
        'upscale_4k_test.py', 'export_opensource.py']
WORKFLOWS = {'h3_ref2va_video_edit.json': PROD / 'shots/076/A/h3_workflow.json',
             'h3_i2v_first_frame.json': PROD / 'revisions/041_043_i2v_v6b/workflow_wide.json',
             'seedvr2_upscale.json': PROD / 'upscale_4k_test/seedvr2_7b/003_0_workflow.json'}
LEAKS = [r'\b[A-Z]:[\\/]+(?!Program Files|Windows[\\/]+Fonts)', r'\blsh\b', r'sk-or-v1-[A-Za-z0-9]', r'@gmail\.com',
         r'X-Amz-(?:Credential|Signature)=', r'Bearer [A-Za-z0-9]{12}']  # system tool/font paths are fine


def rel(text):
    """Workspace-absolute paths -> repo-relative, forward slashes."""
    for root in (str(ROOT), str(ROOT).replace('\\', '/'), str(ROOT).replace('\\', '\\\\')):
        text = text.replace(root + ('\\\\' if '\\\\' in root else '\\' if '\\' in root else '/'), '').replace(root, '.')
    return text


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')


def approved_units():
    manifest = read(PROD / 'review/manifest.json')
    decisions = read(PROD / 'review/review_state.json')['decisions']
    for unit in manifest['shots']:
        approved = [(decisions[f"{unit['id']}/{v['id']}"]['updated'], v) for v in unit['versions']
                    if decisions.get(f"{unit['id']}/{v['id']}", {}).get('status') == 'approved']
        yield unit, (max(approved, key=lambda x: x[0])[1] if approved else
                     next(v for v in unit['versions'] if v['prompt'].get('kind') == 'original_copy'))


def main():
    for item in ('pipelines', 'data', 'prompts', 'workflows', 'cast/designs'):
        shutil.rmtree(OUT / item, ignore_errors=True)
    for name in CODE:
        write(OUT / 'pipelines/anime_op' / name, rel((ROOT / 'pipelines/anime_op' / name).read_text(encoding='utf-8-sig')))
    plan = read(PROD / 'full_plan.json')
    plan['source'] = '<your 1080p NCOP file of Make Heroine ga Oosugiru!>'
    write(OUT / 'data/full_plan.json', rel(json.dumps(plan, ensure_ascii=False, indent=1)))
    write(OUT / 'data/credit_decisions.json', rel((PROD / 'runtime/credit_decisions.json').read_text(encoding='utf-8-sig')))
    rows = []
    for unit, v in approved_units():
        kind, text = v['prompt'].get('kind'), v['prompt'].get('text', '')
        refs = [r.get('name') for r in v['prompt'].get('refs') or []]
        if text:
            write(OUT / 'prompts' / f"{unit['id']}.txt", rel(text).rstrip() + '\n')
        rows.append(dict(unit=unit['id'], frames=unit['frames'], kind=kind, version=v.get('label'),
                         prompt=f"{unit['id']}.txt" if text else '', seed=v['prompt'].get('seed'),
                         reference_images=' | '.join(str(r) for r in refs)))
    with (OUT / 'prompts/index.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for name, src in WORKFLOWS.items():
        write(OUT / 'workflows' / name, rel(src.read_text(encoding='utf-8-sig')))
    (OUT / 'cast/designs').mkdir(parents=True, exist_ok=True)
    for key in plan['cast']:
        stem = 'gpt_original_school_front_v1' if key == 'gpt' else f'{key}_school_front_v1'
        shutil.copy2(DESIGNS / f'{stem}.png', OUT / 'cast/designs' / f'{key}.png')
        write(OUT / 'cast/designs' / f'{key}.prompt.txt', rel((DESIGNS / f'{stem}.prompt.txt').read_text(encoding='utf-8-sig')))
    leaks = []
    for path in OUT.rglob('*'):
        if path.name != Path(__file__).name and path.is_file() and path.suffix in ('.py', '.json', '.txt', '.md', '.csv', '.html', '.cmd', '.ps1'):
            text = path.read_text(encoding='utf-8-sig')
            leaks += [f'{path.relative_to(OUT)}: {m.group(0)!r} …{text[max(0, m.start() - 40):m.end() + 20]!r}'
                      for pattern in LEAKS for m in re.finditer(pattern, text)]
    if leaks:
        raise SystemExit('LEAKS\n' + '\n'.join(leaks[:60]))
    files = [p for p in OUT.rglob('*') if p.is_file() and '.git' not in p.parts]
    print('OK', len(files), 'files', round(sum(p.stat().st_size for p in files) / 2**20, 1), 'MiB,',
          len(rows), 'units,', sum(1 for r in rows if r['prompt']), 'prompts', flush=True)


if __name__ == '__main__':
    main()
