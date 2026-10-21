# GameAssembly.dll: 지정한 함수 범위 안의 RIP 상대 참조(문자열 리터럴 슬롯) 주소만 다른 슬롯으로 바꾼다.
# 명령어 길이·구조는 그대로 두고 disp32 4바이트만 고친다.
import struct
import pefile, capstone


def patch_slots(dll: bytearray, rng, mapping):
    """rng=(시작, 끝) RVA, mapping={옛 슬롯 RVA: 새 슬롯 RVA} → [(명령 RVA, 옛, 새)]"""
    pe = pefile.PE(data=bytes(dll), fast_load=True)
    start, end = rng
    off = pe.get_offset_from_rva(start)
    code = bytes(dll[off: off + (end - start)])
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    done = []
    for ins in md.disasm(code, start):
        for op in ins.operands:
            if op.type == capstone.x86.X86_OP_MEM and op.mem.base == capstone.x86.X86_REG_RIP:
                tgt = ins.address + ins.size + op.mem.disp
                if tgt in mapping:
                    new = mapping[tgt]
                    fo = pe.get_offset_from_rva(ins.address) + ins.disp_offset
                    assert ins.disp_size == 4 and struct.unpack_from("<i", dll, fo)[0] == op.mem.disp
                    struct.pack_into("<i", dll, fo, new - (ins.address + ins.size))
                    done.append((ins.address, tgt, new))
    return done
