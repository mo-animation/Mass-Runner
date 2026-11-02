"""Stand-in for phBot.exe used by tools/test_runner.py: a status segment like phBot's own (logged in after
0.5 s, level 100 or 95 for FAKE_LOW), then MoDungeon's last log line after 1.5 s; waits to be terminated."""
import ctypes
import hashlib
import os
import sys
import time
from ctypes import wintypes

args = sys.argv[1:]
char = args[args.index('--character') + 1]
k32 = ctypes.windll.kernel32
k32.CreateFileMappingW.restype = wintypes.HANDLE
k32.CreateFileMappingW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD, wintypes.LPCWSTR]
k32.MapViewOfFile.restype = ctypes.c_void_p
k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
key = 'phBot_%d' % os.getpid()
name = 'qipc_sharedmemory_' + ''.join(c for c in key if c.isascii() and c.isalpha()) + \
    hashlib.sha1(key.encode()).hexdigest()
h = k32.CreateFileMappingW(wintypes.HANDLE(-1), None, 0x04, 0, 0x790, name)
view = k32.MapViewOfFile(h, 0xF001F, 0, 0, 0x790)
put = lambda off, data: ctypes.memmove(view + off, data, len(data))
time.sleep(0.5)
put(0x037, char.encode() + b'\0')
put(0x137, bytes([95 if char in os.environ.get('FAKE_LOW', '').split(',') else 100]))
put(0x02d, b'\x01')
put(0x02f, b'\x01')
time.sleep(1.5)
with open(os.path.join('Log', 'Elysian_%s_Log.txt' % char), 'a') as f:
    if char in os.environ.get('FAKE_PLAYED', '').split(','):     # already played today: the entry NPC refuses
        f.write('[10:00:00] [MoDungeon] debug: NPC refused, code 271c\n[10:00:00] [MoDungeon] No teleport after '
                'talking to the NPC (dungeon full?) - trying again every 3s until it works (try 2).\n')
    else:
        f.write('[10:00:00] [MoDungeon] Run 1 finished (1/1).\n[10:00:00] [MoDungeon] All 1 run(s) done - stopping.\n')
time.sleep(600)
