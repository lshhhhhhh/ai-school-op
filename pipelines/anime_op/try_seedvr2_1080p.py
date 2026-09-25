"""Three-shot SeedVR2 trial using existing native ComfyUI nodes."""
import ctypes,json,shutil,sys,time,uuid
from pathlib import Path
import op_pipeline as p
import op_graph as graph

ROOT=p.ROOT
BASE=ROOT/'assets/anime_op/makeine_school_first12s_v2'
OUT=ROOT/'assets/anime_op/seedvr2_1080p_trial_v1'
BASELINE=BASE/'op_first12s_school_gemini_motion_v1.mp4'
MODEL='seedvr2_3b_fp16.safetensors'
VAE='seedvr2_ema_vae_fp16.safetensors'
FPS='24000/1001'
SHOTS=[
 dict(id='01_claude_close',start=105,end=129,crop_x=480,input_kind='native 1024x576 model output -> 1920x1080',native=True),
 dict(id='02_gemini_run',start=72,end=105,crop_x=736,input_kind='existing repaired 1920x1080 composite -> same-size restoration',native=False),
 dict(id='03_gpt_title',start=186,end=288,crop_x=960,input_kind='existing repaired 1920x1080 composite -> same-size restoration',native=False),
]

def extract(source,target,start,end):
    p.run([p.FFMPEG,'-hide_banner','-loglevel','error','-y','-i',source,'-vf',
      f'trim=start_frame={start}:end_frame={end},setpts=N*1001/(24000*TB),setsar=1',
      '-frames:v',end-start,'-r',FPS,'-an','-c:v','libx264','-crf','12','-pix_fmt','yuv420p',target])

def node(kind,**inputs):return dict(class_type=kind,inputs=inputs)

def workflow(input_file,prefix,seed):
    return {
      '1':node('LoadVideo',file=p.stage(input_file)),
      '2':node('GetVideoComponents',video=['1',0]),
      '3':node('ImageScale',image=['2',0],upscale_method='lanczos',width=1920,height=1080,crop='disabled'),
      '4':node('SeedVR2Preprocess',resized_images=['3',0]),
      '5':node('VAEEncodeTiled',pixels=['4',0],vae=['21',0],tile_size=768,overlap=128,temporal_size=32,temporal_overlap=8),
      '6':node('SeedVR2TemporalChunk',latent=['5',0],temporal_overlap=2,chunking_mode='manual',**{'chunking_mode.frames_per_chunk':33}),
      '7':node('SeedVR2Conditioning',model=['20',0],vae_conditioning=['6',0]),
      '8':node('KSampler',model=['20',0],seed=seed,steps=1,cfg=1.0,sampler_name='euler',scheduler='simple',
          positive=['7',0],negative=['7',1],latent_image=['6',0],denoise=1.0),
      '9':node('SeedVR2TemporalMerge',latents=['8',0],temporal_overlap=['6',1]),
      '10':node('VAEDecodeTiled',samples=['9',0],vae=['21',0],tile_size=768,overlap=128,temporal_size=32,temporal_overlap=8),
      '11':node('SeedVR2PostProcessing',images=['10',0],original_resized_images=['3',0],color_correction_method='lab'),
      '12':node('CreateVideo',images=['11',0],fps=24,bit_depth=8),
      '13':node('SaveVideo',video=['12',0],filename_prefix=prefix,format='auto',codec='auto'),
      '20':node('UNETLoader',unet_name=MODEL,weight_dtype='default'),
      '21':node('VAELoader',vae_name=VAE),
    }

def validate(path,frames,audio=False):
    info=p.video_info(path)
    if (info['width'],info['height'],info['frames'],info['fps'],info['has_audio'])!=(1920,1080,frames,FPS,audio):
        raise RuntimeError(f'Unexpected export: {path}: {info}')
    p.run([p.FFMPEG,'-hide_banner','-loglevel','error','-i',path,'-f','null','-'])
    return info

def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    raw=Path(json.loads((BASE/'08_claude_close/raw_result.json').read_text(encoding='utf-8'))['raw'])
    for shot in SHOTS:
        dest=OUT/shot['id'];dest.mkdir(exist_ok=True)
        reference=dest/'baseline_1080p.mp4';source=dest/'input.mp4'
        if not reference.exists():extract(BASELINE,reference,shot['start'],shot['end'])
        if not source.exists():
            if shot['native']:extract(raw,source,0,shot['end']-shot['start'])
            else:shutil.copy2(reference,source)
        wf=workflow(source,'video/anime_op/seedvr2_trial_v1/'+shot['id'],2609232000+shot['start'])
        p.write_json(dest/'workflow.json',wf)
        p.write_json(dest/'build.json',dict(**shot,input=str(source),input_info=p.video_info(source),
          baseline=str(reference),baseline_sha256=p.sha(reference),target_resolution=[1920,1080],
          model=MODEL,vae=VAE,steps=1,color_correction='lab',temporal_chunk_pixel_frames=33,
          temporal_overlap_latent_frames=2,source_fps=FPS))
    p.write_json(OUT/'trial_plan.json',dict(baseline=str(BASELINE),baseline_sha256=p.sha(BASELINE),shots=SHOTS,
      output_scope='Only the three named shots are enhanced; other shots are retained. User reviews visual quality.',
      implementation='Native ComfyUI SeedVR2; existing core and dependencies unchanged.'))

def comparison(shot):
    dest=OUT/shot['id'];x=shot['crop_x']
    font="C\\:/Windows/Fonts/arial.ttf"
    labels=(f"drawtext=fontfile='{font}':text='ORIGINAL':fontcolor=white:fontsize=30:x=28:y=48,"
      f"drawtext=fontfile='{font}':text='SEEDVR2 3B':fontcolor=white:fontsize=30:x=988:y=48,"
      f"drawtext=fontfile='{font}':text='{shot['id']} - 1 to 1 detail crop':fontcolor=white:fontsize=20:x=28:y=92")
    vf=(f'[0:v]crop=960:1080:{x}:0,setpts=PTS-STARTPTS[a];'
      f'[1:v]crop=960:1080:{x}:0,setpts=PTS-STARTPTS[b];'
      f'[a][b]hstack=inputs=2,{labels}[v]')
    target=dest/'comparison_1to1.mp4'
    p.run([p.FFMPEG,'-hide_banner','-loglevel','error','-y','-i',dest/'baseline_1080p.mp4','-i',dest/'seedvr2_1080p.mp4',
      '-filter_complex',vf,'-map','[v]','-an','-r',FPS,'-c:v','libx264','-crf','15','-pix_fmt','yuv420p',target])
    return target

def assemble():
    plan=json.loads((OUT/'trial_plan.json').read_text(encoding='utf-8'))
    if p.sha(BASELINE)!=plan['baseline_sha256']:raise RuntimeError('Source baseline changed')
    filters=(
      '[0:v]split=3[a][b][c];'
      '[a]trim=end_frame=72,setpts=PTS-STARTPTS[v0];'
      '[1:v]setpts=PTS-STARTPTS[v1];'
      '[2:v]setpts=PTS-STARTPTS[v2];'
      '[b]trim=start_frame=129:end_frame=186,setpts=PTS-STARTPTS[v3];'
      '[3:v]setpts=PTS-STARTPTS[v4];'
      '[c]nullsink;[v0][v1][v2][v3][v4]concat=n=5:v=1:a=0[v]')
    final=OUT/'op_first12s_seedvr2_trial_v1.mp4'
    p.run([p.FFMPEG,'-hide_banner','-loglevel','error','-y','-i',BASELINE,
      '-i',OUT/'02_gemini_run/seedvr2_1080p.mp4','-i',OUT/'01_claude_close/seedvr2_1080p.mp4',
      '-i',OUT/'03_gpt_title/seedvr2_1080p.mp4','-filter_complex',filters,'-map','[v]','-map','0:a:0',
      '-frames:v','288','-r',FPS,'-c:v','libx264','-crf','15','-pix_fmt','yuv420p','-c:a','copy',
      '-t','12.012','-movflags','+faststart',final])
    info=validate(final,288,True)
    hashes=[]
    for path in [BASELINE,final]:hashes.append(p.run([p.FFMPEG,'-v','error','-i',path,'-map','0:a:0','-c:a','copy','-f','hash','-hash','sha256','-']).stdout.strip())
    if hashes[0]!=hashes[1]:raise RuntimeError('Original audio packets changed')
    comparisons=[comparison(shot) for shot in SHOTS]
    concat=OUT/'comparison_concat.txt'
    concat.write_text(''.join(f"file '{path.as_posix()}'\n" for path in comparisons for _ in range(2)),encoding='utf-8')
    side=OUT/'seedvr2_comparison_1to1.mp4'
    p.run([p.FFMPEG,'-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',concat,
      '-an','-c:v','libx264','-crf','15','-r',FPS,'-pix_fmt','yuv420p','-movflags','+faststart',side])
    validate(side,318)
    p.write_json(OUT/'technical_validation.json',dict(video=str(final),comparison=str(side),validation=info,
      original_audio_hash=hashes[0],output_audio_hash=hashes[1],baseline_unchanged=True,
      enhanced_source_intervals=[[72,105],[105,129],[186,288]],visual_review='Reserved for user; no per-frame aesthetic review.'))
    return final,side

def main():
    prepare()
    if '--prepare-only' in sys.argv:return
    if not (OUT/'model_provenance.json').exists():raise RuntimeError('Verified requested models not ready')
    if not p.queue_empty():raise RuntimeError('ComfyUI queue busy')
    power=float(p.run(['nvidia-smi','--query-gpu=power.limit','--format=csv,noheader,nounits']).stdout.strip().splitlines()[0])
    if power>450.5:raise RuntimeError('Expected existing 450W power cap')
    state=dict(status='running',started=time.time(),model=MODEL,completed=[])
    p.write_json(OUT/'status.json',state)
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    try:
        for shot in SHOTS:
            dest=OUT/shot['id'];record=dest/'raw_result.json'
            state.update(current=shot['id']);p.write_json(OUT/'status.json',state)
            if record.exists():raw=Path(json.loads(record.read_text(encoding='utf-8'))['raw'])
            else:
                wf=json.loads((dest/'workflow.json').read_text(encoding='utf-8'))
                elapsed,peak,files=graph.core.run_one(wf,str(uuid.uuid4()),'SeedVR2 '+shot['id'])
                matches=sorted((ROOT/'ComfyUI/output/video/anime_op/seedvr2_trial_v1').glob(shot['id']+'_*.mp4'))
                if not matches:raise RuntimeError('SeedVR2 result missing')
                raw=matches[-1]
                p.write_json(record,dict(raw=str(raw),seconds=elapsed,peak_vram_mb=peak,files=files))
            frames=shot['end']-shot['start'];target=dest/'seedvr2_1080p.mp4'
            extract(raw,target,0,frames)
            info=validate(target,frames)
            state['completed'].append(dict(id=shot['id'],output=str(target),validation=info))
            p.write_json(OUT/'status.json',state)
        final,side=assemble()
        state.update(status='complete_pending_user_review',video=str(final),comparison=str(side),finished=time.time())
    except BaseException as exc:
        state.update(status='failed',error=str(exc));raise
    finally:
        p.write_json(OUT/'status.json',state)
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        if p.queue_empty():graph.core.post('/free',{'unload_models':True,'free_memory':True})

if __name__=='__main__':main()
