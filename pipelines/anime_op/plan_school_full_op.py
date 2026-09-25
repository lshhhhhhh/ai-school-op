"""Freeze the reviewed whole-OP cast, text and shot plan before generation."""
import json
from pathlib import Path
import op_pipeline as p

OUT = p.ROOT / 'assets/anime_op/school_full_op_v1'
REF = p.ROOT / 'assets/character_designs/school_v1/outputs'
OLD = p.ROOT / 'assets/anime_op/seedvr2_first12s_full_v1'
GEM = p.ROOT / 'assets/anime_op/makeine_school_first12s_v2/refinements/07_gemini_fullframe_v1'
TITLE = 'Replace the existing Japanese series title with exactly 負けAIが多すぎる！ and its English tagline with too many losing AIs. Preserve their graphic style, placement, layering and animation. Remove old ヒロイン wording. Do not add a title where there was none.'

# Each original identity has exactly one replacement throughout the full opening.
CAST = {
 'gpt': ('温水和彦 / Kazuhiko Nukumizu', 'the ordinary short dark-haired male protagonist', 'gpt_original', 'human silver-white medium-length hair, grey-green eyes, calm reliable demeanor, green bows; no dragon features'),
 'deepseek': ('八奈見杏菜 / Anna Yanami', 'the blue/teal-haired girl with a short voluminous bob and yellow uniform bows', 'deepseek', 'long dark indigo-blue hair, blue eyes, loop ahoge, small whale fins and whale tail, blue bows'),
 'gemini': ('焼塩檸檬 / Lemon Yakishio', 'the athletic tan girl with short brown hair and sporty poses', 'gemini', 'long blue-violet hair with pink tips, cat ears and fluffy purple tail, multicolored Google bow accents'),
 'claude': ('小鞠知花 / Chika Komari', 'the shy petite red/auburn bob-haired girl', 'claude', 'long copper-orange hair, amber eyes, small flower/starburst hairclip, orange bows'),
 'minimax': ('温水佳樹 / Kaju Nukumizu', 'the younger sister with long dark-brown hair, straight short bangs and large eyes', 'minimax', 'short peach-orange hair, coral wave clip, coral bows'),
 'kimi': ('姫宮華恋 / Karen Himemiya', 'the long pink/lavender-haired girl accompanying the spiky brown-haired boy', 'kimi', 'very long silvery-lavender hair, blue eyes, compact crescent/prism accessory, navy and pale-blue bows'),
 'grok': ('袴田草介 / Sosuke Hakamada', 'the handsome spiky brown-haired boy beside the long pink-haired girl', 'grok', 'long black hair, broad white forelock, grey eyes, small X clip, black-and-white bows'),
 'qwen': ('朝雲千早 / Chihaya Asagumo', 'the petite green-bob-haired girl with a broad forehead beside the light-brown-haired boy', 'qwen', 'long blue-violet hair with side braid, violet eyes, compact violet knot clip and purple bows'),
 'mistral': ('綾野光希 / Mitsuki Ayano', 'the light-brown/blond-haired intellectual boy accompanying the green-bob-haired girl', 'mistral', 'short angular orange bob with dark underlayer and red forelock, amber eyes, pixel M clip, warm orange/red bows'),
 'perplexity': ('月之木古都 / Koto Tsukinoki', 'the tall long dark-purple-haired literature-club girl with rectangular glasses', 'perplexity', 'deep teal low ponytail, teal eyes, rectangular glasses, teal-grey-white bows and compass badge'),
 'glm': ('玉木慎太郎 / Shintaro Tamaki', 'the literature-club boy with short dark green hair accompanying the glasses-wearing girl', 'glm', 'green bob with two slim braids, green eyes and rectangular glasses, green-black-white bows, GLM badge'),
 'doubao': ('甘夏古奈美 / Konami Amanatsu', 'the short brown-haired teacher in a purple casual outfit', 'doubao', 'brown bob with two small buns, brown eyes, blue-cream bows and small bean/speech badge'),
 'copilot': ('小抜小夜 / Sayo Konuki', 'the tall long pale-green-haired teacher beside the short brown-haired teacher', 'copilot', 'turquoise high ponytail, blue-green eyes, blue-green-coral bows and ribbon-loop badge'),
 'llama': ('志喜屋夢子 / Yumeko Shikiya', 'the pale long-haired girl with a sleepy gaze at the bus stop', 'llama', 'fluffy honey-blonde shoulder-length curls, cream llama ears, hazel eyes, small fluffy cream tail, blue infinity badge'),
 'ernie': ('馬剃天愛星 / Tiara Basori', 'the shorter navy-haired student-council girl on the left side of the stairs', 'ernie', 'midnight-blue asymmetrical bob, tiny braid, paired red-blue clips, blue eyes and blue-red-white bows'),
 'hunyuan': ('放虎原ひばり / Hibari Hokobaru', 'the very tall straight long red-haired student-council president on the stairs', 'hunyuan', 'long silver-white side braid with teal ends, turquoise eyes, orbit clip and teal-blue-white bows'),
 'cohere': ('権藤アサミ / Asami Gondo', 'the taller brown-bob-haired schoolgirl accompanying Kaju outside the shop', 'cohere', 'shoulder-length wavy forest-green hair with one cream streak, hazel eyes and peach/green/cream bows'),
 'spark': ('student-council reader on the stairs', 'the seated purple-haired glasses-wearing student reading behind the two council girls', 'spark', 'burgundy low side ponytail, rimless glasses, blue eyes, blue flame clip and blue-red-white bows'),
}

# (candidate number, cast, source action, replacement credit pairs). Credit names are a fictional tribute.
SPECS = [
 (1, [], 'Opening geometric flash.', []),
 (2, ['deepseek'], 'Extreme close-up of Yanami; preserve the exact facial pose.', []),
 (3, ['deepseek'], 'Second short close-up of Yanami.', []),
 (4, ['deepseek'], 'Wide rooftop character-introduction card; retain the small figure and gestures.', [('character name','DeepSeekちゃん')]),
 (5, ['gemini'], 'Athletic girl close-up.', []),
 (6, ['gemini'], 'Athletic girl second close-up.', []),
 (7, ['gemini'], 'Wide park card: stretch, bend into a deep crouch, then launch into a run to SCREEN RIGHT. The head turns and tilts with the neck and torso; show a right-facing side profile on the run, never a fixed frontal face. Preserve the large foreground drink bottle.', [('character name','Geminiちゃん')]),
 (8, ['claude'], 'Close-up of the shy girl.', []),
 (9, ['claude'], 'Wide library character card: reproduce the shy girl turning her head, shoulders and body and responding to the camera. Retain her small source screen size, her changing expression, arm motion and the source camera move; do not leave a still reference portrait standing motionless.', [('character name','Claudeちゃん')]),
 (10, [], 'Blue title typography flash.', []),
 (11, [], 'Purple typography flash.', [('large purple-card glyphs','AI')]),
 (12, [], 'Yellow typography flash.', [('large yellow-card glyphs','が')]),
 (13, [], 'Orange title typography flash.', []),
 (14, ['gpt'], 'Blackboard title scene. Replace the boy, including his entire body, with the original silver-haired GPT schoolgirl. Copy his complete performance, movements, perspective and relationship to the board.', []),
 (16, [], 'Black-and-white statue and animated title graphic; the statue remains a statue.', []),
 (17, ['deepseek','gemini'], 'Two girls stretching in the classroom, divided by graphic shapes.', []),
 (18, ['claude','gpt'], 'Split library panels containing the shy girl and the male protagonist.', []),
 (19, [], 'Title graphic over the rooftop/roadside sign.', []),
 (20, ['gpt'], 'The protagonist browses in a colorful bookstore, seen from behind.', []),
 (21, ['gpt'], 'Wide bookstore view, protagonist at the shelves.', [('原作','Yoshua Bengio')]),
 (22, ['gpt'], 'Bookstore medium shot, protagonist reaching toward books.', [('原作','Yoshua Bengio')]),
 (23, ['gpt'], 'Close-up of protagonist turning at the bookstore.', [('キャラクター原案','Fei-Fei Li')]),
 (24, [], 'Street/sky with large multicolored planning-credit columns.', [('企画','Sam Altman / Elon Musk / 梁文锋 / Sundar Pichai / Dario Amodei / Jensen Huang / Mark Zuckerberg')]),
 (25, ['deepseek','gemini'], 'Two main girls begin entering below the large planning credits.', [('企画','Sam Altman / Elon Musk / 梁文锋 / Sundar Pichai / Dario Amodei / Jensen Huang / Mark Zuckerberg')]),
 (26, ['deepseek','gemini'], 'Blue-haired girl and athletic girl in front of planning credits.', [('企画','Sam Altman / Elon Musk / 梁文锋 / Sundar Pichai / Dario Amodei / Jensen Huang / Mark Zuckerberg')]),
 (27, [], 'Passing streetcars with diagonal series-composition credit.', [('シリーズ構成','Yann LeCun')]),
 (28, ['deepseek','gemini','claude'], 'Wide shopping plaza; replace any of the three main girls actually visible, retaining distant pedestrians.', [('キャラクターデザイン','Ilya Sutskever'),('サブキャラクターデザイン','Demis Hassabis')]),
 (29, ['claude'], 'The shy red-haired girl looks at her phone in the shopping plaza.', [('キャラクターデザイン','Ilya Sutskever'),('サブキャラクターデザイン','Demis Hassabis')]),
 (30, [], 'Brief graphic of a space shuttle.', []),
 (31, ['claude','glm','perplexity'], 'Station front: the shy girl is seen from behind in the foreground, with the literature-club boy and glasses-wearing girl behind her.', [('メインアニメーター','Andrej Karpathy / Noam Shazeer / Ashish Vaswani')]),
 (32, ['deepseek','gemini','claude','gpt','glm','perplexity'], 'Restaurant wide shot; keep each recognizable club member in their original position and preserve generic background diners.', [('ビジュアルボード','Fei-Fei Li / Saining Xie')]),
 (33, ['deepseek','gemini','claude','gpt','glm','perplexity'], 'Restaurant table group: the tall glasses-wearing girl is on the left, blue-haired main girl raises an arm on the right, athletic girl at the table, shy girl and club boy further back. Identify the protagonist only if visible.', [('ビジュアルボード','Fei-Fei Li / Saining Xie')]),
 (34, ['mistral','qwen'], 'Restaurant couple: light-brown-haired boy on the right and green-bob-haired girl on the left.', [('プロップデザイン','Andrej Karpathy')]),
 (35, ['grok','kimi'], 'Formal flower garden and topiary; spiky-brown-haired boy and pink-haired girlfriend enter along the bottom edge. Keep their original partial framing.', [('色彩設計','Robin Rombach')]),
 (36, ['minimax'], 'Younger sister Kaju close-up, smiling with both index fingers pointing at her cheeks; retain the blink and smile.', []),
 (38, ['gpt','minimax'], 'Zoo entrance wide shot: dark-haired protagonist and younger sister together near the middle-right; preserve unrelated visitors and signage.', [('美術監督','Alec Radford'),('美術','Aditya Ramesh')]),
 (39, [], 'Wood tabletop with art-setting credits.', [('美術設定','David Holz'),('美術ボード協力','Patrick Esser')]),
 (40, ['doubao','copilot'], 'Teachers by the bookstore stairs: short brown-haired teacher on the left and tall pale-green-haired teacher on the right.', [('3D監督','Ben Mildenhall')]),
 (41, ['deepseek'], 'Blue-haired main girl pictured within a curved turning book page. Preserve the full page-turn, picture deformation and dissolve into printed paper; do not turn the page into a separate portrait.', []),
 (44, [], 'Printed page and incidental book prose.', []),
 (45, ['gpt'], 'Close-up of protagonist in the library with changing light.', []),
 (46, ['gpt'], 'Wide library scene with protagonist.', [('撮影監督','Tim Brooks / William Peebles'),('編集','tibo / RESET')]),
 (47, ['deepseek'], 'Back of the blue-haired main girl, retain source head turn.', []),
 (48, ['deepseek'], 'Close-up of the main girl eye and face.', []),
 (49, ['deepseek'], 'Main girl looks up into sunlight; follow the exact gaze and head angle.', [('オープニングテーマ','つよがるガール'),('AI音楽演出','Mati Staniszewski')]),
 (50, ['deepseek'], 'Main girl raises an arm in sunlight.', [('オープニングテーマ','つよがるガール'),('AI音楽演出','Mati Staniszewski')]),
 (51, [], 'Fence and sky with opening-theme lettering. Retain song title but replace the displayed staff/artist-name block as specified; original recorded soundtrack is unchanged.', [('オープニングテーマ','つよがるガール'),('AI音楽演出','Mati Staniszewski / Mikey Shulman / David Ding')]),
 (52, ['deepseek','gemini','claude'], 'The three main girls start running across the field. Follow each source identity consistently through crossing, distance and occlusion.', [('音楽','Mikey Shulman'),('音楽制作','AI School Sound')]),
 (53, ['deepseek','gemini','claude'], 'Close-up of running legs and shoes. Use school uniform skirt, socks and brown loafers matching the appropriate runner. Keep feet planted at each impact and retain the source running rhythm. Do not add faces into this foot-only composition.', []),
 (54, ['deepseek'], 'Side-profile running close-up. Her head bobs and pitches with each stride, nose follows travel direction; hair and tail trail naturally. Never freeze her head in a front portrait.', []),
 (55, ['gemini'], 'Athletic girl side-profile running close-up. Head, neck and shoulders move together; ears follow head rotation, hair and tail trail, nearer eye only when in side profile. Preserve exact source running direction and body tilt.', []),
 (56, ['claude'], 'Shy girl side-profile running close-up, exertion and bobbing head follow the source; long copper hair responds to running.', []),
 (57, [], 'Sky and expressive lyric/graphic lettering.', []),
 (58, ['deepseek','claude'], 'Two main girls on a pedestrian bridge.', [('音楽プロデューサー','David Ding / Mati Staniszewski')]),
 (59, ['deepseek','claude'], 'Bridge scene from behind, follow walking and turns.', [('音響監督','Alex Graves'),('音響効果','Aaron van den Oord')]),
 (60, ['llama'], 'Sleepy pale long-haired girl at a bus stop, originally wearing pink; transfer her pose and expression to Llama schoolgirl.', [('プロデューサー','Dario Amodei / Daniela Amodei')]),
 (61, ['grok','kimi'], 'Spiky brown-haired boy and his long pink-haired girlfriend stand close together on the shopping street.', [('プロデューサー','Elon Musk')]),
 (62, ['mistral','qwen'], 'Light-brown-haired boy and green-bob-haired girl beside a pool in a forest cave.', [('プロデューサー','Arthur Mensch / 吴泳铭')]),
 (63, ['ernie','hunyuan','spark'], 'School staircase: shorter navy-haired council girl left, tall very long red-haired council president center, purple-haired glasses-wearing seated reader behind. Retain three different identities and their positions.', [('原作協力','李彦宏 / 马化腾')]),
 (64, ['glm','perplexity'], 'Literature-club boy and tall dark-haired glasses-wearing girl at the classroom doorway.', [('制作統括','唐杰 / Aravind Srinivas')]),
 (65, ['copilot','doubao'], 'Two teachers inside an old church-like room: tall pale-green-haired woman left, short brown-haired woman right.', [('アニメーションプロデューサー','张一鸣'),('制作デスク','Satya Nadella')]),
 (66, ['cohere','minimax'], 'Two younger girls outside the shop: taller brown-bob-haired friend on the left, Kaju with long dark hair on the right.', [('アニメーション制作','AI School Studio')]),
 (67, ['gpt'], 'Six-frame coastal panorama with protagonist seen from behind in a white hooded jacket. Replace identity and clothing with GPT school uniform; preserve the billowing motion through hair and skirt.', []),
 (68, ['deepseek','gemini'], 'Six-frame split street panel: blue-haired main girl left and athletic girl right.', []),
 (69, ['claude','deepseek','gemini'], 'Six-frame urban panel: shy girl in foreground, blue-haired main girl on right, athletic girl behind. Preserve all three positions.', []),
 (70, ['deepseek'], 'Six-frame dreamlike leafy scene with blue-haired main girl in a white dress. Transfer her whole pose and face to DeepSeek in school uniform, preserve the dappled green light.', []),
 (71, [], 'Paperback book on wooden table; incidental printed prose remains.', []),
 (72, ['claude'], 'Shy girl sitting on a beach in a pink hoodie; use Claude school uniform and retain her hunched seated posture.', []),
 (73, ['gemini'], 'Athletic girl under a yellow umbrella in the rain; keep the umbrella, wet ground and reflections; use Gemini school uniform.', []),
 (74, ['deepseek'], 'Blue-haired main girl from behind beneath sunset roof structures.', []),
 (75, [], 'Large director credit against animated clouds and colorful geometric shapes.', [('総監督','Geoffrey Hinton')]),
 (76, ['deepseek'], 'Close back-of-head view of main girl before turning.', []),
 (77, ['deepseek'], 'Main girl turns, smiles and waves energetically toward the camera; preserve her complete head/body turn and arm sweep.', []),
 (78, [], 'Five-frame whip-pan motion blur transition.', []),
 (79, ['gemini'], 'Athletic girl by the classroom window with one hand at her temple and phone in the other. Preserve relaxed head tilt, blinking and arm gestures.', []),
 (80, ['claude'], 'Shy girl with a book in the library, glancing toward camera. Preserve page and hand movement and actual head turn.', []),
 (81, [], 'Menu and food graphic flash; keep original food and packaging text.', []),
 (82, [], 'Sports shoe graphic flash; retain original prop.', []),
 (83, [], 'Seven-frame literature-club sign; retain the incidental sign.', []),
 (84, [], 'Hand closes/holds a book on wooden table, final production committee credit. Preserve hand, book movement and final transition.', [('製作','AI学園制作委員会')]),
 (85, [], 'Final black tail.', []),
]

def prompt_for(s, cast):
    refs = '\n'.join(f'<Picture {i}> defines ONLY {k.upper()} appearance: {cast[k]["appearance"]}. Replace {cast[k]["original"]}, identified as {cast[k]["source_description"]}, with this girl.' for i,k in enumerate(s['cast'],1))
    text = '\n'.join(f'Existing credit/name area {role}: use exactly "{name}".' for role,name in s['credits'])
    if s['credits']:
        text += '\nReplace old personal names in these credit blocks; do not overlay old and new names. Keep the source typography, colors, orientation, relative size, animated placement and graphic layering. Split slash-separated names into separate existing credit lines/columns. Replace any A-1 Pictures affiliation with AI School Studio. Fit legibly inside the original credit regions, never across faces. Do not invent additional credits.'
    else:
        text = 'Keep incidental text and packaging unchanged. Do not introduce new names, labels or credits.'
    if s['number'] in {4,7,9,14,16,17,18,19}:
        text += '\n' + TITLE
    return f'''subject_definitions:
<Video 1> is the COMPLETE source anime shot, governing full-frame composition, source identities, every pose, performance, camera motion, props, transitions and text layout.
{refs}
Reference pictures are identity and school-uniform references ONLY, never pose, camera, expression, framing or background references. When several identities are listed, replace each ONLY if that source identity is actually visible; do not add absent characters. Generic background extras stay unchanged.

summary:
[video editing + reference generation] Generate ONE coherent complete anime shot. Perform every listed identity, clothing and text change together throughout the full frame. Match the original hand-drawn television anime, clean outlines, restrained cel shadows and color palette. No separate character patch or text-paste stage will follow.

detailed_description:
[Shot 1] {s['description']}
Match all actions and camera movements of Video 1 from beginning to end. This complete source shot has been uniformly stretched to the model frame grid; its full output will be uniformly returned to the original shot duration. Never stop the action early or add a new shot. Preserve intentional anime holds, while reproducing all actual source head, neck, shoulder, torso, hand and leg motion. Source gaze and head orientation take precedence over the frontal reference sheets. Allow profile, back of head, foreshortening, occlusion and partial framing; no fixed frontal face. Hair, ears, tails and uniform respond to the source movement. Do not enlarge distant people.
Every replacement wears the unified school outfit from her own picture: white short-sleeve collared shirt, small colored stacked bows, grey-blue pleated skirt with one white hem stripe, dark socks and brown loafers. Change original male characters into their assigned AI girls completely. Keep each assigned girl's own hair, face and compact badge separate; no identity blending or swapped colors.
Retain original background layout, perspective, linework, lighting, props, source motion blur, split panels and foreground occlusion. All scene regions share consistent texture and sharpness. No rectangular patch, background halo, pasted silhouette, doubled outline, freeze-frame portrait, new camera zoom, inset, white reference-sheet background or extra character.

text_replacements:
{text}

retention_analysis:
Listed cast: replace identity and outfit, preserve full source performance, screen size, screen position, interactions and source occlusion.
Background extras and props: preserve. Product packaging, shop signs, drinks, food, umbrellas and incidental book prose stay as drawn unless specifically listed above.
Composition, camera, animation timing, graphic layering and transitions: preserve across the entire shot.
Text blocks listed above: replace content while retaining source graphic treatment. All other lettering remains as in Video 1.
Produce the entire composition as one video; no cropped character, mask, compositing patch or separately replaced background.

overall_soundscape:
None. Original recorded soundtrack is restored after generation.

non_diegetic_music:
N/A
'''

def main():
    inv=json.loads((OUT/'source_inventory.json').read_text(encoding='utf-8-sig'))
    cast={k:dict(original=v[0],source_description=v[1],reference=str(REF/(v[2]+'_school_front_v1.png')),appearance=v[3]) for k,v in CAST.items()}
    old=json.loads((OLD/'full_plan.json').read_text(encoding='utf-8-sig'))['shots']
    reuse={i+1:str(OLD/s['id']/'seedvr2_1080p.mp4') for i,s in enumerate(old[:13]) if i+1!=9}
    reuse[7]=str(GEM/'seedvr2_1080p.mp4')
    merged={14:15,36:37,41:43}
    shots=[]
    for num, actors,desc,credits in SPECS:
        start=inv['shots'][num-1]['start']; last=merged.get(num,num); end=inv['shots'][last-1]['end']
        sid=f'{num:03d}' if last==num else f'{num:03d}_{last:03d}'
        mode='generate' if actors or credits or num in {14,16,17,18,19} else 'copy'
        s=dict(id=sid,number=num,candidate_ids=list(range(num,last+1)),start=start,end=end,frames=end-start,
               start_seconds=start*1001/24000,end_seconds=end*1001/24000,cast=actors,description=desc,
               credits=credits,mode=mode,seed_A=2609230000+num*101,seed_B=2609240000+num*101,
               model_frames=p.snap_frames(end-start),a_reuse=reuse.get(num))
        s['prompt']=prompt_for(s,cast)
        shots.append(s)
    assert shots[0]['start']==0 and shots[-1]['end']==inv['frames']
    assert all(a['end']==b['start'] for a,b in zip(shots,shots[1:]))
    assert max(len(s['cast']) for s in shots)<=9
    plan=dict(schema=1,source=inv['source'],source_sha256=inv['source_sha256'],fps=inv['fps'],frames=inv['frames'],
        duration=inv['duration'],cast=cast,shots=shots,settings=dict(width=1024,height=576,steps=20,model='minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors',upscale='SeedVR2 3B FP16, full-frame 1920x1080, original fps'),
        order='Complete all A shots and assemble full A first, then generate distinct-seed B shots. Untouched original graphics share the same clip by design.',
        safety=dict(power_limit_w=400,stop_temperature_c=87,cooldown_to_c=70,guard_required=True,automatic_thermal_restart=False),
        review='User owns visual acceptance. Technical frame count, fps, dimensions, full decode and audio hash only; no per-frame aesthetic QA.',
        credits_disclosure='AI-industry names are a fictional tribute/parody, not a statement of real participation. Original song audio is unchanged.',
        cut_review='One representative source image per candidate used for planning. Merge artificial 288 boundary into blackboard shot ending301; merge Kaju blink cuts825:843 and continuous page/dissolve963:1052.',
        visual_planning_corrections='61 is Hakamada/Karen,62 is Ayano/Asagumo,63 is council trio,65 is teachers,66 is Kaju/Asami,70 is Yanami; mappings use these actual visible identities.')
    target=OUT/'full_plan.json'
    if target.exists() and json.loads(target.read_text(encoding='utf-8'))!=plan:
        raise RuntimeError('Plan changed after freezing; create a new version or explicitly review migration')
    p.write_json(target,plan)
    lines=['# 完整 OP 逐镜计划','','全片 2181 帧 / 90.965875 秒。先 A 后 B，B 采用不同种子；无须更改的背景/道具镜头共用原片。画面验收由用户进行。','',
           '主创人名为虚构致敬；原音乐未改变。Hinton 为总监督，企业负责人用于企划/制作，tibo 为编辑/RESET。','',
           '| 原人物 | AI 角色 |','|---|---|']
    lines += [f'| {c["original"]} | {k} |' for k,c in cast.items()]
    lines += ['','| 镜头 | 秒数 | 人物 | 文字替换 | A 来源 |','|---|---|---|---|---|']
    for s in shots:
        lines.append(f'| {s["id"]} | {s["start_seconds"]:.2f}–{s["end_seconds"]:.2f} | {", ".join(s["cast"]) or "—"} | {"; ".join(f"{a}: {b}" for a,b in s["credits"]) or ("系列标题" if s["number"] in {4,7,9,14,16,17,18,19} else "保留")} | {"已完成整镜/已接受文字" if s["a_reuse"] else s["mode"]} |')
    (OUT/'PLAN.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(shots=len(shots),frames=sum(s['frames'] for s in shots),new_A=sum(s['mode']=='generate' and not s['a_reuse'] for s in shots),new_B=sum(s['mode']=='generate' for s in shots),references=len(cast)),ensure_ascii=False))

if __name__=='__main__':main()
