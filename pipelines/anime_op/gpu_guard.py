"""Local thermal interlock for this OP batch; never changes hardware limits."""
import csv
import datetime
import json
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'assets/anime_op/school_full_op_v1/runtime'
OUT.mkdir(parents=True, exist_ok=True)
STOP = OUT / 'STOP_GPU_GUARD.json'
SCOPES = ('video/anime_op/school_first12s_v2/gemini_fullframe_v1', 'video/anime_op/school_full_op_v1/')


def api(path, data=None):
    req = urllib.request.Request('http://127.0.0.1:8188' + path,
        data=None if data is None else json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def write(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    # Windows readers/scanners can briefly hold a handle without delete sharing.
    # Retry only the atomic rename, bounded well below the worker's 45s watchdog.
    # Persistent failure still raises; never silently skip a telemetry update.
    for attempt in range(31):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt == 30:
                raise
            time.sleep(0.1)


def owned(queue_item):
    workflow = queue_item[2]
    return any(str(node.get('inputs', {}).get('filename_prefix', '')).startswith(SCOPES)
               for node in workflow.values() if isinstance(node, dict))


def main():
    tripped = STOP.exists()
    while not (OUT / 'STOP_GUARD_MONITOR').exists():
        tripped = tripped or STOP.exists()
        sample = {'time': datetime.datetime.now().astimezone().isoformat(), 'tripped': tripped}
        reason = None
        try:
            fields = 'temperature.gpu,power.limit,power.draw,fan.speed,clocks_event_reasons.hw_thermal_slowdown,clocks_event_reasons.sw_thermal_slowdown'
            run = subprocess.run(['nvidia-smi', '--query-gpu=' + fields, '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=15, check=True)
            values = next(csv.reader(run.stdout.splitlines()))
            temp, limit, power, fan = [float(v.strip()) for v in values[:4]]
            thermal = any(v.strip() == 'Active' for v in values[4:])
            sample.update(temperature_c=temp, power_limit_w=limit, power_w=power, fan_percent=fan, thermal_slowdown=thermal)
            if temp >= 87:
                reason = f'Conservative batch temperature threshold reached: {temp} C >= 87 C'
            elif thermal:
                reason = 'Thermal slowdown reported'
            elif limit > 400.5:
                reason = f'Expected <=400 W resumed-batch power limit; observed {limit} W'
            elif temp >= 80 and fan <= 0:
                reason = 'No fan activity reported at high GPU temperature'
        except Exception as exc:
            reason = f'GPU monitoring unavailable: {exc}'
        if reason and not tripped:
            tripped = True
            write(STOP, dict(time=sample['time'], reason=reason, automatic_restart=False))
            print(reason, flush=True)
        sample['tripped'] = tripped
        if tripped:
            try:
                queue = api('/queue')
                pending = [item[1] for item in queue['queue_pending'] if owned(item)]
                if pending:
                    api('/queue', {'delete': pending})
                running = queue['queue_running']
                if running and all(owned(item) for item in running):
                    api('/interrupt', {})
                    sample['owned_render_interrupted'] = True
                elif not running:
                    api('/free', {'unload_models': True, 'free_memory': True})
            except Exception as exc:
                sample['interrupt_error'] = str(exc)
        write(OUT / 'gpu_guard_status.json', sample)
        with (OUT / 'gpu_telemetry.jsonl').open('a', encoding='utf-8') as log:
            log.write(json.dumps(sample) + '\n')
        time.sleep(2)
    write(OUT / 'gpu_guard_status.json', dict(status='monitor_stopped', tripped=tripped))


if __name__ == '__main__':
    main()
