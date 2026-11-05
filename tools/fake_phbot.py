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
crash = char in os.environ.get('FAKE_CRASH', '').split(',') and not os.path.isfile('crash_%s.txt' % char)
if crash:                                   # the first time its client crashes at start: a crash file, no connection
    import json
    open('crash_%s.txt' % char, 'w').close()
    client = next(iter(json.load(open('vSRO.json')).values()))['path']
    os.makedirs(os.path.join(client, 'Dump'), exist_ok=True)
    open(os.path.join(client, 'Dump', '0N[%s]_00_65 _.dmp' % time.strftime('%Y-%m-%d %H-%M-%S')), 'w').close()
if crash or (char in os.environ.get('FAKE_NO_CONNECT', '').split(',') and
             not os.path.isfile('noconnect_%s.txt' % char)):
    open('noconnect_%s.txt' % char, 'w').close()    # the first time: never connects (stuck at the login)
    while not ctypes.string_at(view + 0x5e6, 1)[0]:
        time.sleep(0.1)
    sys.exit(0)
put(0x037, char.encode() + b'\0')
put(0x137, bytes([95 if char in os.environ.get('FAKE_LOW', '').split(',') else 100]))
put(0x02d, b'\x01')
put(0x02f, b'\x01')
time.sleep(1.5)
no_plugin = char in os.environ.get('FAKE_NO_PLUGIN', '').split(',') and not os.path.isfile('noplugin_%s.txt' % char)
if no_plugin:                               # the first time: in the game without MoDungeon (no line of it)
    open('noplugin_%s.txt' % char, 'w').close()
with open(os.path.join('Log', 'Elysian_%s_Log.txt' % char), 'a') as f:
    if no_plugin:
        f.write('[10:00:00] Character successfully joined the game\n')
    elif char in os.environ.get('FAKE_WAIT', '').split(',') and not os.path.isfile('wait_%s.txt' % char):
        open('wait_%s.txt' % char, 'w').close()     # the first time: stays at the entry NPC (no result of its own)
        f.write('[10:00:00] [MoDungeon] Going to the dungeon...\n')
    elif char in os.environ.get('FAKE_GATE', '').split(','):       # its partner did not come to the entry NPC
        f.write('[10:00:00] [MoDungeon] My partner did not come to the gate in 5 min - going back to town and '
                'stopping.\n')
    elif char in os.environ.get('FAKE_PLAYED', '').split(','):     # already played today: the entry NPC refuses
        f.write('[10:00:00] [MoDungeon] debug: NPC refused, code 271c\n[10:00:00] [MoDungeon] No teleport after '
                'talking to the NPC (dungeon full?) - trying again every 3s until it works (try 2).\n')
    else:
        f.write('[10:00:00] [MoDungeon] In the dungeon - fighting.\n')
        f.flush()
        time.sleep(float(os.environ.get('FAKE_DUNGEON_SECONDS') or 0))     # the dungeon itself
        f.write('[10:00:00] [MoDungeon] Run 1 finished (1/1).\n[10:00:00] [MoDungeon] All 1 run(s) done - stopping.\n')
quits = os.environ.get('FAKE_NO_QUIT', '').split(',')
for _ in range(6000):                       # phBot's graceful close flag (Mass Runner's close); FAKE_NO_QUIT ignore it
    if ctypes.string_at(view + 0x5e6, 1)[0] and char not in quits:
        with open('quit_%s.txt' % char, 'w') as f:
            f.write('quit')
        sys.exit(0)
    time.sleep(0.1)
