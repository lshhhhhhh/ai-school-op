"""H3 selective character edit graphs. Baseline and experimental hybrid/refine."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import generate as core

HYBRID = "minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors"
UPSCALER = str(Path("minimax_h3_latent_upscaler_3d_conv_v1") / "minimax_h3_latent_upscaler_3d_conv_v1_fp16.safetensors")

def prompt_text(target, description, wardrobe="source", preserve=""):
    clothes = (
        "Retain the source character's clothing, accessories, body proportions and silhouette "
        "where compatible with the new identity."
        if wardrobe == "source" else
        "Use the whale girl's outfit from <Picture 2>, fitted to the source pose and perspective."
    )
    return f"""subject_definitions:
<Subject 1> is the source character identified by <Picture 1>: {target}.
<Subject 2> is the DeepSeek whale girl identified by <Picture 2> and any later pictures of her; preserve her blue hair, facial identity, whale features and recognizable design.
<Subject 3> is the remaining cast of <Video 1>, excluding <Subject 1>.
<Subject 4> is the source background, props, foreground occluders, lighting and existing visual effects.
<Video 1> is the source clip to edit.

summary:
[video editing + reference generation] The target video is an edited version of <Video 1>. Replace only <Subject 1> with <Subject 2>. Keep source action timing, framing, camera motion and cut times. {clothes}

retention_analysis:
<Subject 1>: attribute_transfer - transfer only the source performance, screen position, perspective and action timing to <Subject 2>; remove the source identity.
<Subject 2>: fully_preserved - retain her identity while adapting linework, shading and proportions to the source animation.
<Subject 3>: fully_preserved - keep identities, clothing, actions and positions; do not turn them into whale girls.
<Subject 4>: fully_preserved - keep scene layout, colors, props and layering.
<Video 1>: fully_preserved - retain composition, timing and shot order. {preserve}

detailed_description:
Match the original OP's animation technique, line thickness, flat colors and shading.
[Shot 1] {description}
Track <Subject 1> by identity throughout the clip, including when characters cross or exchange screen positions. Only her replacement <Subject 2> changes appearance. Reproduce source poses, gaze, expressions and interactions. Respect objects and other characters passing in front of her. Keep empty frames empty. Preserve stylized motion and intentional held frames. Do not invent camera moves, cuts, captions, credits, logos, extra characters or new action.

overall_soundscape:
No intelligible dialogue or new sound events. The generated audio is discarded during assembly.

non_diegetic_music:
N/A
"""

def build(prompt, video, images, prefix, *, variant="baseline", width=1024,
          height=576, frames=124, seed=26092201, steps=20,
          refine=False, refine_width=1536, refine_height=864,
          refine_steps=10, refine_denoise=0.30):
    for dimension in (width, height, refine_width, refine_height):
        if dimension < 32 or dimension % 32:
            raise ValueError("H3 dimensions must be positive multiples of 32")
    if (frames - 5) % 17 or not 56 <= frames <= 362:
        raise ValueError("Reference clips must use 56..362 frames on H3's 17k+5 grid")
    if len(images) < 2 or len(images) > 9:
        raise ValueError("Provide original-character reference first, then whale references (2..9 images)")
    if refine and (refine_width < width or refine_height < height):
        raise ValueError("Refinement cannot downscale")
    if refine and abs(refine_width / refine_height - width / height) > 1e-6:
        raise ValueError("Both passes must have the same aspect ratio")
    if variant not in ("baseline", "hybrid"):
        raise ValueError("Unknown model variant")
    wf = core.build_workflow(prompt, width, height, frames, seed, steps,
                             "res_multistep", "simple", prefix,
                             video=video, ref_images=images, ref_size="match",
                             ref_audio=False)
    if variant == "hybrid":
        wf["6"]["inputs"]["unet_name"] = HYBRID
    # OP audio is copied from the original master; don't decode generated audio.
    wf.pop("23")
    wf["91"]["inputs"].pop("audio")
    if refine:
        wf["300"] = {"class_type": "LTXVSeparateAVLatent",
                     "inputs": {"av_latent": ["14", 0]}}
        wf["301"] = {"class_type": "MinimaxH3LatentUpscaler3D", "inputs": {
            "latent": ["300", 0], "model_name": UPSCALER,
            "mode": "target dimensions",
            "mode.width": refine_width, "mode.height": refine_height,
            "align": 32, "enable_temporal_chunking": True,
            "force_unload": True, "device": "cuda", "precision": "fp16"}}
        wf["302"] = {"class_type": "LTXVConcatAVLatent", "inputs": {
            "video_latent": ["301", 0], "audio_latent": ["300", 1]}}
        wf["303"] = {"class_type": "BasicScheduler", "inputs": {
            "model": ["6", 0], "scheduler": "simple",
            "steps": refine_steps, "denoise": refine_denoise}}
        wf["304"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed + 1}}
        wf["305"] = {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["304", 0], "guider": ["16", 0], "sampler": ["17", 0],
            "sigmas": ["303", 0], "latent_image": ["302", 0]}}
        wf["10"]["inputs"]["samples"] = ["305", 0]
    return wf
