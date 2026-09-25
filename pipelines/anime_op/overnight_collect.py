"""Collect the per-shot findings into a summary table and a draft overnight queue (CPU only)."""
import json
from pathlib import Path

PROD = Path(__file__).resolve().parents[2] / 'assets/anime_op/school_full_op_v1'
REV = PROD / 'revisions'
FIRST = ['019', '021-022']  # already flagged by the user, run first


def findings():
    found = {}
    for path in sorted(REV.glob('*/findings.json')):
        if not path.parent.name.endswith(('_prompt_v2', '_prompt_v3', '_prompt_v4', '_prompt_v5', '_check', '_merged_v2', '_merged_v3')):
            continue
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        data['_dir'] = path.parent.name
        data['_has_prompt'] = (path.parent / 'prompt_requested.txt').exists()
        found[str(data['id'])] = data
    return found


def members(unit):
    """All planned shots from the unit's first to its last id, e.g. 049-051 -> 049, 050, 051."""
    ids = [s['id'] for s in json.loads((PROD / 'full_plan.json').read_text(encoding='utf-8-sig'))['shots']]
    first, last = unit.split('-')
    return ids[ids.index(first):ids.index(last) + 1]


def main():
    found = findings()
    lines = ['| 镜头 | 重跑 | A版问题 | 需要你决定 |', '| --- | --- | --- | --- |']
    for sid in sorted(found):
        f = found[sid]
        problems = '；'.join(f.get('a_version_problems') or []) or '—'
        questions = '；'.join(f.get('questions_for_user') or []) or '—'
        lines.append(f"| {sid} | {'是' if f.get('rerun_recommended') else '否'} | {problems} | {questions} |")
    (PROD / 'runtime/overnight_findings_summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    jobs = []
    order = FIRST + sorted(s for s in found if s not in FIRST)
    for sid in order:
        f = found.get(sid)
        if not f or not f.get('rerun_recommended') or not f['_has_prompt']:
            continue
        if f['_dir'].endswith(('_merged_v2', '_merged_v3')):
            jobs.append(dict(id=sid, kind='merged', dir=f['_dir'], members=members(sid),
                layout='continuous' if f.get('merge_layout') == 'continuous' else 'cut',
                cast=f.get('cast_order') or [], label=f.get('label') or '修订2·合并生成·官方格式',
                description=f.get('source_summary', '')))
        else:
            kind = 'single' if (PROD / 'shots' / sid / 'A/h3_workflow.json').exists() else 'new'
            job = dict(id=sid, kind=kind, dir=f['_dir'], cast=f.get('cast_order') or [],
                label=f.get('label') or ('修订2·官方格式' if kind == 'single' else '首版·官方格式'))
            if f.get('source_cuts'):
                job['cuts'] = f['source_cuts']
            jobs.append(job)
    (PROD / 'runtime/overnight_queue.json').write_text(json.dumps(dict(jobs=jobs), ensure_ascii=False, indent=1),
        encoding='utf-8')
    publish(jobs)
    print(f'{len(found)} findings, {len(jobs)} queued:', ' '.join(j['id'] for j in jobs))


def publish(jobs):
    """Show each queued prompt on the review page until its generation is registered."""
    import review_school_op as review
    with review.LOCK:
        manifest = review.read(review.REVIEW / 'manifest.json')
        by_id = {j['id']: j for j in jobs}
        for entry in manifest['shots']:
            job = by_id.get(entry['id'])
            if job:
                prompt = (REV / job['dir'] / 'prompt_requested.txt').read_text(encoding='utf-8-sig')
                done = entry.get('queued', {}).get('state') == 'done' and entry['queued'].get('prompt') == prompt
                entry['queued'] = dict(prompt=prompt, cast=job['cast'], dir=job['dir'], kind=job['kind'],
                    label=job['label'], state='done' if done else 'queued', updated=review.stamp())
            elif entry.get('queued', {}).get('state') == 'queued':
                del entry['queued']
        review.write(review.REVIEW / 'manifest.json', manifest)


if __name__ == '__main__':
    main()
