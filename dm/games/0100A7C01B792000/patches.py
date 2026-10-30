# Minecraft Dungeons II v65536: verified on the exact main NSO below.
# nninitStartup allocates the 4064 MiB engine region, then constructs
# FMallocBinned3 in place at main+0x9979228. Its constructor at +0xb0560
# requests 0x980000000 (38 GiB), overflowing that region. The out-of-memory
# path crashes while reading a null object at +0x141619c. Keep the engine
# region, omit the incompatible pool constructor, and use the existing host
# allocator installed before guest startup by dm/host/hle_uemalloc.c.
# Original BL, constructor stores, derived/base vtables and TLS Malloc/Free
# dispatch verified privately in Actions run 40977368275 (2026-10-30).
CUTS = {
    '9e9d887d59f7f7db176b35e3379258b9': [
        (0x143b0, '6c700294', '1f2003d5',
         'nninitStartup: omit verified FMallocBinned3 constructor (38 GiB pools); hle_uemalloc installs the host allocator in the fixed object'),
    ],
}

GENERALIZE = ('Use the existing Unreal host allocator mapping for this verified UE5.6 build; '
              'constructor/object/vtable discovery could eventually replace per-build addresses. '
              'Do not reuse these offsets for another module build.')
