# global-metadata.dat v107 문자열 리터럴 교체
#   헤더 = (offset, size, count) 3개 묶음. 1번 묶음 = 리터럴 표(항목 4바이트 = 데이터 블록 안 시작 오프셋),
#   2번 묶음 = 리터럴 데이터(UTF-8, 길이 = 다음 오프셋 − 현재, 마지막은 블록 크기까지).
#   길이가 바뀌므로 데이터 블록을 새로 만들어 파일 끝에 붙이고, 2번 묶음의 offset·size 와 표를 다시 쓴다.
import struct

def read_literals(b: bytes):
    tbl_off, tbl_size, tbl_cnt = struct.unpack_from("<III", b, 8)
    dat_off, dat_size, _ = struct.unpack_from("<III", b, 20)
    esz = tbl_size // tbl_cnt
    assert esz == 4, esz
    offs = [struct.unpack_from("<I", b, tbl_off + 4 * i)[0] for i in range(tbl_cnt)]
    out = []
    for i, o in enumerate(offs):
        end = offs[i + 1] if i + 1 < len(offs) else dat_size
        out.append(b[dat_off + o: dat_off + end])
    return out

def patch_identifiers(b: bytes, rename: dict):
    """식별자 문자열 영역(헤더 3번 묶음, NUL 로 끝나는 UTF-8)에서 이름을 그 자리에서 바꾼다.
    새 이름이 원래보다 길면 안 된다(남는 바이트는 NUL). 열거형 멤버 이름 등. → (새 bytes, {이름: 바꾼 개수})"""
    off, size, _ = struct.unpack_from("<III", b, 32)
    out = bytearray(b); cnt = {}
    for old, new in rename.items():
        ob, nb = old.encode("utf-8"), new.encode("utf-8")
        assert len(nb) <= len(ob), (old, new)
        pat = b"\0" + ob + b"\0"
        n = 0; i = out.find(pat, off, off + size)
        while i >= 0:
            out[i + 1:i + 1 + len(ob)] = nb + b"\0" * (len(ob) - len(nb))
            n += 1; i = out.find(pat, i + 1, off + size)
        cnt[old] = n
    return bytes(out), cnt

def patch(b: bytes, rep: dict):
    """rep = {리터럴 번호: 새 문자열} → (새 파일 bytes, 바꾼 개수)"""
    assert struct.unpack_from("<I", b, 0)[0] == 0xFAB11BAF
    tbl_off, tbl_size, tbl_cnt = struct.unpack_from("<III", b, 8)
    lits = read_literals(b)
    for i, s in rep.items():
        lits[i] = s.encode("utf-8")
    blob = bytearray(); offs = []
    for x in lits:
        offs.append(len(blob)); blob += x
    out = bytearray(b)
    for i, o in enumerate(offs):
        struct.pack_into("<I", out, tbl_off + 4 * i, o)
    pad = (-len(out)) % 16
    out += b"\0" * pad
    new_off = len(out)
    out += blob
    struct.pack_into("<II", out, 20, new_off, len(blob))
    # 검증: 다시 읽으면 교체가 반영되고 나머지는 그대로
    chk = read_literals(bytes(out))
    assert len(chk) == len(lits) and all(a == c for a, c in zip(lits, chk))
    return bytes(out), len(rep)
