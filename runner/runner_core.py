"""Mass Runner core: plays an event (a dungeon, ...) with the characters MassLeveler Escort finished.

The characters come from MassLeveler Escort's accounts.csv + the Excel sheet of accounts added by hand. Only the ones in the finished list
MassLeveler Escort writes to OneDrive (MassLeveler/finished.json) are opened, at most max_open at a time: a
character that is done is closed (phBot terminated) and the next one opens in its place.

Event = what every character plays, picked from settings.json "events". type MoDungeon: the character's
Config/MoDungeon_<char>.json gets the event's MoDungeon profile + Auto start, MoDungeon starts by itself in the
game and the runner reads phBot's log (Log/<server>_<char>_Log.txt) to know when it is done: the last
"All N run(s) done" line comes after the pet's items went to the inventory.

An event with party_size 2 (the Maze: its entry NPC needs a party) opens the characters two by two: the first of a pair
invites the second in phBot, the second accepts only from it; MoDungeon's "Party of 2" keeps them together inside.

Every character gets a phBot profile from its build (templates/<build>.json + the last skill bracket of
data/builds/<build>.json): Auto Mastery masteries + skills of the build, then the job mastery + job skills.
"""
import copy
import csv
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, 'frozen', False) else \
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_SETTINGS = {
    'phbot_dir': 'D:/Games/silkroad/bots/phBot Runner',
    'phbot_dirs': ['D:/Games/silkroad/bots/phBot Runner'],     # the first one that exists (folder copied between PCs)
    'phbot_command': ['phBot.exe'],
    'server': 'Elysian',
    'login_args': [],                   # [] = --locale private-server --privateserver <key> from vSRO.json
    'extra_args': ['--skipupdates', '--relogdc'],
    'mode': 'client',                   # client / clientless (the server kicked clientless characters)
    'hide_client': True,
    'minimize_bots': True,
    'accounts_csv': 'accounts.csv',     # + MassLeveler Escort's accounts.csv (found by itself)
    'extra_accounts': 'extra_accounts.xlsx',    # Excel sheet of accounts added by hand (Accounts button)
    'passcode_digits': 8,
    'default_build': 'ch_nuker',        # accounts.csv column "build" overrides it
    'builds_dir': 'data/builds',
    'templates_dir': 'templates',
    'pick_template': 'templates/pick_filter.db3',
    'job_mastery': 'Job Skills',        # phBot Auto Mastery levels this one up too ('' = no)
    'job_skills': ['Job - Trader Pouch'],
    'finished_file': '',                # '' = <OneDrive>/MassLeveler/finished.json (MassLeveler Escort writes it)
    # MassLeveler Escort folders on THIS PC: their data/progress.json (done / finished) counts as finished too
    # ('' entries skipped; the "MassLeveler Escort" folder next to Mass Runner is always looked at)
    'massleveler_dirs': [],
    'only_finished': True,              # open only characters in the finished list
    'min_level': 100,                   # a character below it is closed again ("not ready")
    'max_open': 7,
    'launch_delay_seconds': 20,
    'login_timeout_minutes': 4,
    'login_restarts': 10,               # a bot stuck at the login is restarted this often, then failed
    'start_fail_wait_minutes': 15,      # a bot whose game client never came up: no new bot for this long
    'max_restarts': 3,                  # a bot that closes by itself / never gets in the game is opened again
    'close_delay_seconds': 5,           # done: wait this long, then terminate the bot
    'event': 'Isis Dungeon',
    'events': [
        {'name': 'Isis Dungeon', 'type': 'MoDungeon', 'profile': 'Isis', 'runs': 1, 'timeout_minutes': 45,
         'party_size': 1},
    ],
    'runs_dir': 'data/runs',            # <event>.json: the result of every character in the event's run
    'skip': 'data/skip.json',           # characters unticked in the dashboard
}

EVENT_TYPES = ('MoDungeon',)

PLUGIN_WAIT = 45            # seconds in the game without a single MoDungeon log line = it did not load
ENTERED_LINE = '[MoDungeon] In the dungeon - fighting'   # from here on a character of a pair goes on alone if needed
LOCK_RECHECK = 30           # seconds between two looks at locked game files
NO_ENTRIES_WAIT = 90        # after "No more entries" MoDungeon uses a return scroll and empties the pet
# MoDungeon log lines (plugin v4.14) -> result, note, seconds before the bot is closed (None = close_delay_seconds)
MODUNGEON_LINES = [
    (re.compile(r'\[MoDungeon\] All \d+ run\(s\) done'), 'done', '', None),
    (re.compile(r'\[MoDungeon\] No more entries'), 'no entries', 'entry limit reached', NO_ENTRIES_WAIT),
    # the entry NPC refuses with 271c when the character already played the dungeon today (Elysian logs: every
    # 271c came after a finished run the same day; 291c = full for now, it got in later). MoDungeon only retries.
    (re.compile(r'\[MoDungeon\] debug: NPC refused, code 271c'), 'no entries', 'already played today (NPC code 271c)',
     None),
    # Party of 2: the partner did not come to the entry NPC in time - MoDungeon goes back to town and stops
    (re.compile(r'\[MoDungeon\] My partner did not come to the gate'), 'failed', 'partner did not come to the gate',
     NO_ENTRIES_WAIT),
    (re.compile(r'\[MoDungeon\] Character died - stopped'), 'failed', 'died', None),
    (re.compile(r'\[MoDungeon\] Record all 3 columns first'), 'failed', 'the MoDungeon profile has no route', None),
    (re.compile(r'\[MoDungeon\] Did not get into the dungeon'), 'failed', 'did not get into the dungeon', None),
    (re.compile(r'\[MoDungeon\] Profile "(.*)" not found'), 'failed', 'MoDungeon profile not found', None),
    (re.compile(r'\[MoDungeon\] Error: (.*)'), 'failed', 'MoDungeon error', None),
]


# ---------------------------------------------------------------- files
def path_in_root(p):
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def read_json(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)    # a bot is reading the file right now (Windows)
    os.replace(tmp, path)


def load_settings(path=None):
    path = path or path_in_root('settings.json')
    s = copy.deepcopy(DEFAULT_SETTINGS)
    s.update(read_json(path, {}) or {})
    if not os.path.isdir(s['phbot_dir']):
        s['phbot_dir'] = next((p for p in s.get('phbot_dirs') or [] if os.path.isdir(p)), s['phbot_dir'])
    return s


def finished_path(s):
    if s.get('finished_file'):
        return path_in_root(s['finished_file'])
    base = os.environ.get('OneDrive') or os.path.join(os.path.expanduser('~'), 'OneDrive')
    return os.path.join(base, 'MassLeveler', 'finished.json')


def local_massleveler_dirs(s):
    dirs = [path_in_root(d) for d in s.get('massleveler_dirs') or [] if d]
    if s.get('find_massleveler', True):
        dirs.append(os.path.join(os.path.dirname(ROOT), 'MassLeveler Escort'))
        dirs.append(os.path.join(os.path.dirname(os.path.dirname(ROOT)), 'MassLeveler Escort'))  # mass runner\Mass Runner
    out = []
    for d in dirs:
        if (os.path.isfile(os.path.join(d, 'data', 'progress.json')) or os.path.isfile(os.path.join(d, 'accounts.csv'))) \
                and os.path.normcase(os.path.abspath(d)) not in [os.path.normcase(os.path.abspath(x)) for x in out]:
            out.append(d)
    return out


def event_key(name):
    return re.sub(r'[^A-Za-z0-9_-]+', '_', name).strip('_') or 'event'


def modungeon_profiles(s):
    """MoDungeon's route profiles in the runner's phBot: Default + Config/MoDungeon.<name>.json"""
    cfg = os.path.join(s['phbot_dir'], 'Config')
    names = []
    try:
        for f in os.listdir(cfg):
            if f.startswith('MoDungeon.') and f.endswith('.json'):
                names.append(f[len('MoDungeon.'):-5])
    except OSError:
        pass
    return ['Default'] + sorted((n for n in names if n.lower() != 'default'), key=str.lower)


# ---------------------------------------------------------------- accounts
class Char:
    def __init__(self, username, password, passcode, character, build):
        self.username = username
        self.password = password
        self.passcode = passcode
        self.character = character
        self.build = build
        self.skip = False
        self.always = False         # accounts.csv column "finished" = yes: plays without the finished list
        self.proc = None
        self.queued = False         # waits for a free place to open
        self.launched_at = 0.0
        self.in_game_at = 0.0
        self.restarts = 0
        self.log_pos = 0            # phBot log size when it opened: only newer lines count
        self.finish = None          # (result, note, close at)
        self.plugin_seen = False    # a MoDungeon line came in its log since it opened
        self.force_open = False     # Restart bot: opens even while the runner is stopped
        self.login_restarts = 0     # restarted because it got stuck at the login
        self.connected_once = False # phBot reached the game server since it opened
        self.partner = None         # party_size 2: the character it plays with
        self.leader = False         # the first of its pair: invites the partner
        self.entered = False        # MoDungeon said it is in the dungeon since it opened
        self.held = False           # no entries (played today) but its partner plays: waits in town for it
        self.hold_note = ''
        self.hold_min = 0.0         # not closed before this (MoDungeon's return scroll + pet to bag)
        self.state = ''
        self.level = 0


def party_size(ev):
    return 2 if int((ev or {}).get('party_size') or 1) >= 2 else 1


def read_csv_rows(path):
    if not os.path.isfile(path):
        return []
    with open(path, newline='', encoding='utf-8-sig') as f:
        rows = []
        for r in csv.DictReader(f):
            r = {(k or '').strip().lower(): (v or '').strip() for k, v in r.items()}
            if r.get('username') and not r['username'].startswith('#'):
                rows.append(r)
        return rows


EXTRA_HEADER = ['username', 'password', 'passcode', 'character', 'build']


def _cell(v):
    if isinstance(v, float) and v.is_integer():
        v = int(v)                  # Excel keeps a number passcode as 1234567.0
    return '' if v is None else str(v).strip()


def read_xlsx_rows(path):
    if not os.path.isfile(path):
        return []
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        it = wb.worksheets[0].iter_rows(values_only=True)
        head = [_cell(h).lower() for h in next(it, ())]
        rows = []
        for vals in it:
            r = {k: _cell(v) for k, v in zip(head, vals) if k}
            if r.get('username') and not r['username'].startswith('#'):
                rows.append(r)
        return rows
    finally:
        wb.close()


def extra_path(s):
    return path_in_root(s.get('extra_accounts') or 'extra_accounts.xlsx')


def make_extra_sheet(path):
    """the Excel sheet for accounts added by hand (empty, only the header)"""
    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'accounts'
    ws.append(EXTRA_HEADER)
    for i, w in enumerate((18, 18, 12, 18, 14)):
        ws.cell(1, i + 1).font = Font(bold=True)
        ws.column_dimensions['ABCDE'[i]].width = w
    for col in 'ABCD':              # text, so Excel keeps the passcode's leading zeros
        for row in range(2, 500):
            ws['%s%d' % (col, row)].number_format = '@'
    wb.save(path)


def account_rows(s):
    """(row, added by hand): MassLeveler Escort's accounts.csv (found by itself), this folder's accounts.csv if any,
    then the Excel sheet of accounts added by hand (extra_accounts.xlsx); a character is taken once, the sheet wins"""
    rows = {}
    files = [path_in_root(s['accounts_csv'])] + [os.path.join(d, 'accounts.csv') for d in local_massleveler_dirs(s)]
    for f in files:
        for r in read_csv_rows(f):
            if r.get('character'):
                rows.setdefault(r['character'].lower(), (r, False))
    path = extra_path(s)
    for r in read_csv_rows(path) if path.lower().endswith('.csv') else read_xlsx_rows(path):
        if r.get('character'):
            rows[r['character'].lower()] = (r, True)
    return list(rows.values())


def load_chars(s, skip):
    out = []
    yes = ('yes', 'y', '1', 'true', 'x')
    for r, added in account_rows(s):
        if r.get('escort', '').lower() in yes:
            continue        # the escort levels the groups, it does not play events
        passcode = r.get('passcode', '')
        if passcode.isdigit() and s['passcode_digits']:
            passcode = passcode.zfill(s['passcode_digits'])     # Excel drops leading zeros
        c = Char(r['username'], r.get('password', ''), passcode, r['character'], r.get('build') or s['default_build'])
        c.skip = c.character in skip
        # added by hand / "finished" column: leveled elsewhere, plays without being in the finished list
        c.always = added or r.get('finished', '').lower() in yes
        out.append(c)
    return out


# ---------------------------------------------------------------- phBot status (shared memory)
def _phbot_shm(pid, size, work, write=False):
    """phBot's status segment "phBot_<pid>" (what phBot's own Manager reads and writes), a QSharedMemory"""
    if sys.platform != 'win32' or not pid:
        return None
    import hashlib
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.OpenFileMappingW.restype = wintypes.HANDLE
    k32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k32.MapViewOfFile.restype = ctypes.c_void_p
    k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
    k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    key = 'phBot_%d' % pid
    name = 'qipc_sharedmemory_' + ''.join(c for c in key if c.isascii() and c.isalpha()) + \
        hashlib.sha1(key.encode()).hexdigest()
    access = 0x0002 if write else 0x0004
    h = k32.OpenFileMappingW(access, False, name)
    if not h:
        return None
    try:
        view = k32.MapViewOfFile(h, access, 0, 0, size)
        if not view:
            return None
        try:
            return work(view)
        finally:
            k32.UnmapViewOfFile(view)
    finally:
        k32.CloseHandle(h)


def phbot_status(pid):
    import struct
    b = _phbot_shm(pid, 0x790, lambda view: ctypes.string_at(view, 0x790))
    if not b:
        return None
    text = lambda o, n: b[o:o + n].split(b'\0', 1)[0].decode('utf-8', 'replace')
    return {'name': text(0x037, 64), 'connected': bool(b[0x02d]), 'in_game': bool(b[0x02d] and b[0x02f]),
            'botting': bool(b[0x035]), 'dead': bool(b[0x153]), 'level': b[0x137], 'zone': text(0x4ac, 32),
            'exp': struct.unpack_from('<Q', b, 0x15c)[0], 'level_exp': struct.unpack_from('<Q', b, 0x164)[0],
            'client_pid': struct.unpack_from('<I', b, 0x020)[0]}


def show_bot(pid, show):
    """phBot's own Show / Hide command (status byte 0x4cd = 1 / 2)"""
    def work(view):
        ctypes.memmove(view + 0x4cd, b'\x01' if show else b'\x02', 1)
        return True
    return bool(_phbot_shm(pid, 0x4ce, work, write=True))


def _windows_of(pid):
    """titled top-level windows of a process"""
    if sys.platform != 'win32' or not pid:
        return []
    user32 = ctypes.windll.user32
    found = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.GetWindowTextLengthW(hwnd) > 0 and not user32.GetWindow(hwnd, 4):  # no owner
            found.append(hwnd)
        return True

    user32.EnumWindows(proto(cb), 0)
    return found


def show_client(bot_pid, show):
    """show (restore) or hide the game client of a bot (phBot started it with --hideclient); windows changed.
    The Elysian client's small LV / HP window (class SRO_StatusWidget) stays hidden."""
    wins = []
    for hwnd in _windows_of((phbot_status(bot_pid) or {}).get('client_pid', 0)):
        cls = ctypes.create_unicode_buffer(64)
        ctypes.windll.user32.GetClassNameW(hwnd, cls, 64)
        if cls.value != 'SRO_StatusWidget':
            wins.append(hwnd)
    for hwnd in wins:
        ctypes.windll.user32.ShowWindow(hwnd, 9 if show else 0)       # SW_RESTORE / SW_HIDE
        if show:
            ctypes.windll.user32.SetForegroundWindow(hwnd)
    return len(wins)


def process_image(pid):
    """full exe path of a running process, '' when it is not running"""
    if sys.platform != 'win32' or not pid:
        return ''
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k32.OpenProcess(0x1000, False, int(pid))          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ''
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:     # STILL_ACTIVE
            return ''
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        return buf.value if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) else ''
    finally:
        k32.CloseHandle(h)


def process_alive(pid):
    return bool(process_image(pid))


class RunningBot:
    """a phBot that was open before the runner (taken over by its character name)"""

    def __init__(self, pid):
        self.pid = pid

    def poll(self):
        return None if process_alive(self.pid) else 1


def kill_tree(pid):
    if pid:
        subprocess.run(['taskkill', '/PID', str(int(pid)), '/T', '/F'], capture_output=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


QUIT_WAIT = 15      # seconds a bot gets to close by itself before it is terminated


def quit_bot(pid):
    """close a phBot the way phBot's own Manager does: its graceful close flag (0x5e6), then terminate it if it is
    still open after QUIT_WAIT seconds. A terminated phBot never logs out of its ProjectHax session: many of them
    in a few minutes (characters that were refused at once) left new bots stuck on "Disconnected" without a client.
    Blocks up to QUIT_WAIT seconds: run it in a thread."""
    client = (phbot_status(pid) or {}).get('client_pid', 0)

    def flag(view):
        ctypes.memmove(view + 0x5e6, b'\x01', 1)
        return True
    if _phbot_shm(pid, 0x5e7, flag, write=True):
        end = time.time() + QUIT_WAIT
        while time.time() < end and process_alive(pid):
            time.sleep(0.5)
    if process_alive(pid):
        kill_tree(pid)
    if client and process_alive(client):       # its game client, when phBot left it behind
        kill_tree(client)


def running_phbots(s, any_folder=False):
    """{character in lower case: pid} of the open phBots of the runner's phBot folder that are logged in
    (never the bots of another phBot folder: farm bots must not be taken over and closed)"""
    found = {}
    folder = os.path.normcase(os.path.abspath(s['phbot_dir'])) + os.sep
    try:
        out = subprocess.run(['tasklist', '/FO', 'CSV', '/NH', '/FI', 'IMAGENAME eq %s' %
                              os.path.basename(s['phbot_command'][0])], capture_output=True, text=True,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
    except Exception:
        return found
    for row in csv.reader(out.splitlines()):
        if len(row) > 1 and row[1].isdigit():
            if not any_folder and not os.path.normcase(process_image(int(row[1]))).startswith(folder):
                continue
            st = phbot_status(int(row[1]))
            if st and st['name']:
                found[st['name'].lower()] = int(row[1])
    return found


# ---------------------------------------------------------------- launching
def private_server_args(s):
    data = read_json(os.path.join(s['phbot_dir'], 'vSRO.json'), {}) or {}
    for key, info in data.items():
        if s['server'].lower() in [str(x).lower() for x in (info or {}).get('servers', [])]:
            return ['--locale', 'private-server', '--privateserver', key]
    return []


def client_dir(s):
    """the game client's folder (vSRO.json 'path' of our server's entry), '' when not known"""
    data = read_json(os.path.join(s['phbot_dir'], 'vSRO.json'), {}) or {}
    for key, info in data.items():
        if s['server'].lower() in [str(x).lower() for x in (info or {}).get('servers', [])]:
            p = (info or {}).get('path') or ''
            return p if os.path.isdir(p) else ''
    return ''


def blocked_pk2(folder):
    """the .pk2 files a starting client cannot open. The client opens them read-only and lets others only read:
    while any process holds one with write access, every NEW client fails to open it and crashes in
    GFXFileManager.dll (+0x10a76, the archive pointer is null) - clients already running carry on.
    2 and 3 Nov: the long-running clients of the user's phBot Manager; all new clients worked again once it closed."""
    if sys.platform != 'win32' or not folder:
        return []
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    out = []
    for f in sorted(os.listdir(folder)):
        if f.lower().endswith('.pk2'):
            h = k32.CreateFileW(os.path.join(folder, f), 0x80000000, 1, None, 3, 0x80, None)   # read, share read
            if h is None or h == wintypes.HANDLE(-1).value:
                if ctypes.GetLastError() == 32:                 # ERROR_SHARING_VIOLATION
                    out.append(f)
            else:
                k32.CloseHandle(h)
    return out


def file_users(path):
    """[(pid, exe name)] of the processes that have a file open (Windows Restart Manager)"""
    if sys.platform != 'win32':
        return []
    from ctypes import wintypes

    class UNIQUE(ctypes.Structure):
        _fields_ = [('dwProcessId', wintypes.DWORD), ('ProcessStartTime', wintypes.FILETIME)]

    class INFO(ctypes.Structure):
        _fields_ = [('Process', UNIQUE), ('strAppName', ctypes.c_wchar * 256), ('strServiceShortName', ctypes.c_wchar * 64),
                    ('ApplicationType', ctypes.c_int), ('AppStatus', wintypes.ULONG), ('TSSessionId', wintypes.DWORD),
                    ('bRestartable', wintypes.BOOL)]
    rm = ctypes.WinDLL('rstrtmgr')
    sess = wintypes.DWORD()
    if rm.RmStartSession(ctypes.byref(sess), 0, ctypes.create_unicode_buffer(64)):
        return []
    try:
        if rm.RmRegisterResources(sess, 1, (ctypes.c_wchar_p * 1)(path), 0, None, 0, None):
            return []
        need, count, why = wintypes.UINT(0), wintypes.UINT(0), wintypes.DWORD()
        rm.RmGetList(sess, ctypes.byref(need), ctypes.byref(count), None, ctypes.byref(why))
        infos = (INFO * max(need.value, 1))()
        count = wintypes.UINT(need.value)
        if rm.RmGetList(sess, ctypes.byref(need), ctypes.byref(count), infos, ctypes.byref(why)):
            return []
        return [(infos[i].Process.dwProcessId, infos[i].strAppName) for i in range(count.value)]
    finally:
        rm.RmEndSession(sess)


def describe_holders(s, path):
    """'Death (phBot Sabo client), ...' for every process holding a file (which one has write access is not known)"""
    names = {}
    for ch, pid in running_phbots(s, any_folder=True).items():
        st = phbot_status(pid) or {}
        folder = os.path.basename(os.path.dirname(process_image(pid)))
        names[pid] = '%s (%s)' % (st.get('name') or ch, folder)
        if st.get('client_pid'):
            names[st['client_pid']] = '%s (%s client)' % (st.get('name') or ch, folder)
    out = []
    for pid, exe in file_users(path):
        out.append(names.get(pid, '%s pid %d' % (exe, pid)))
    return out


def build_command(s, c):
    cmd = list(s['phbot_command'])
    exe = cmd[0] if os.path.isabs(cmd[0]) else os.path.join(s['phbot_dir'], cmd[0])
    if os.path.isfile(exe):
        cmd[0] = os.path.normpath(exe)
    cmd += ['--username', c.username, '--password', c.password, '--server', s['server'], '--character', c.character]
    if c.passcode:
        cmd += ['--passcode', c.passcode]
    cmd += list(s['login_args']) if s['login_args'] else private_server_args(s)
    if s['mode'] == 'client':
        cmd.append('--client')
        if s['hide_client']:
            cmd.append('--hideclient')
    if s['minimize_bots']:
        cmd.append('--minimize')
    return cmd + list(s['extra_args'])


def masked(cmd):
    out, hide = [], False
    for x in cmd:
        out.append('***' if hide else x)
        hide = x in ('--password', '--passcode')
    return out


# ---------------------------------------------------------------- per-character phBot / plugin files
# profile settings taken from the build's template on every launch (everything else phBot saved is kept)
MANAGED = [('Auto Mastery',), ('Stat Points',), ('Berserk',), ('Auto Resurrect',), ('Scrolls',),
           ('Pick',), ('Shared Pick Filter',), ('Town', 'AutoAcceptRes')]


def apply_managed(prof, template):
    for path in MANAGED:
        src, dst = template, prof
        for k in path[:-1]:
            src = src.get(k) or {}
            dst = dst.setdefault(k, {})
        if path[-1] in src:
            dst[path[-1]] = copy.deepcopy(src[path[-1]])


def write_profile(s, c, template, build):
    """Config/<server>_<char>.json: the build's template, solo (no party, no trace) or a party of 2 with its partner,
    the skill lists of the build's last bracket and Auto Mastery = the build's masteries + skills, then the job
    mastery + job skills"""
    cfg = os.path.join(s['phbot_dir'], 'Config')
    path = os.path.join(cfg, '%s_%s.json' % (s['server'], c.character))
    prof = read_json(path)
    if prof and len(prof) == 1 and prof.get('Profile'):     # pointer to the named profile phBot used last
        prof = read_json(os.path.join(cfg, '%s_%s.%s.json' % (s['server'], c.character, prof['Profile'])))
    if not prof or len(prof) < 5:
        prof = copy.deepcopy(template)
    else:
        apply_managed(prof, template)
    prof.pop('Profile', None)
    party = prof.setdefault('Party', {})
    party.update({'AutoFormParty': False, 'AutoInviteParty': False, 'Invite From List': False,
                  'AutoAcceptParty': False, 'Accept Invites From List': False, 'Accept All Invites': False,
                  'Invite All Players': False, 'InviteList': [], 'Party Leader Names': [],
                  'LeaveIfLeaderNotInList': False})
    if c.partner and c.leader:          # the first of the pair invites its partner (as MassLeveler's leaders do)
        party.update({'AutoInviteParty': True, 'Invite From List': True, 'InviteList': [c.partner]})
    elif c.partner:                     # the second accepts only from it
        party.update({'AutoAcceptParty': True, 'Accept Invites From List': True, 'InviteList': [c.partner],
                      'Party Leader Names': [c.partner], 'LeaveIfLeaderNotInList': True})
    prof['Union Party'] = dict(prof.get('Union Party') or {}, AcceptUnionPartyInvites=False,
                               AutoUnionPartyInvite=False, UnionPartyAccept=[], UnionPartyInvite=[])
    prof['Trace'] = dict(prof.get('Trace') or {}, Name='None')
    am = prof.setdefault('Auto Mastery', {})
    for key, names in (('Mastery', [s['job_mastery']] if s['job_mastery'] else []), ('Skill', s['job_skills'])):
        am[key] = list(am.get(key) or []) + [n for n in names if n not in (am.get(key) or [])]
    brackets = (build or {}).get('brackets') or []
    if brackets:
        b = brackets[-1]
        sk = prof.setdefault('Skills', {})
        for key in ('sNormal', 'bNormal', 'bScript', 'Healing', 'Resurrect'):
            sk[key] = list(b.get(key, []))
        sk['Imbue'] = b.get('Imbue', '')
        sk['Party Buffs'] = {k: [] for k in sk.get('Party Buffs', {})}
    write_json(path, prof)


def ensure_pick_filter(s, c):
    """a new character gets the pick filter database of the template (what the pet picks); phBot keeps it"""
    src = path_in_root(s['pick_template'])
    dst = os.path.join(s['phbot_dir'], 'Config', '%s_%s.db3' % (s['server'], c.character))
    if os.path.isfile(src) and not os.path.isfile(dst):
        shutil.copyfile(src, dst)


def prepare_modungeon(s, c, ev):
    """MoDungeon_<char>.json: the event's profile, Auto start, skills from the phBot profile (written above)"""
    cfg = os.path.join(s['phbot_dir'], 'Config')
    path = os.path.join(cfg, 'MoDungeon_%s.json' % c.character)
    mine = read_json(path, {}) or {}
    mine.update({'profile': ev.get('profile') or 'Default', 'auto_start': True, 'use_phbot': True})
    for k, v in (('attack', []), ('buff', []), ('imbue', None), ('last_enter', 0), ('last_exit', 0),
                 ('phbot_buff', False), ('inside', False)):
        mine.setdefault(k, v)
    write_json(path, mine)


def set_modungeon_runs(s, runs):
    """runs per character: shared by every character (MoDungeon.json runs_todo)"""
    path = os.path.join(s['phbot_dir'], 'Config', 'MoDungeon.json')
    c = read_json(path)
    if c is not None and c.get('runs_todo') != runs:
        c['runs_todo'] = runs
        write_json(path, c)


def log_path(s, c):
    return os.path.join(s['phbot_dir'], 'Log', '%s_%s_Log.txt' % (s['server'], c.character))


def new_log_lines(s, c):
    path = log_path(s, c)
    try:
        size = os.path.getsize(path)
        if size < c.log_pos:
            c.log_pos = 0           # a new log file
        if size == c.log_pos:
            return []
        with open(path, 'rb') as f:
            f.seek(c.log_pos)
            data = f.read()
    except OSError:
        return []
    end = data.rfind(b'\n') + 1     # only whole lines
    c.log_pos += end
    return data[:end].decode('utf-8', 'replace').splitlines()


def log_size(s, c):
    try:
        return os.path.getsize(log_path(s, c))
    except OSError:
        return 0


# ---------------------------------------------------------------- the runner
class Runner:
    def __init__(self, settings_path=None, log=print):
        self.settings_path = settings_path or path_in_root('settings.json')
        self.log = log
        self.lock = threading.RLock()
        self.running = False            # opening new characters allowed
        self.stop_event = threading.Event()
        self.thread = None
        self.next_launch = 0.0
        self.start_pause = 0.0       # no bot opens before this (a game client did not start)
        self.blocked = None          # (locked .pk2 files, who has them open) while new clients cannot start
        self.chars = []
        self.closing = []           # threads of bots being closed (they still count as open)
        self.reload()

    # -------------------------------------------------- setup
    def reload(self):
        with self.lock:
            self.s = load_settings(self.settings_path)
            self.skip = set(read_json(path_in_root(self.s['skip']), []) or [])
            old = {c.character: c for c in self.chars}
            self.chars = load_chars(self.s, self.skip)
            for i, c in enumerate(self.chars):        # open bots stay as they are
                if c.character in old and (old[c.character].proc or old[c.character].queued):
                    o = old[c.character]
                    o.skip = c.skip
                    self.chars[i] = o
            self.builds, self.templates = {}, {}
            bdir = path_in_root(self.s['builds_dir'])
            for f in sorted(os.listdir(bdir)) if os.path.isdir(bdir) else []:
                if f.endswith('.json'):
                    self.builds[f[:-5]] = read_json(os.path.join(bdir, f), {}) or {}
                    self.templates[f[:-5]] = read_json(os.path.join(path_in_root(self.s['templates_dir']), f), {}) or {}
            self.load_finished()
            self.load_results()
            if not self.running:
                self._take_over()

    def event(self):
        evs = self.s.get('events') or []
        return next((e for e in evs if e.get('name') == self.s.get('event')), evs[0] if evs else None)

    def load_finished(self):
        """{char: level} from the finished list MassLeveler Escort writes to OneDrive (other PC) + the progress of a
        MassLeveler Escort on this PC (level 0 = not known; checked after login); None = neither found"""
        path = finished_path(self.s)
        data = read_json(path)
        local = {}
        for d in local_massleveler_dirs(self.s):
            for ch, v in (read_json(os.path.join(d, 'data', 'progress.json'), {}) or {}).items():
                if v in ('done', 'finished'):
                    local[ch] = 0
        if data is None and not local:
            self.finished = None
            if not getattr(self, 'warned_finished', False):
                self.log('Finished list not found: %s' % path)
                self.warned_finished = True
            return
        self.warned_finished = False
        self.finished = dict(local)
        if isinstance(data, dict) and 'characters' not in data:
            # the file picked is MassLeveler Escort's own data/progress.json ({char: state}), not finished.json
            self.finished.update({ch: 0 for ch, v in data.items() if v in ('done', 'finished')})
            return
        self.finished.update({ch: int((v or {}).get('level') or 0)
                              for ch, v in ((data or {}).get('characters') or {}).items()})

    def results_path(self):
        ev = self.event()
        return path_in_root(os.path.join(self.s['runs_dir'], event_key(ev['name']) + '.json')) if ev else None

    def load_results(self):
        p = self.results_path()
        self.results = (read_json(p, {}) or {}) if p else {}

    def save_results(self):
        p = self.results_path()
        if p:
            write_json(p, self.results)

    def save_skip(self):
        write_json(path_in_root(self.s['skip']), sorted(self.skip))

    def save_settings(self, changes):
        with self.lock:
            raw = read_json(self.settings_path, {}) or {}
            raw.update(changes)
            write_json(self.settings_path, raw)
            self.s.update(changes)
            if 'event' in changes or 'events' in changes:
                self.load_results()

    def _take_over(self):
        """bots open from before (or opened by hand) are shown as open and finished like the others"""
        found = running_phbots(self.s)
        for c in self.chars:
            pid = found.get(c.character.lower())
            if pid and not self.alive(c):
                c.proc, c.launched_at, c.in_game_at = RunningBot(pid), time.time(), time.time()
                c.plugin_seen = True    # its plugin lines came before the runner read its log
                c.log_pos = log_size(self.s, c)
                c.state = 'open (taken over)'

    # -------------------------------------------------- what is left
    def ready(self, c):
        """'' = can play, else why not"""
        if c.skip:
            return 'unticked'
        if self.s['only_finished'] and not c.always:
            if self.finished is None:
                return 'no finished list'
            if c.character not in self.finished:
                return 'not finished leveling'
        return ''

    def todo(self):
        return [c for c in self.chars if not self.ready(c) and c.character not in self.results]

    @staticmethod
    def alive(c):
        return c.proc is not None and c.proc.poll() is None

    def open_count(self):
        self.closing = [t for t in self.closing if t.is_alive()]
        return sum(1 for c in self.chars if self.alive(c)) + len(self.closing)

    # -------------------------------------------------- control
    def start(self):
        with self.lock:
            ev = self.event()
            if not ev:
                self.log('No event - add one in Events.')
                return
            self.load_finished()
            if self.s['only_finished'] and self.finished is None:
                self.log('Not started: the finished list of MassLeveler Escort is missing (%s).' % finished_path(self.s))
                return
            if ev.get('type') == 'MoDungeon':
                set_modungeon_runs(self.s, int(ev.get('runs') or 1))
            todo = self.todo()
            for c in todo:
                c.queued = not self.alive(c)
                if c.queued:
                    self.unpair(c)              # pairs are made when they open
            self.running = True
            self.next_launch = 0.0
            self.log('%s: %d character(s) to play, at most %d open at a time.' % (ev['name'], len(todo),
                                                                                 self.s['max_open']))
        self._ensure_loop()

    def stop(self, close_bots=False):
        with self.lock:
            self.running = False
            for c in self.chars:
                c.queued = False
                if close_bots and self.alive(c):
                    self.close(c, 'closed by hand')
            self.log('Stopped' + (' - every bot closed.' if close_bots else ' - open bots keep playing.'))

    def _ensure_loop(self):
        if not self.thread or not self.thread.is_alive():
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    def shutdown(self):
        self.stop_event.set()

    def close(self, c, why=''):
        """the bot closes in the background (graceful close, then terminate); it still counts as open meanwhile"""
        if c.proc:
            t = threading.Thread(target=quit_bot, args=(c.proc.pid,), daemon=True)
            t.start()
            self.closing.append(t)
        c.proc, c.queued, c.finish = None, False, None
        c.state = why or 'closed'

    def set_skip(self, c, skip):
        with self.lock:
            c.skip = skip
            (self.skip.add if skip else self.skip.discard)(c.character)
            if skip:
                c.queued = False
            self.save_skip()

    def restart_bot(self, c):
        """right-click Restart bot: close its phBot (graceful, then terminate) and open it again first in line, also
        while the runner is stopped; its tries and result stay as they are (a bot stuck at the login)"""
        with self.lock:
            if c.character in self.results:
                self.log('%s already has a result - use "Play again" to play it again' % c.character)
                return
            if self.ready(c):
                self.log('%s is not opened (%s)' % (c.character, self.ready(c)))
                return
            if self.alive(c):
                self.close(c, 'restarting')
            c.queued, c.force_open, c.state = True, True, 'restarting'
            self.log('%s: bot restarted by hand' % c.character)

    def reset_result(self, c):
        with self.lock:
            self.results.pop(c.character, None)
            self.save_results()
            c.state = ''
            if self.running and not self.ready(c) and not self.alive(c):
                c.queued = True

    def new_run(self):
        """forget every result of this event: everyone plays it again"""
        with self.lock:
            self.results = {}
            self.save_results()
            for c in self.chars:
                if not self.alive(c):
                    c.state = ''
            self.log('%s: new run - every character plays it again.' % self.event()['name'])

    def retry_failed(self):
        with self.lock:
            n = 0
            for ch, r in list(self.results.items()):
                if r.get('result') in ('failed', 'not ready'):
                    del self.results[ch]
                    n += 1
            self.save_results()
            self.log('%d failed character(s) will play again.' % n)
            if self.running:
                for c in self.todo():
                    c.queued = not self.alive(c)
                    if c.queued:
                        self.unpair(c)

    # -------------------------------------------------- pairs (party_size 2)
    def by_name(self, name):
        return next((c for c in self.chars if c.character == name), None) if name else None

    def unpair(self, c):
        c.partner, c.leader = None, False

    def partner_ended(self, c, result):
        """c of a pair has its result: a partner that is not in the dungeon yet cannot get in alone (the NPC needs
        the party), so its bot closes and it waits for a new partner; one that is inside goes on alone"""
        p = self.by_name(c.partner)
        self.unpair(c)
        if not p or p.partner != c.character or p.character in self.results:
            return
        if p.held:                          # it waited in town for c: the two close together now
            self.release(p, time.time())
            return
        if self.alive(p) and not p.entered and not p.finish:
            self.close(p, 'partner %s: %s - waiting for a new partner' % (c.character, result))
            p.queued = True
            self.unpair(p)
            self.log('%s: its partner %s ended (%s) before the dungeon - closed, it plays with another one'
                     % (p.character, c.character, result))
        elif not self.alive(p):
            self.unpair(p)                  # not open (yet): it gets another partner

    def hold_for_partner(self, c):
        """c got no entries (played today): it waits in town for its partner when the partner can play on - it is
        inside, or it is the pair's leader (the leader enters first, alone if it must). A leader that got no entries
        does not wait: its partner cannot get in without it (partner_ended gives that one a new partner)"""
        p = self.by_name(c.partner)
        return bool(p and p.partner == c.character and self.alive(p) and p.character not in self.results
                    and (p.entered or p.leader))

    def release(self, c, at):
        """a character that waited in town for its partner closes at the same time as the partner"""
        c.held = False
        c.finish = ('no entries', c.hold_note, max(c.hold_min, at))
        c.state = 'no entries - closing with its partner'

    def pick_next(self, want):
        """the next character to open, None = none now. party_size 2: the partner of an open character first,
        else a new pair (two free places) - the first one opens now, its partner right after"""
        free = int(self.s['max_open']) - self.open_count()
        if free <= 0:
            return None
        if party_size(self.event()) == 1:
            return next((c for c in want if c.force_open), want[0])
        for c in want:
            p = self.by_name(c.partner)
            if p and p.partner == c.character and self.alive(p):
                return c                    # the second of a pair (or one of a pair opened again)
        if free < 2:
            return None
        a = next((c for c in want if c.force_open), want[0])
        b = next((c for c in want if c is not a), None)
        if not b:
            others = [c for c in self.chars if c is not a and (self.alive(c) or c.queued)]
            if not others and not self.closing:
                self.unpair(a)
                a.queued = False
                self.set_result(a, 'failed', 'no partner left')
                a.state = 'no partner left'
                self.log('%s: no character left to play with - failed (Retry failed later)' % a.character)
            return None                     # others still play: one of them may need a partner again
        a.partner, a.leader = b.character, True
        b.partner, b.leader = a.character, False
        self.log('Pair: %s + %s' % (a.character, b.character))
        return a

    # -------------------------------------------------- loop
    def _loop(self):
        tick = 0
        while not self.stop_event.is_set():
            try:
                with self.lock:
                    if tick % 20 == 0:
                        self.load_finished()        # MassLeveler Escort keeps finishing characters
                    self.tick(time.time())
            except Exception as e:
                self.log('runner error: %s' % e)
            tick += 1
            self.stop_event.wait(2.0)

    def game_files_free(self):
        """False while a .pk2 file of the client is held with write access (a new client would crash): the
        holders are logged once per block and shown in the window"""
        blocked = blocked_pk2(client_dir(self.s))
        if not blocked:
            if self.blocked:
                self.log('Game files free again - opening bots.')
            self.blocked = None
            return True
        if not self.blocked or self.blocked[0] != blocked:
            holders = describe_holders(self.s, os.path.join(client_dir(self.s), blocked[0]))
            self.blocked = (blocked, holders)
            self.log('Game files locked: %s - a new game client would crash, no bot opens until they are free. '
                     'Open: %s. Close the bots of other phBot folders / managers (their clients hold them).' % (
                         ', '.join(blocked), ', '.join(holders) or '?'))
        return False

    def client_crashes(self, since):
        """client crash files (Dump\\0N[...].dmp, a client that crashed at start) written since a time"""
        folder = os.path.join(client_dir(self.s), 'Dump')
        try:
            return sum(1 for f in os.listdir(folder) if f.startswith('0N[') and
                       os.path.getmtime(os.path.join(folder, f)) >= since)
        except OSError:
            return 0

    def set_result(self, c, result, note=''):
        self.results[c.character] = {'result': result, 'note': note, 'level': c.level, 'at': time.time()}
        self.save_results()
        if c.partner:
            self.partner_ended(c, result)

    def tick(self, now):
        s, ev = self.s, self.event()
        for c in self.chars:
            if c.proc is None:
                continue
            if not self.alive(c):
                c.proc = None
                if c.finish or c.character in self.results:
                    continue
                if self.running and c.restarts < s['max_restarts']:
                    c.restarts += 1
                    c.queued = True
                    c.state = 'closed by itself - opening again (%d/%d)' % (c.restarts, s['max_restarts'])
                    self.log('%s %s' % (c.character, c.state))
                else:
                    c.state = 'closed by itself'
                    self.set_result(c, 'failed', 'bot closed')
                continue
            live = phbot_status(c.proc.pid)
            if live and live['level']:
                c.level = live['level']
            if c.held:
                # no entries, waiting in town until its partner is done (no event timeout meanwhile)
                p = self.by_name(c.partner)
                if not p or p.partner != c.character or not self.alive(p) or p.character in self.results:
                    self.release(c, now)
                elif p.finish:
                    self.release(c, p.finish[2])     # the partner closes then: both together
                else:
                    continue
            if c.finish:
                if now >= c.finish[2]:
                    self.set_result(c, c.finish[0], c.finish[1])
                    self.close(c, '%s%s' % (c.finish[0], ' - ' + c.finish[1] if c.finish[1] else ''))
                    self.log('%s %s - closed' % (c.character, c.state))
                continue
            if live and live['connected']:
                c.connected_once = True
            if not (live and live['in_game']):
                if not c.in_game_at and now - c.launched_at > s['login_timeout_minutes'] * 60:
                    crashes = 0 if c.connected_once else self.client_crashes(c.launched_at)
                    if crashes:
                        # its game client crashed at start (2 and 3 Nov: every new client did, a .pk2 file was held
                        # by other clients): a new bot would crash too, so none opens for a while (the .pk2 check
                        # holds them as well); the character keeps its tries
                        wait = float(s.get('start_fail_wait_minutes') or 15)
                        self.close(c, 'game client crashed - waiting %g min' % wait)
                        c.queued = True
                        if now >= self.start_pause:     # once per pause (the other open bots end the same way)
                            self.log('%s: its game client crashed at start (%d client crash file(s) since it opened)'
                                     ' - no bot opens for %g min' % (c.character, crashes, wait))
                        self.start_pause = max(self.start_pause, now + wait * 60)
                        self.next_launch = max(self.next_launch, self.start_pause)
                        continue
                    # stuck at the login: no server in the list, the character missing from the character list,
                    # a client that never connected... its bot is restarted (first in line, also when stopped)
                    limit = int(s.get('login_restarts') or 10)
                    if c.login_restarts < limit:
                        c.login_restarts += 1
                        self.log('%s: not in the game after %g min (%s) - restarting its bot (%d/%d)' % (
                            c.character, s['login_timeout_minutes'],
                            'stuck at the login' if c.connected_once else 'never connected', c.login_restarts, limit))
                        self.close(c, 'stuck at the login - restarting')
                        c.queued, c.force_open = True, True
                    else:
                        self.close(c, 'never got in the game')
                        self.set_result(c, 'failed', 'login')
                    continue
                c.state = 'logging in' if live and live['connected'] else 'starting'
                continue
            if not c.in_game_at:
                c.in_game_at = now
            if c.level and c.level < s['min_level']:
                self.set_result(c, 'not ready', 'level %d' % c.level)
                self.close(c, 'not ready - level %d' % c.level)
                self.log('%s is level %d (below %d) - closed' % (c.character, c.level, s['min_level']))
                continue
            c.state = 'dead' if live['dead'] else 'playing'
            for line in new_log_lines(s, c):
                if '[MoDungeon]' in line:
                    c.plugin_seen = True
                if ENTERED_LINE in line:
                    c.entered = True
                for rx, result, note, wait in MODUNGEON_LINES:
                    m = rx.search(line)
                    if m:
                        if result == 'failed' and m.groups():
                            note = '%s: %s' % (note, m.group(1))
                        wait = s['close_delay_seconds'] if wait is None else wait
                        if result == 'no entries' and self.hold_for_partner(c):
                            # MoDungeon (Party of 2) goes back to town; the bot stays open for its partner
                            c.held, c.hold_note = True, note
                            c.hold_min = max(c.hold_min, now + NO_ENTRIES_WAIT)
                            c.state = 'no entries - waiting in town for %s' % c.partner
                            self.log('%s: %s - waits in town for %s, then they close together' % (
                                c.character, note, c.partner))
                            break
                        c.finish = (result, note, now + wait)
                        c.state = result + (' - going to town' if wait > 30 else ' - closing')
                        self.log('%s: %s' % (c.character, line.strip()))
                        break
            if (ev or {}).get('type') == 'MoDungeon' and not c.plugin_seen and not c.finish and \
                    now - c.in_game_at > PLUGIN_WAIT:
                # phBot now and then joins the game without its plugins (fnuk38 / 39 on 2 Nov): open it again
                if c.restarts < s['max_restarts']:
                    c.restarts += 1
                    self.log('%s: MoDungeon did not load - opening again (%d/%d)' % (
                        c.character, c.restarts, s['max_restarts']))
                    self.close(c, 'MoDungeon did not load - opening again')
                    c.queued = True
                else:
                    self.set_result(c, 'failed', 'MoDungeon did not load')
                    self.close(c, 'MoDungeon did not load')
                continue
            timeout = int((ev or {}).get('timeout_minutes') or 0)
            if not c.finish and timeout and now - c.in_game_at > timeout * 60:
                self.set_result(c, 'failed', 'timeout %d min' % timeout)
                self.close(c, 'timeout - closed')
                self.log('%s still not done after %d min - closed' % (c.character, timeout))
        # open the next one
        want = [c for c in self.chars if c.queued and not self.alive(c) and not self.ready(c)
                and c.character not in self.results and (self.running or c.force_open)]
        if want and now >= self.next_launch and self.open_count() < int(s['max_open']):
            nxt = self.pick_next(want)        # a bot restarted by hand / the partner of an open one goes first
            if nxt and not self.game_files_free():
                self.next_launch = now + LOCK_RECHECK   # a client started now would crash: look again soon
            elif nxt:
                self.launch(nxt, ev)
                self.next_launch = now + s['launch_delay_seconds']
        if self.running and not any(c.queued for c in self.chars) and not self.open_count():
            self.running = False
            done = sum(1 for r in self.results.values() if r.get('result') in ('done', 'no entries'))
            self.log('%s finished: %d done, %d failed / not ready.' % (ev['name'], done, len(self.results) - done))

    def launch(self, c, ev):
        s = self.s
        c.queued = False
        c.finish, c.in_game_at, c.level, c.plugin_seen, c.connected_once = None, 0.0, 0, False, False
        c.force_open, c.entered, c.held, c.hold_min = False, False, False, 0.0
        pid = running_phbots(s).get(c.character.lower())
        if pid:
            c.proc, c.launched_at, c.plugin_seen = RunningBot(pid), time.time(), True
            c.log_pos = log_size(s, c)
            c.state = 'already open - taken over'
            self.log('%s is already open in phBot - taken over' % c.character)
            return
        if c.character.lower() in running_phbots(s, any_folder=True):
            c.state = 'playing in another phBot - left out'     # logging in would kick it there
            self.log('%s is logged in from another phBot folder - not opened' % c.character)
            return
        try:
            write_profile(s, c, self.templates.get(c.build) or {}, self.builds.get(c.build))
            ensure_pick_filter(s, c)
            if ev.get('type') == 'MoDungeon':
                prepare_modungeon(s, c, ev)
        except Exception as e:
            self.log('%s: files not written (%s)' % (c.character, e))
        cmd = build_command(s, c)
        c.log_pos = log_size(s, c)
        try:
            c.proc = subprocess.Popen(cmd, cwd=s['phbot_dir'])
            c.launched_at = time.time()
            c.state = 'starting' + (' (with %s)' % c.partner if c.partner else '')
            self.log('open %s (%s, %s%s): %s' % (c.character, c.build, ev['name'],
                                                 ', with %s' % c.partner if c.partner else '', ' '.join(masked(cmd[1:]))))
        except Exception as e:
            c.proc = None
            c.state = 'open failed: %s' % e
            self.set_result(c, 'failed', 'open failed')
            self.log('open %s failed: %s' % (c.character, e))
