"""Mass Runner core: plays an event (a dungeon, ...) with the characters MassLeveler Escort finished.

The characters come from accounts.csv (the same file as MassLeveler Escort). Only the ones in the finished list
MassLeveler Escort writes to OneDrive (MassLeveler/finished.json) are opened, at most max_open at a time: a
character that is done is closed (phBot terminated) and the next one opens in its place.

Event = what every character plays, picked from settings.json "events". type MoDungeon: the character's
Config/MoDungeon_<char>.json gets the event's MoDungeon profile + Auto start, MoDungeon starts by itself in the
game and the runner reads phBot's log (Log/<server>_<char>_Log.txt) to know when it is done: the last
"All N run(s) done" line comes after the pet's items went to the inventory.

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
    'accounts_csv': 'accounts.csv',
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
    'max_restarts': 3,                  # a bot that closes by itself / never gets in the game is opened again
    'close_delay_seconds': 5,           # done: wait this long, then terminate the bot
    'event': 'Isis Dungeon',
    'events': [
        {'name': 'Isis Dungeon', 'type': 'MoDungeon', 'profile': 'Isis', 'runs': 1, 'timeout_minutes': 45},
    ],
    'runs_dir': 'data/runs',            # <event>.json: the result of every character in the event's run
    'skip': 'data/skip.json',           # characters unticked in the dashboard
}

EVENT_TYPES = ('MoDungeon',)

NO_ENTRIES_WAIT = 90        # after "No more entries" MoDungeon uses a return scroll and empties the pet
# MoDungeon log lines (plugin v4.14) -> result, note, seconds before the bot is closed (None = close_delay_seconds)
MODUNGEON_LINES = [
    (re.compile(r'\[MoDungeon\] All \d+ run\(s\) done'), 'done', '', None),
    (re.compile(r'\[MoDungeon\] No more entries'), 'no entries', 'entry limit reached', NO_ENTRIES_WAIT),
    # the entry NPC refuses with 271c when the character already played the dungeon today (Elysian logs: every
    # 271c came after a finished run the same day; 291c = full for now, it got in later). MoDungeon only retries.
    (re.compile(r'\[MoDungeon\] debug: NPC refused, code 271c'), 'no entries', 'already played today (NPC code 271c)',
     None),
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
    dirs.append(os.path.join(os.path.dirname(ROOT), 'MassLeveler Escort'))
    out = []
    for d in dirs:
        if os.path.isfile(os.path.join(d, 'data', 'progress.json')) and                 os.path.normcase(os.path.abspath(d)) not in [os.path.normcase(os.path.abspath(x)) for x in out]:
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
        self.proc = None
        self.queued = False         # waits for a free place to open
        self.launched_at = 0.0
        self.in_game_at = 0.0
        self.restarts = 0
        self.log_pos = 0            # phBot log size when it opened: only newer lines count
        self.finish = None          # (result, note, close at)
        self.state = ''
        self.level = 0


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


def load_chars(s, skip):
    out = []
    for r in read_csv_rows(path_in_root(s['accounts_csv'])):
        if r.get('escort', '').lower() in ('yes', 'y', '1', 'true', 'x') or not r.get('character'):
            continue        # the escort levels the groups, it does not play events
        passcode = r.get('passcode', '')
        if passcode.isdigit() and s['passcode_digits']:
            passcode = passcode.zfill(s['passcode_digits'])     # Excel drops leading zeros
        c = Char(r['username'], r.get('password', ''), passcode, r['character'], r.get('build') or s['default_build'])
        c.skip = c.character in skip
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
            'exp': struct.unpack_from('<Q', b, 0x15c)[0], 'level_exp': struct.unpack_from('<Q', b, 0x164)[0]}


def show_bot(pid, show):
    """phBot's own Show / Hide command (status byte 0x4cd = 1 / 2)"""
    def work(view):
        ctypes.memmove(view + 0x4cd, b'\x01' if show else b'\x02', 1)
        return True
    return bool(_phbot_shm(pid, 0x4ce, work, write=True))


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
    """Config/<server>_<char>.json: the build's template, solo (no party, no trace), the skill lists of the build's
    last bracket and Auto Mastery = the build's masteries + skills, then the job mastery + job skills"""
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
        self.chars = []
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
                c.log_pos = log_size(self.s, c)
                c.state = 'open (taken over)'

    # -------------------------------------------------- what is left
    def ready(self, c):
        """'' = can play, else why not"""
        if c.skip:
            return 'unticked'
        if self.s['only_finished']:
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
        return sum(1 for c in self.chars if self.alive(c))

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
        if c.proc:
            kill_tree(c.proc.pid)
        c.proc, c.queued, c.finish = None, False, None
        c.state = why or 'closed'

    def set_skip(self, c, skip):
        with self.lock:
            c.skip = skip
            (self.skip.add if skip else self.skip.discard)(c.character)
            if skip:
                c.queued = False
            self.save_skip()

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

    def set_result(self, c, result, note=''):
        self.results[c.character] = {'result': result, 'note': note, 'level': c.level, 'at': time.time()}
        self.save_results()

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
            if c.finish:
                if now >= c.finish[2]:
                    self.set_result(c, c.finish[0], c.finish[1])
                    self.close(c, '%s%s' % (c.finish[0], ' - ' + c.finish[1] if c.finish[1] else ''))
                    self.log('%s %s - closed' % (c.character, c.state))
                continue
            if not (live and live['in_game']):
                if not c.in_game_at and now - c.launched_at > s['login_timeout_minutes'] * 60:
                    if c.restarts < s['max_restarts']:
                        c.restarts += 1
                        self.log('%s not in the game after %d min - opening again' % (c.character,
                                                                                     s['login_timeout_minutes']))
                        self.close(c, 'login timeout - opening again')
                        c.queued = True
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
                for rx, result, note, wait in MODUNGEON_LINES:
                    m = rx.search(line)
                    if m:
                        if result == 'failed' and m.groups():
                            note = '%s: %s' % (note, m.group(1))
                        wait = s['close_delay_seconds'] if wait is None else wait
                        c.finish = (result, note, now + wait)
                        c.state = result + (' - going to town' if wait > 30 else ' - closing')
                        self.log('%s: %s' % (c.character, line.strip()))
                        break
            timeout = int((ev or {}).get('timeout_minutes') or 0)
            if not c.finish and timeout and now - c.in_game_at > timeout * 60:
                self.set_result(c, 'failed', 'timeout %d min' % timeout)
                self.close(c, 'timeout - closed')
                self.log('%s still not done after %d min - closed' % (c.character, timeout))
        # open the next one
        if self.running and now >= self.next_launch and self.open_count() < int(s['max_open']):
            nxt = next((c for c in self.chars if c.queued and not self.alive(c) and not self.ready(c)
                        and c.character not in self.results), None)
            if nxt:
                self.launch(nxt, ev)
                self.next_launch = now + s['launch_delay_seconds']
        if self.running and not any(c.queued for c in self.chars) and not self.open_count():
            self.running = False
            done = sum(1 for r in self.results.values() if r.get('result') in ('done', 'no entries'))
            self.log('%s finished: %d done, %d failed / not ready.' % (ev['name'], done, len(self.results) - done))

    def launch(self, c, ev):
        s = self.s
        c.queued = False
        c.finish, c.in_game_at, c.level = None, 0.0, 0
        pid = running_phbots(s).get(c.character.lower())
        if pid:
            c.proc, c.launched_at = RunningBot(pid), time.time()
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
            c.state = 'starting'
            self.log('open %s (%s, %s): %s' % (c.character, c.build, ev['name'], ' '.join(masked(cmd[1:]))))
        except Exception as e:
            c.proc = None
            c.state = 'open failed: %s' % e
            self.set_result(c, 'failed', 'open failed')
            self.log('open %s failed: %s' % (c.character, e))
