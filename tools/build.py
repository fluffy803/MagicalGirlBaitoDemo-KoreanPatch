# 魔法少女×幸せ××××バイト 체험판 한국어 패치 빌드
#   python tools/build.py [게임 폴더] [출력 폴더]
#     게임 폴더: 패치 전 원본 (기본값: Steam 기본 설치 경로). meta/game.json 의 해시와 맞아야 한다
#     출력 폴더: 게임 폴더에 덮어쓸 파일이 만들어진다 (기본값: dist/stage)
#   환경변수 MGB_FONTS: tools/data/fonts.json 의 한글 폰트 ttf 를 둔 폴더
#   필요: Python 3.10+, UnityPy, fonttools, pillow, pefile, capstone
import sys, os, io, json, struct, copy, hashlib
import UnityPy
from UnityPy.helpers import TypeTreeHelper
from UnityPy.helpers.TypeTreeNode import TypeTreeNode
from UnityPy.streams import EndianBinaryWriter
from PIL import Image
from fontTools.ttLib import TTFont

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import mgb_script, patch_metadata, patch_gameassembly

GAME = sys.argv[1] if len(sys.argv) > 1 else r"C:\Program Files (x86)\Steam\steamapps\common\魔法少女×幸せ××××バイト Demo"
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(REPO, "dist", "stage")
FONTS = os.environ.get("MGB_FONTS")
if not FONTS:
    sys.exit("환경변수 MGB_FONTS 에 한글 폰트 폴더를 지정할 것 (tools/data/fonts.json 참고)")
DS, DO = os.path.join(GAME, "xxxxx_Data"), os.path.join(OUT, "xxxxx_Data")
os.makedirs(os.path.join(DO, "il2cpp_data", "Metadata"), exist_ok=True)

def data(*a):
    return json.load(open(os.path.join(HERE, "data", *a), encoding="utf-8"))

# 원본 확인 (이미 패치된 파일이나 다른 빌드면 중단)
meta = json.load(open(os.path.join(REPO, "meta", "game.json"), encoding="utf-8"))
for f, h in meta["sha256"].items():
    if hashlib.sha256(open(os.path.join(GAME, f), "rb").read()).hexdigest() != h:
        sys.exit(f"원본과 다른 파일: {f} (패치가 이미 적용됐거나 게임이 업데이트됨 — Steam 무결성 검사로 원본을 받을 것)")

ks = json.load(open(os.path.join(REPO, "translations", "story.ko.json"), encoding="utf-8"))
kl = json.load(open(os.path.join(REPO, "translations", "loc.ko.json"), encoding="utf-8"))
LOGO = os.path.join(REPO, "translations", "images", "logo2.png")
TG = data("targets.json")
KF = data("fonts.json")
PT = data("patch.json")
TERM = {k[5:]: v for k, v in kl.items() if k.startswith("term/")}
ENUM_RENAME = PT["enum_rename"]
log = []
missing_inline = []

def nodes(cls):
    return TypeTreeNode.from_list(data("typetree", cls + ".json"))
TMP_ROOT, FONT_ROOT, SET_ROOT = nodes("TextMeshProUGUI"), nodes("TMP_FontAsset"), nodes("TMP_Settings")

scripts = {o.path_id: o.read().m_ClassName for o in UnityPy.load(os.path.join(DS, "globalgamemanagers.assets")).objects
           if o.type.name == "MonoScript"}
def mb_class(o):
    return scripts.get(struct.unpack_from("<q", o.get_raw_data(), 20)[0])

def read_str(raw, off):
    L = struct.unpack_from("<I", raw, off)[0]
    return raw[off + 4: off + 4 + L].decode("utf-8")

def replace_str(raw, off, new):
    L = struct.unpack_from("<I", raw, off)[0]
    end = (off + 4 + L + 3) & ~3
    b = new.encode("utf-8")
    nb = struct.pack("<I", len(b)) + b
    nb += b"\0" * ((-len(nb)) % 4)
    return raw[:off] + nb + raw[end:]

# ---------- 한글 폰트: 새 오브젝트 번호 (resources.assets 원래 최대 pathID = 25) ----------
RES_MAX_ID = 25
_next = RES_MAX_ID + 1
for f in KF["fonts"]:
    if f.get("global"):
        f.update(font=17, ftex=8, tex=9, mat=5, asset=21)   # 원래 동적 폴백 묶음을 그대로 쓴다
    else:
        f.update(font=_next, ftex=_next + 1, tex=_next + 2, mat=_next + 3, asset=_next + 4); _next += 5
LINK = {(fn, pid): (f["asset"], nm) for f in KF["fonts"] for fn, pid, nm in f["jp"]}

def prep_ttf(path):
    """TMP 폰트 엔진이 윤곽선 대신 내장 비트맵을 집으면 SDF 가 깨진다 → 비트맵 테이블을 뺀 TTF"""
    ft = TTFont(path, recalcTimestamp=False)  # 다시 빌드해도 같은 바이트가 나오게
    gone = [t for t in ("EBDT", "EBLC", "EBSC", "CBDT", "CBLC", "sbix", "bdat", "bloc") if t in ft]
    if not gone:
        return open(path, "rb").read(), ft, gone
    for t in gone:
        del ft[t]
    bio = io.BytesIO(); ft.save(bio)
    return bio.getvalue(), TTFont(io.BytesIO(bio.getvalue())), gone

# ---------- 파일별 편집 목록 ----------
edits = {}
def add_edit(f, pid, e):
    edits.setdefault(f, {}).setdefault(pid, []).append(e)
for id_, tg in TG["targets"].items():
    if tg["type"] not in ("tmp", "raw"):
        continue   # chara 는 CharaFile 을 돌며, literal 은 메타데이터에서 처리. sceneinfo 는 번역에서 원문 유지
    for t in [tg] + tg.get("also", []):
        if t["type"] == "tmp":
            add_edit(t["file"], t["path_id"], ("tmp", kl[id_]))
        else:
            add_edit(t["file"], t["path_id"], ("raw", t["off"], kl[id_]))
for d in TG["derived"]:   # 연동 용어에서 나오는 씬 문구: 수치 라벨, 컷인 큰 글씨, 백로그 태그 키
    text = TERM[d["term"]] + d["suffix"]
    add_edit(d["file"], d["path_id"], ("tmp", text) if d["type"] == "tmp" else ("raw", d["off"], text))

def chara_off(raw):   # CharaFile: m_Name 뒤 int 다음 문자열 = 표시 이름
    L = struct.unpack_from("<I", raw, 28)[0]
    return ((32 + L + 3) & ~3) + 4

# ---------- 시나리오 ----------
def build_script(name, raw_bytes):
    lines = mgb_script.split_lines(raw_bytes)
    inl = mgb_script.parse_inline_choice_prompts(lines)   # 「@choice|질문」 같은 줄 질문
    ko_inl = {i: ks.get(f"{name}/cq{i + 1:04d}") for i, _ in inl}
    missing_inline.extend(f"{name}/cq{i + 1:04d}" for i, k in ko_inl.items() if k is None)
    lines = mgb_script.apply_inline_choice_prompts(lines, ko_inl)
    lh = mgb_script.parse_inline_log_heads(lines)   # 여러 줄 로그의 첫 줄이 명령과 같은 줄에 있는 경우
    ko_lh = {i: ks.get(f"{name}/lh{i + 1:04d}") for i, _ in lh}
    missing_inline.extend(f"{name}/lh{i + 1:04d}" for i, k in ko_lh.items() if k is None)
    lines = mgb_script.apply_inline_log_heads(lines, ko_lh)
    units = mgb_script.parse(lines)
    ko = [ks[f"{name}/{n:04d}"] for n in range(1, len(units) + 1)]
    out = mgb_script.apply(lines, units, ko)
    n_sp = 0
    for i, l in enumerate(out):
        # @param 키는 일본어 그대로: 코드가 키를 첫 글자 문자 코드로 판별하는 switch 라 한국어 키는 전부 기본값(부하)이 된다
        if mgb_script.SAY_HEAD.match(l):   # 화자 칸 = CharaName 열거형 이름 → 메타데이터에서 바꾼 이름과 같게
            p = l.split("|")
            if p[0] in ENUM_RENAME:
                p[0] = ENUM_RENAME[p[0]]; out[i] = "|".join(p); n_sp += 1
    assert [u["text"] for u in mgb_script.parse(out)] == ko, name
    log.append(f"{name}: 문구 {len(units)}, 같은 줄 선택지 질문 {sum(1 for k in ko_inl.values() if k)}/{len(inl)}, 화자 칸 {n_sp}")
    return mgb_script.join_lines(out)

def save(env, name):
    b = list(env.files.values())[0].save()
    open(os.path.join(DO, name), "wb").write(b)
    log.append(f"저장 {name} ({len(b):,} B)")

# ---------- sharedassets0 / sharedassets1 / level0 / level1 ----------
for fname in ["sharedassets0.assets", "sharedassets1.assets", "level0", "level1"]:
    env = UnityPy.load(os.path.join(DS, fname))
    n_tmp = n_raw = 0
    for o in env.objects:
        t = o.type.name
        if t == "TextAsset":
            d = o.read()
            if d.m_Name.startswith("Y_") or d.m_Name == "GameOver":
                raw = d.m_Script.encode("utf-8", "surrogateescape") if isinstance(d.m_Script, str) else bytes(d.m_Script)
                d.m_Script = build_script(d.m_Name, raw).decode("utf-8")
                d.save()
        elif t == "Texture2D" and fname == "sharedassets0.assets":
            d = o.read()
            if d.m_Name == "logo2":
                img = Image.open(LOGO).convert("RGBA")
                assert img.size == (d.m_Width, d.m_Height), img.size
                d.image = img
                d.save()
                log.append(f"logo2 교체 {img.size}")
        elif t == "MonoBehaviour":
            cls = mb_class(o)
            if cls == "TMP_FontAsset":
                if (fname, o.path_id) in LINK:   # 일본어 폰트의 폴백 표 → 짝 한글 폰트(resources.assets)
                    ko_pid, jp_name = LINK[(fname, o.path_id)]
                    ft = o.read_typetree(FONT_ROOT, check_read=True)
                    assert ft["m_Name"] == jp_name, (ft["m_Name"], jp_name)
                    sf = list(env.files.values())[0]
                    paths = [e.path for e in sf.externals]
                    if "resources.assets" not in paths:   # sharedassets0 에는 없다 → sharedassets1 의 참조 항목을 복사
                        sf1 = list(UnityPy.load(os.path.join(DS, "sharedassets1.assets")).files.values())[0]
                        sf.externals.append(copy.copy(next(e for e in sf1.externals if e.path == "resources.assets")))
                        paths = [e.path for e in sf.externals]
                    ft["m_FallbackFontAssetTable"] = [{"m_FileID": paths.index("resources.assets") + 1, "m_PathID": ko_pid}]
                    o.save_typetree(ft, FONT_ROOT)
                continue
            if cls == "CharaFile":
                raw = o.get_raw_data()
                id_ = "chara/" + read_str(raw, 28)
                if id_ in kl and read_str(raw, chara_off(raw)) != kl[id_]:
                    o.set_raw_data(replace_str(raw, chara_off(raw), kl[id_])); n_raw += 1
            es = edits.get(fname, {}).get(o.path_id)
            if not es:
                continue
            tmp_e = [e for e in es if e[0] == "tmp"]
            raw_e = sorted([e for e in es if e[0] == "raw"], key=lambda e: -e[1])
            if tmp_e:
                assert len(tmp_e) == 1, (fname, o.path_id, tmp_e)
                tt = o.read_typetree(TMP_ROOT, check_read=True)
                if tt["m_text"].replace("\r\n", "\n") != tmp_e[0][1]:
                    tt["m_text"] = tmp_e[0][1]
                    o.save_typetree(tt, TMP_ROOT); n_tmp += 1
            if raw_e:
                raw = o.get_raw_data(); ch = 0
                for _, off, text in raw_e:
                    if read_str(raw, off) != text:
                        raw = replace_str(raw, off, text); ch += 1
                if ch:
                    o.set_raw_data(raw); n_raw += ch
    for fx in PT["layout_fixes"]:   # 한글이 길어져 겹치는 텍스트의 위치 보정
        if fx["file"] != fname:
            continue
        objs_ = {o.path_id: o for o in env.objects}
        go = objs_[fx["tmp"]].read_typetree(TMP_ROOT)["m_GameObject"]["m_PathID"]
        rto = next(o for o in env.objects if o.type.name == "RectTransform" and o.read_typetree()["m_GameObject"]["m_PathID"] == go)
        rt = rto.read_typetree()
        rt["m_AnchoredPosition"]["x"] += fx["dx"]; rt["m_AnchoredPosition"]["y"] += fx["dy"]
        rto.save_typetree(rt)
    log.append(f"{fname}: TMP {n_tmp}, 문자열 {n_raw}")
    save(env, fname)

# ---------- resources.assets: 일본어 폰트마다 짝 한글 동적 폰트 ----------
# 외곽선·두께 효과는 (패딩 ÷ 샘플링 크기 × scale)에 비례 → 짝 일본어 폰트와 같은 비율로 샘플링한다.
# global 은 원래 동적 폴백 묶음(Font#17·Texture#8·Atlas#9·Material#5·FontAsset#21)을 재사용, 나머지는 복제해 새 pathID 로 추가.
env = UnityPy.load(os.path.join(DS, "resources.assets"))
sf_res = list(env.files.values())[0]
objs = {o.path_id: o for o in env.objects}
assert max(objs) == RES_MAX_ID, max(objs)
AT = 2048
src_font = objs[17].read_typetree()
src_mat = objs[5].read_typetree()
src_fa = objs[21].read_typetree(FONT_ROOT, check_read=True)

def tt_bytes(src, tree, nd=None):
    w = EndianBinaryWriter(endian=src.reader.endian)
    TypeTreeHelper.write_typetree(tree, src._get_typetree_node(nd), w, src.assets_file)
    return w.bytes

def put(src_pid, new_pid, b):
    if new_pid == src_pid:
        objs[src_pid].set_raw_data(b); return
    o = copy.copy(objs[src_pid]); o.path_id = new_pid; o.set_raw_data(b)
    sf_res.objects[new_pid] = o; objs[new_pid] = o

for f in KF["fonts"]:
    ttf_bytes, ft, gone = prep_ttf(os.path.join(FONTS, f["ttf"]))
    upm = ft["head"].unitsPerEm; hh = ft["hhea"]; os2 = ft["OS/2"]; post = ft["post"]
    fam = ft["name"].getDebugName(1)
    P, PAD, GS = f["pt"], f["pad"], f["gs"]; s = P / upm
    fo = copy.deepcopy(src_font)
    fo.update(m_Name="KO_" + f["key"], m_FontData=list(ttf_bytes), m_FontNames=[fam], m_KerningValues=[],
              m_Texture={"m_FileID": 0, "m_PathID": f["ftex"]})
    put(17, f["font"], tt_bytes(objs[17], fo))
    if f["ftex"] != 8:
        put(8, f["ftex"], objs[8].get_raw_data())   # 레거시 Font Texture(0x0)
    if f["tex"] != 9:
        put(9, f["tex"], objs[9].get_raw_data())    # 1x1 자리표시자(런타임에 AT² 로 다시 만들어짐)
    mt = copy.deepcopy(src_mat)
    mt["m_Name"] = "KO_" + f["key"] + " SDF Material"
    mt["m_SavedProperties"]["m_TexEnvs"] = [(k, dict(v, m_Texture={"m_FileID": 0, "m_PathID": f["tex"]}) if k == "_MainTex" else v)
                                           for k, v in mt["m_SavedProperties"]["m_TexEnvs"]]
    fl = mt["m_SavedProperties"]["m_Floats"]
    for i, kv in enumerate(fl):
        if kv[0] in ("_TextureWidth", "_TextureHeight"):
            fl[i] = (kv[0], float(AT))
        elif kv[0] == "_GradientScale":
            fl[i] = (kv[0], float(GS))
    put(5, f["mat"], tt_bytes(objs[5], mt))
    tt = copy.deepcopy(src_fa)
    tt["m_Name"] = "KO_" + f["key"] + " SDF"
    tt["m_FaceInfo"].update({"m_FamilyName": fam, "m_StyleName": "Regular", "m_PointSize": float(P), "m_Scale": 1.0, "m_UnitsPerEM": upm,
               "m_LineHeight": (hh.ascent - hh.descent + hh.lineGap) * s, "m_AscentLine": hh.ascent * s,
               "m_CapLine": os2.sCapHeight * s, "m_MeanLine": os2.sxHeight * s, "m_Baseline": 0.0,
               "m_DescentLine": hh.descent * s, "m_SuperscriptOffset": hh.ascent * s, "m_SubscriptOffset": hh.descent * s,
               "m_UnderlineOffset": post.underlinePosition * s, "m_UnderlineThickness": post.underlineThickness * s,
               "m_StrikethroughOffset": os2.sxHeight * s / 2, "m_StrikethroughThickness": post.underlineThickness * s,
               "m_TabWidth": ft["hmtx"][ft.getBestCmap()[32]][0] * s})
    tt["m_FaceInfo"]["m_Scale"] = float(f.get("scale", 1.0))
    tt["m_CreationSettings"].update({"pointSize": P, "padding": PAD, "atlasWidth": AT, "atlasHeight": AT, "renderMode": 4169})
    tt.update(m_SourceFontFile={"m_FileID": 0, "m_PathID": f["font"]}, m_Material={"m_FileID": 0, "m_PathID": f["mat"]},
              m_AtlasTextures=[{"m_FileID": 0, "m_PathID": f["tex"]}], m_AtlasPopulationMode=1, m_IsMultiAtlasTexturesEnabled=1,
              m_ClearDynamicDataOnBuild=1, m_AtlasRenderMode=4169, m_AtlasTextureIndex=0, m_AtlasWidth=AT, m_AtlasHeight=AT,
              m_AtlasPadding=PAD, m_FreeGlyphRects=[{"m_X": 0, "m_Y": 0, "m_Width": AT - 1, "m_Height": AT - 1}],
              m_UsedGlyphRects=[], m_GlyphTable=[], m_CharacterTable=[], m_FallbackFontAssetTable=[])
    for k, v in tt["m_FontFeatureTable"].items():   # LiberationSans 글리프 번호 기준 커닝 표는 비운다
        if isinstance(v, list):
            tt["m_FontFeatureTable"][k] = []
    put(21, f["asset"], tt_bytes(objs[21], tt, FONT_ROOT))
    log.append(f"폰트 {f['key']} #{f['asset']} {P}pt/패딩{PAD}/GS{GS}/x{f.get('scale', 1.0)}" + (f" (비트맵 테이블 제거 {gone})" if gone else ""))
st_o = objs[25]
st = st_o.read_typetree(SET_ROOT, check_read=True)
st["m_fallbackFontAssets"] = [{"m_FileID": 0, "m_PathID": 21}]
st["m_UseModernHangulLineBreakingRules"] = 1
st_o.save_typetree(st, SET_ROOT)
save(env, "resources.assets")

# ---------- global-metadata.dat: 코드 문자열 ----------
mpath = os.path.join(DS, "il2cpp_data", "Metadata", "global-metadata.dat")
mbytes = open(mpath, "rb").read()
lits = [x.decode("utf-8", "replace") for x in patch_metadata.read_literals(mbytes)]
rep = {}
for id_, tg in TG["targets"].items():
    if tg["type"] == "literal" and lits[tg["index"]] != kl[id_]:
        rep[tg["index"]] = kl[id_]
# 수치 이름: 원래 리터럴 4개는 파싱 키라 그대로, 디버그 메시지로만 쓰이는 리터럴 4개를 빌려 한국어 이름을 넣는다(표시 함수만 그쪽을 읽게 아래에서 고침)
for p in PT["param_display"]:
    assert lits[p["borrow_literal"]].startswith(p["borrow_head"]), p
    rep[p["borrow_literal"]] = TERM[p["term"]]
rep[lits.index("【劇薬行動】")] = "【" + TERM["劇薬行動"] + "】"
for i, s_ in enumerate(lits):   # 엔딩 기록 키 「ユメ:5:Holic」 등 → 바꾼 열거형 이름에 맞춘다
    for old, new in ENUM_RENAME.items():
        if s_.startswith(old + ":"):
            rep[i] = new + s_[len(old):]
mbytes, n = patch_metadata.patch(mbytes, rep)
mbytes, ren = patch_metadata.patch_identifiers(mbytes, ENUM_RENAME)
assert all(v == 1 for v in ren.values()), ren
open(os.path.join(DO, "il2cpp_data", "Metadata", "global-metadata.dat"), "wb").write(mbytes)
log.append(f"global-metadata.dat: 리터럴 {n}, 열거형 이름 {len(ren)}")

# ---------- GameAssembly.dll: 수치 이름 표시 함수만 빌린 슬롯을 읽게 ----------
dll = bytearray(open(os.path.join(GAME, "GameAssembly.dll"), "rb").read())
slot_map = {int(p["slot"], 16): int(p["borrow_slot"], 16) for p in PT["param_display"]}
for m in PT["gameassembly_methods"]:
    done = patch_gameassembly.patch_slots(dll, (int(m["start"], 16), int(m["end"], 16)), slot_map)
    assert len(done) == m["expect"], (m, done)
    log.append(f"GameAssembly: {m['name']} {len(done)}곳")
open(os.path.join(OUT, "GameAssembly.dll"), "wb").write(dll)
if missing_inline:
    log.append(f"※ 번역 없는 같은 줄 문구(선택지 질문·로그 첫 줄) {len(missing_inline)}개(원문 유지): {', '.join(missing_inline)}")
print("\n".join(log))
