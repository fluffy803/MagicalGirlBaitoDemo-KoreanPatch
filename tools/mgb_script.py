# 시나리오 TextAsset(Y_*.txt, GameOver.txt) 파서 — 화면 문구만 단위로 뽑고, 번역문을 같은 자리에 되돌려 넣는다
#   파일은 UTF-8 + CRLF. 블록 끝은 "|||||".
#   단위 kind: say / log:INFO|ACT|COMMENT / day / choice:q|opt|why / confirm:q|opt
#   키 칸(화자·표정·@param·라벨·플래그·lock 값)은 단위에 넣지 않는다.
import re

SAY_HEAD = re.compile(r"^[^|@]+\|-?\d+\|\s*$")
END = "|||||"


def split_lines(raw: bytes):
    t = raw.decode("utf-8")
    lines = t.split("\r\n")
    assert all("\n" not in l for l in lines), "CRLF 아닌 줄바꿈이 섞여 있음"
    return lines


def join_lines(lines) -> bytes:
    return "\r\n".join(lines).encode("utf-8")


def parse(lines):
    """[{kind, text, speaker, face, loc}] — loc 은 apply 가 쓰는 위치 정보"""
    units = []
    i = 0
    n = len(lines)
    while i < n:
        l = lines[i]
        if l.startswith("@log|"):
            p = l.split("|")
            if l.endswith(END) and len(p) > 2 and p[2]:
                units.append({"kind": "log:" + p[1], "text": p[2], "loc": ("field", i, 2)})
                i += 1
                continue
            start = i + 1
            j = start
            while j < n and not lines[j].endswith(END):
                j += 1
            tail = lines[j][:-len(END)]
            buf = lines[start:j] + ([tail] if tail else [])
            units.append({"kind": "log:" + p[1], "text": "\n".join(buf), "loc": ("mlines", start, j, bool(tail))})
            i = j + 1
            continue
        if l.startswith("@day|"):
            units.append({"kind": "day", "text": l.split("|")[1], "loc": ("field", i, 1)})
            i += 1
            continue
        if l.startswith("@choice|") or l.startswith("@confirm|"):
            kind = l[1:].split("|")[0]
            if kind == "choice":
                i += 1
                # 질문 줄은 없을 수도 있다(바로 "|선택지|라벨" 줄로 시작)
                if lines[i].startswith("|"):
                    i -= 1
                elif lines[i]:
                    units.append({"kind": "choice:q", "text": lines[i], "loc": ("line", i)})
            else:
                q = l.split("|", 1)[1]
                if q:
                    units.append({"kind": "confirm:q", "text": q, "loc": ("field", i, 1)})
            i += 1
            while i < n and lines[i] != END:
                p = lines[i].split("|")
                if len(p) > 1 and p[1]:
                    units.append({"kind": kind + ":opt", "text": p[1], "loc": ("field", i, 1)})
                for k, x in enumerate(p):
                    if x.startswith("why="):
                        units.append({"kind": kind + ":why", "text": x[4:], "loc": ("why", i, k)})
                i += 1
            i += 1
            continue
        if l.startswith("@"):
            i += 1
            continue
        if SAY_HEAD.match(l):
            sp, face = l.split("|")[:2]
            start = i + 1
            j = start
            while j < n and lines[j] != END:
                j += 1
            units.append({"kind": "say", "text": "\n".join(lines[start:j]), "speaker": sp, "face": int(face),
                          "loc": ("lines", start, j)})
            i = j + 1
            continue
        i += 1
    return units


def parse_inline_choice_prompts(lines):
    """「@choice|질문」처럼 질문이 명령과 같은 줄에 있는 경우(10개). parse() 의 순번과 섞이지 않게 따로 뽑는다.
    → [(줄 번호 0-base, 질문)]. id 는 '<파일>/cq<줄번호 1-base 4자리>'"""
    out = []
    for i, l in enumerate(lines):
        if l.startswith("@choice|"):
            q = l.split("|", 1)[1]
            if q and not q.endswith(END):
                out.append((i, q))
    return out


def apply_inline_choice_prompts(lines, ko_by_line):
    out = list(lines)
    for i, ko in ko_by_line.items():
        if ko is None:
            continue
        if "|" in ko or "\n" in ko:
            raise ValueError(f"선택지 질문에 | 또는 줄바꿈: {ko!r}")
        assert out[i].startswith("@choice|")
        out[i] = "@choice|" + ko
    return out


def parse_inline_log_heads(lines):
    """여러 줄 @log 의 첫 줄이 명령과 같은 줄에 있는 경우(「@log|COMMENT|첫 줄」 다음 줄부터 이어지고 |||||로 끝남, 2개).
    parse() 는 다음 줄부터만 잡으므로 따로 뽑는다 → [(줄 번호 0-base, 첫 줄)]. id 는 '<파일>/lh<줄번호 1-base 4자리>'"""
    out = []
    for i, l in enumerate(lines):
        if l.startswith("@log|") and not l.endswith(END):
            p = l.split("|")
            if len(p) > 2 and p[2]:
                out.append((i, p[2]))
    return out


def apply_inline_log_heads(lines, ko_by_line):
    out = list(lines)
    for i, ko in ko_by_line.items():
        if ko is None:
            continue
        if "|" in ko or "\n" in ko:
            raise ValueError(f"로그 첫 줄에 | 또는 줄바꿈: {ko!r}")
        p = out[i].split("|")
        assert p[0] == "@log" and len(p) == 3, out[i]
        p[2] = ko; out[i] = "|".join(p)
    return out


def apply(lines, units, ko_list):
    """units[k] 자리에 ko_list[k] 를 넣은 새 줄 목록. ko 가 None 이면 그대로 둔다."""
    out = list(lines)
    edits = sorted(zip(units, ko_list), key=lambda x: x[0]["loc"][1], reverse=True)
    for u, ko in edits:
        if ko is None:
            continue
        if "|" in ko:
            raise ValueError(f"번역문에 반각 | 가 있음: {ko!r}")
        loc = u["loc"]
        if loc[0] in ("field", "why", "line") and "\n" in ko:
            raise ValueError(f"한 줄 칸에 줄바꿈: {ko!r}")
        if loc[0] == "field":
            p = out[loc[1]].split("|"); p[loc[2]] = ko; out[loc[1]] = "|".join(p)
        elif loc[0] == "why":
            p = out[loc[1]].split("|"); p[loc[2]] = "why=" + ko; out[loc[1]] = "|".join(p)
        elif loc[0] == "line":
            out[loc[1]] = ko
        elif loc[0] == "lines":
            out[loc[1]:loc[2]] = ko.split("\n")
        elif loc[0] == "mlines":
            start, j, tail = loc[1], loc[2], loc[3]
            new = ko.split("\n")
            if tail:
                out[start:j + 1] = new[:-1] + [new[-1] + END]
            else:
                out[start:j] = new
    return out
