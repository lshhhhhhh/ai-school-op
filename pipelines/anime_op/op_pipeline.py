"""Selective anime OP preparation, resumable rendering and original-audio assembly.
Examples are in README.md. No video frames are sent to an LLM by this script.
"""
import argparse, ctypes, hashlib, json, math, re, shutil, subprocess, sys, time, uuid
from pathlib import Path
from fractions import Fraction
import urllib.request
import op_graph as graph

ROOT = Path(__file__).resolve().parents[2]
FFMPEG = Path(r"C:\Program Files\ffmpeg\bin\ffmpeg.exe")
FFPROBE = FFMPEG.with_name("ffprobe.exe")
FPS = 24

def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows readers can temporarily deny replacement while inspecting status.
    # Keep the old valid JSON until atomic replacement succeeds; fail after 3s.
    for attempt in range(31):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 30:
                raise
            time.sleep(0.1)

def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for part in iter(lambda: f.read(8*1024*1024), b""): h.update(part)
    return h.hexdigest()

def run(args):
    proc = subprocess.run([str(a) for a in args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode:
        raise RuntimeError(proc.stderr[-3500:])
    return proc

def probe(path):
    return json.loads(run([FFPROBE, "-v", "error", "-show_streams", "-show_format",
                           "-of", "json", path]).stdout)

def video_info(path):
    p = probe(path); v = next(x for x in p["streams"] if x["codec_type"]=="video")
    return {"frames": int(v["nb_frames"]), "width": int(v["width"]),
            "height": int(v["height"]), "fps": v["r_frame_rate"],
            "duration": float(v["duration"]), "has_audio":
            any(x["codec_type"]=="audio" for x in p["streams"])}

def snap_frames(n):
    return max(56, 5 + math.ceil((n-5)/17)*17)

def stage(path):
    path = Path(path)
    name = "op_" + sha(path)[:20] + path.suffix.lower()
    dst = graph.core.INPUT_DIR / name
    if not dst.exists(): shutil.copy2(path, dst)
    return name

def fit_filter(width, height):
    return (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1")

def extract(master, start, end, out, generated_frames=None, width=None, height=None):
    n = end-start
    vf = f"trim=start_frame={start}:end_frame={end},setpts=N/(24*TB),fps=24"
    if width: vf += "," + fit_filter(width,height)
    if generated_frames and generated_frames>n:
        vf += ",tpad=stop_mode=clone:stop_duration=16"
    count = generated_frames or n
    run([FFMPEG,"-hide_banner","-loglevel","error","-y","-i",master,
         "-vf",vf,"-an","-frames:v",count,"-r",FPS,
         "-c:v","libx264","-crf","16","-pix_fmt","yuv420p",out])
    if video_info(out)["frames"] != count:
        raise RuntimeError("Prepared clip frame count mismatch")

def init_job(args):
    source = Path(args.source).resolve()
    if not source.is_file(): raise ValueError("Source video is missing")
    folder = Path(args.job).resolve(); folder.mkdir(parents=True,exist_ok=True)
    if (folder/"job.json").exists(): raise ValueError("Job already exists; choose a new directory")
    master = folder/"source_24fps.mp4"
    run([FFMPEG,"-hide_banner","-loglevel","error","-y","-i",source,
         "-vf","scale=trunc(iw*sar/2)*2:trunc(ih/2)*2,setsar=1,fps=24",
         "-an","-c:v","libx264","-crf","16","-pix_fmt","yuv420p",master])
    info = video_info(master)
    result = run([FFMPEG,"-hide_banner","-i",master,
                  "-vf",f"select=gt(scene\\,{args.scene_threshold}),showinfo",
                  "-an","-f","null","-"])
    detected = [round(float(x)*FPS) for x in
                re.findall(r"pts_time:([0-9.]+)",result.stderr)]
    cuts = sorted({0, info["frames"], *[x for x in detected if 0<x<info["frames"]]})
    segments=[{"id":f"shot_{i+1:03d}","start_frame":a,"end_frame":b,
               "action":"review","description":"","target_description":""}
              for i,(a,b) in enumerate(zip(cuts,cuts[1:]))]
    job={"schema":1,"source":str(source),"source_sha256":sha(source),
         "master":str(master),"master_sha256":sha(master),"fps":FPS,
         "master_info":info,"source_has_audio":any(x["codec_type"]=="audio" for x in probe(source)["streams"]),
         "target_description":"","source_character_ref":"","replacement_refs":[],
         "wardrobe":"source","preserve_notes":"",
         "settings":{"variant":"hybrid","width":1024,"height":576,"steps":20,
                     "refine":False,"refine_width":1536,"refine_height":864,
                     "refine_steps":10,"refine_denoise":0.30,"seed":26092201},
         "segments":segments}
    write_json(folder/"job.json",job)
    print(json.dumps({"job":str(folder/"job.json"),"frames":info["frames"],
                      "candidate_shots":len(segments),"action":"review all candidate cuts and mark copy/replace"},ensure_ascii=False))

def validate_job(job, need_render=True):
    total=job["master_info"]["frames"]; cursor=0; ids=set(); edits=0
    if Fraction(str(job.get("fps"))) != 24: raise ValueError("Working timeline must be 24 fps")
    for s in job["segments"]:
        if s["id"] in ids or not re.fullmatch(r"[A-Za-z0-9_-]+",s["id"]):
            raise ValueError("Shot IDs must be unique safe filenames")
        ids.add(s["id"])
        if type(s["start_frame"]) is not int or type(s["end_frame"]) is not int:
            raise ValueError("Shot boundaries must be integer frames")
        if s["start_frame"] != cursor or s["end_frame"]<=cursor:
            raise ValueError("Timeline has a gap, overlap, or empty segment")
        cursor=s["end_frame"]
        if s["action"] not in ("copy","replace"): raise ValueError("Review all segments before building")
        if s["action"]=="replace":
            edits+=1
            n=s["end_frame"]-s["start_frame"]
            if n<48: raise ValueError(f"{s['id']}: group sub-2-second cuts with adjacent shots and describe their cut times")
            if snap_frames(n)>362: raise ValueError(f"{s['id']}: split edit segments longer than the H3 limit")
            if not s.get("description","").strip(): raise ValueError(f"{s['id']}: shot description required")
            if not (s.get("target_description") or job.get("target_description")):
                raise ValueError("Identify the original character explicitly")
    if cursor!=total: raise ValueError("Timeline does not cover the full master")
    if edits:
        refs=[job.get("source_character_ref",""),*job.get("replacement_refs",[])]
        if not 2<=len(refs)<=9 or any(not p or not Path(p).is_file() for p in refs):
            raise ValueError("Provide original-character reference and 1..8 whale references")
    elif need_render: raise ValueError("No replacement shots selected")
    if job.get("wardrobe") not in ("source","replacement"): raise ValueError("Choose a wardrobe policy")
    for key,hashkey in [("source","source_sha256"),("master","master_sha256")]:
        if not Path(job[key]).is_file() or sha(job[key])!=job[hashkey]:
            raise ValueError(f"{key} changed; create a new job to avoid stale resume")
    return edits

def signature(job, segment):
    refs=[job.get("source_character_ref",""),*job.get("replacement_refs",[])]
    data={"source":job["source_sha256"],"master":job["master_sha256"],"segment":segment,
          "settings":job["settings"],"target":job["target_description"],
          "wardrobe":job["wardrobe"],"preserve":job.get("preserve_notes",""),
          "refs":[sha(p) for p in refs] if segment["action"]=="replace" else [],
          "graph_code":sha(Path(graph.__file__)),"pipeline_code":sha(__file__)}
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def build_shot(job, s, folder):
    cfg=job["settings"]; n=s["end_frame"]-s["start_frame"]; frames=snap_frames(n)
    shot=folder/s["id"]; shot.mkdir(exist_ok=True)
    refvideo=shot/"reference.mp4"
    extract(job["master"],s["start_frame"],s["end_frame"],refvideo,frames,
            cfg["width"],cfg["height"])
    refs=[stage(p) for p in [job["source_character_ref"],*job["replacement_refs"]]]
    prompt=graph.prompt_text(s.get("target_description") or job["target_description"],
                            s["description"],job["wardrobe"],job.get("preserve_notes",""))
    (shot/"prompt.txt").write_text(prompt,encoding="utf-8")
    digest=signature(job,s)
    options=dict(cfg); seed=options.pop("seed")+job["segments"].index(s)
    prefix=f"video/anime_op/{folder.name}/{s['id']}_{digest[:12]}"
    wf=graph.build(prompt,stage(refvideo),refs,prefix,frames=frames,seed=seed,**options)
    write_json(shot/"workflow.json",wf)
    write_json(shot/"build.json",{"signature":digest,"target_frames":n,
                                "generated_frames":frames,"prefix":prefix})
    return shot,wf,digest,prefix

def assemble(folder, job):
    outputs=[]
    for s in job["segments"]:
        out=folder/s["id"]/"timeline.mp4"
        if not out.exists() or video_info(out)["frames"]!=s["end_frame"]-s["start_frame"]:
            raise ValueError("Missing or invalid timeline clip: "+s["id"])
        record=json.loads((out.parent/"result.json").read_text(encoding="utf-8"))
        if record["signature"]!=signature(job,s): raise ValueError("Stale clip: "+s["id"])
        outputs.append(out)
    # Safe relative filenames: shot IDs are validated above, output names are fixed.
    concat=folder/"concat.txt"
    concat.write_text("".join(f"file '{p.parent.name}/timeline.mp4'\n" for p in outputs),encoding="utf-8")
    silent=folder/"op_replacement_silent.mp4"
    run([FFMPEG,"-hide_banner","-loglevel","error","-y","-f","concat","-safe","1",
         "-i",concat,"-c","copy","-movflags","+faststart",silent])
    info=video_info(silent)
    if info["frames"]!=job["master_info"]["frames"]:raise RuntimeError("Assembled frame count mismatch")
    final=folder/"op_replacement_original_music.mp4"
    if job["source_has_audio"]:
        run([FFMPEG,"-hide_banner","-loglevel","error","-y","-i",silent,"-i",job["source"],
             "-map","0:v:0","-map","1:a:0","-c:v","copy","-af",
             f"apad,atrim=end={info['frames']/FPS:.9f}","-c:a","aac","-b:a","256k",
             "-t",f"{info['frames']/FPS:.9f}","-movflags","+faststart",final])
    else: final=silent
    return {"silent":str(silent),"final":str(final),"frames":info["frames"],
            "duration_seconds":info["frames"]/FPS,"audio":"original" if job["source_has_audio"] else "none"}

def queue_empty():
    q=graph.core.get("/queue")
    return not q.get("queue_running") and not q.get("queue_pending")

def render_job(args):
    folder=Path(args.job).resolve(); job=json.loads((folder/"job.json").read_text(encoding="utf-8"))
    validate_job(job,need_render=(args.command!="assemble"))
    if args.command=="assemble":
        print(json.dumps(assemble(folder,job),ensure_ascii=False));return
    if args.command=="build":
        for s in job["segments"]:
            if s["action"]=="replace":build_shot(job,s,folder)
        print("Workflow JSON and prompts prepared; nothing submitted.");return
    if not queue_empty():raise RuntimeError("ComfyUI is busy; do not interleave batches")
    import contextlib
    # Retain the existing GPU limit; never change it or request elevation.
    power=run(["nvidia-smi","--query-gpu=power.limit","--format=csv,noheader,nounits"]).stdout.strip()
    if float(power.splitlines()[0])>450.5:raise RuntimeError("Expected existing 450W cap; inspect GPU policy")
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    status={"status":"running","completed":[],"started":time.time()}
    write_json(folder/"status.json",status)
    try:
        for s in job["segments"]:
            status["current"]=s["id"];write_json(folder/"status.json",status)
            shot=folder/s["id"];shot.mkdir(exist_ok=True)
            record=shot/"result.json"; digest=signature(job,s); out=shot/"timeline.mp4"
            if record.exists():
                prior=json.loads(record.read_text(encoding="utf-8"))
                if prior["signature"]!=digest:raise RuntimeError("Inputs changed; use a new job directory")
                if out.exists() and video_info(out)["frames"]==s["end_frame"]-s["start_frame"]:
                    status["completed"].append(s["id"]);write_json(folder/"status.json",status);continue
            if s["action"]=="copy":
                extract(job["master"],s["start_frame"],s["end_frame"],out)
                raw=None
            else:
                shot,wf,digest,prefix=build_shot(job,s,folder)
                raw_record=shot/"raw_result.json"
                if raw_record.exists():
                    cached=json.loads(raw_record.read_text(encoding="utf-8"))
                    if cached["signature"]!=digest:raise RuntimeError("Raw clip belongs to different settings")
                    raw=Path(cached["raw"])
                    if not raw.is_file():raise RuntimeError("Recorded raw render is missing")
                else:
                    prefix_path=ROOT/"ComfyUI/output"/prefix
                    # Adopt a successfully saved render if interrupted before result bookkeeping.
                    candidates=sorted(prefix_path.parent.glob(prefix_path.name+"_*.mp4"))
                    raw=candidates[-1] if candidates else None
                    if raw is None:
                        graph.core.run_one(wf,str(uuid.uuid4()),s["id"])
                        candidates=sorted(prefix_path.parent.glob(prefix_path.name+"_*.mp4"))
                        if not candidates:raise RuntimeError("No saved render found")
                        raw=candidates[-1]
                    write_json(raw_record,{"signature":digest,"raw":str(raw)})
                n=s["end_frame"]-s["start_frame"]; info=job["master_info"]
                # Remove the fit-to-model padding before restoring source output geometry.
                cfg=job["settings"]; iw=cfg["refine_width"] if cfg["refine"] else cfg["width"]
                ih=cfg["refine_height"] if cfg["refine"] else cfg["height"]
                ratio=min(iw/info["width"],ih/info["height"])
                cw=max(2,int(info["width"]*ratio)//2*2);ch=max(2,int(info["height"]*ratio)//2*2)
                vf=f"crop={cw}:{ch},scale={info['width']}:{info['height']},setsar=1"
                run([FFMPEG,"-hide_banner","-loglevel","error","-y","-i",raw,
                     "-vf",vf,"-an","-frames:v",n,"-r",FPS,"-c:v","libx264",
                     "-crf","16","-pix_fmt","yuv420p",out])
                if video_info(out)["frames"]!=n:raise RuntimeError("Generated clip too short")
            write_json(record,{"signature":digest,"action":s["action"],"output":str(out),"raw":str(raw) if raw else None})
            status["completed"].append(s["id"]);write_json(folder/"status.json",status)
        status.update(status="complete",outputs=assemble(folder,job),finished=time.time(),current=None)
    except BaseException as e:
        status.update(status="failed",error=str(e),finished=time.time())
        raise
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        try:
            can_free=queue_empty()
        except Exception:
            can_free=False
        if can_free:
            req=urllib.request.Request(graph.core.SERVER+"/free",data=b'{"unload_models":true,"free_memory":true}',headers={"Content-Type":"application/json"})
            try:
                with urllib.request.urlopen(req,timeout=15) as r:r.read()
            except Exception:pass
        write_json(folder/"status.json",status)
    print(json.dumps(status,ensure_ascii=False))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    a=sub.add_parser("init");a.add_argument("--source",required=True);a.add_argument("--job",required=True)
    a.add_argument("--scene-threshold",type=float,default=0.32)
    for name in ("build","render","assemble"):
        a=sub.add_parser(name);a.add_argument("--job",required=True)
    args=p.parse_args()
    if args.command=="init":init_job(args)
    else:render_job(args)
if __name__=="__main__":
    main()
