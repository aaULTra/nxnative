"""Bounded private instruction inspection for the first MCD2 iPad crash."""
import bisect
import os
from pathlib import Path
import struct
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dm/tools'))
import nso
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcd2_cloud import DriveStore

GAME_BID = '9e9d887d59f7f7db176b35e3379258b9'


def inspect(image, text_end):
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    mod0 = struct.unpack_from('<I', image, 4)[0]
    dyn = mod0 + struct.unpack_from('<i', image, mod0 + 4)[0]
    tags = {}
    while True:
        tag, value = struct.unpack_from('<qQ', image, dyn); dyn += 16
        if tag == 0: break
        tags.setdefault(tag, value)
    sym, strings = tags[6], tags[5]
    symbols, got = {}, {}
    def string(at):
        end = image.find(b'\0', at, min(len(image), at + 2048))
        return image[at:end].decode('utf-8', 'replace') if end >= at else ''
    for at in range(sym + 24, strings, 24):
        name, info, other, section, value, size = struct.unpack_from('<IBBHQQ', image, at)
        if section and value: symbols[value] = (size, string(strings + name))
    for at in range(0, tags.get(2, 0), 24):
        off, info, addend = struct.unpack_from('<QQq', image, tags[23] + at)
        name = struct.unpack_from('<I', image, sym + (info >> 32) * 24)[0]
        got[off] = string(strings + name)
    ordered = sorted(symbols)
    def name(at):
        if not 0 <= at < len(image): return ''
        instructions = list(md.disasm(image[at:at+16], at))
        if len(instructions) == 4 and instructions[0].mnemonic == 'adrp' and instructions[1].mnemonic == 'ldr' and instructions[3].mnemonic == 'br':
            ad = instructions[0].op_str.split('#')[-1]
            dis = instructions[1].op_str.split('#')[-1].split(']')[0] if '#' in instructions[1].op_str else '0'
            slot = int(ad, 0) + int(dis, 0)
            if slot in got: return 'PLT:' + got[slot]
        k = bisect.bisect_right(ordered, at) - 1
        if k >= 0:
            base = ordered[k]; size, label = symbols[base]
            if at == base or base <= at < base + size: return label + (f'+0x{at-base:x}' if at != base else '')
        return ''
    out = [f'Exact main build {GAME_BID}', f'Text end 0x{text_end:x}',
           'Private bounded original instructions; no patch applied.']
    ranges = ((0x142c0,0x14510),(0x14800,0x14cf0),(0xb0560,0xb0b00),
              (0x1416090,0x1416300),(0x28a70,0x28b10))
    for start, stop in ranges:
        out.append(f'\nRANGE 0x{start:x}..0x{stop:x} {name(start)}')
        for ins in md.disasm(image[start:stop], start):
            note = ''
            if ins.mnemonic in ('bl','b') and ins.op_str.startswith('#'):
                note = name(int(ins.op_str[1:], 0))
            out.append(f'{ins.address:09x} {ins.bytes.hex()} {ins.mnemonic:8s} {ins.op_str} {note}')
    for table in (0x85b6ff0, 0x85b68a8):
        out.append(f'\nCANDIDATE VTABLE 0x{table:x} (not yet confirmed)')
        for slot in range(-2, 34):
            ptr = struct.unpack_from('<Q', image, table + slot*8)[0]
            out.append(f'  [{slot:2d}] 0x{ptr:x} {name(ptr)}')
            if 0 <= slot < 32 and 0 < ptr < text_end:
                for ins in md.disasm(image[ptr:ptr+32], ptr):
                    out.append(f'    {ins.address:09x} {ins.bytes.hex()} {ins.mnemonic:8s} {ins.op_str}')
    return '\n'.join(out) + '\n'


def main():
    module = nso.load(Path(sys.argv[1]).read_bytes(), 'main')
    if module.build_id[:16].hex() != GAME_BID: raise ValueError('Unexpected main build; stopping')
    report = inspect(bytes(module.image), module.text_end)
    store = DriveStore(); ident = os.environ['MCD2_INSPECTION_FILE']
    store.check(ident, {'MCD2-startup-inspection.txt'})
    # Text remains private in the user's precreated file. Never print it into public logs.
    from googleapiclient.http import MediaIoBaseUpload
    import io
    store.drive.files().update(fileId=ident, media_body=MediaIoBaseUpload(io.BytesIO(report.encode()), mimetype='text/plain'), fields='id').execute(num_retries=3)
    print(f'Private exact-build instruction report uploaded ({len(report)} characters); no patch applied')


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        from mcd2_drive_transport import safe_failure
        print(f'Inspection failed: {safe_failure(exc)}', flush=True)
        raise SystemExit(1)
