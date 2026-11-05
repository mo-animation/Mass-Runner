"""Mass Runner test with a fake phBot (tools/fake_phbot.py): no game, no real accounts.

py -I tools/test_runner.py
12 characters (accounts.csv + MassLeveler's + the Excel sheet of added ones), 10 in the finished list + 1 added by
hand, one level 95: at most 3 open at a time, the level-95 one is closed as not ready, fnuk06 gets the entry NPC's 271c (played today) and is closed as no entries, the others
write MoDungeon's "All 1 run(s) done" line and are closed as done.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'runner'))
import runner_core as rc  # noqa: E402


def main():
    work = tempfile.mkdtemp(prefix='mr_test_')
    try:
        phbot = os.path.join(work, 'phBot')
        os.makedirs(os.path.join(phbot, 'Config'))
        os.makedirs(os.path.join(phbot, 'Log'))
        rc.write_json(os.path.join(phbot, 'Config', 'MoDungeon.json'), {'go': [1], 'dungeon': [1], 'exit': [1],
                                                                        'runs_todo': 3})
        with open(os.path.join(work, 'accounts.csv'), 'w') as f:
            f.write('username,password,passcode,character,escort\n')
            f.write('esc,p,1,wiz01,yes\n')
            for i in range(1, 11):
                f.write('u%02d,p,1234567,fnuk%02d,\n' % (i, i))
        ml = os.path.join(work, 'MassLeveler Escort')     # its accounts.csv is read too (fnuk01 only once)
        os.makedirs(ml)
        with open(os.path.join(ml, 'accounts.csv'), 'w') as f:
            f.write('username,password,passcode,character\nu01,p,1234567,fnuk01\nu11,p,1234567,fnuk11\n')
        # the Excel sheet of accounts added by hand: fnuk12 plays without being in the finished list
        rc.make_extra_sheet(os.path.join(work, 'extra.xlsx'))
        import openpyxl
        wb = openpyxl.load_workbook(os.path.join(work, 'extra.xlsx'))
        wb.active.append(['u12', 'p', 1234567, 'fnuk12'])
        wb.save(os.path.join(work, 'extra.xlsx'))
        rc.write_json(os.path.join(work, 'finished.json'), {'characters': {
            'fnuk%02d' % i: {'level': 100} for i in range(1, 11)}})
        settings = os.path.join(work, 'settings.json')
        rc.write_json(settings, {
            'phbot_dir': phbot, 'phbot_dirs': [], 'phbot_command': [sys.executable, os.path.join(ROOT, 'tools',
                                                                                              'fake_phbot.py')],
            'login_args': ['--x'], 'accounts_csv': os.path.join(work, 'accounts.csv'),
            'massleveler_dirs': [ml], 'find_massleveler': False, 'extra_accounts': os.path.join(work, 'extra.xlsx'),
            'finished_file': os.path.join(work, 'finished.json'), 'max_open': 3, 'launch_delay_seconds': 0.5,
            'builds_dir': os.path.join(ROOT, 'data', 'builds'), 'templates_dir': os.path.join(ROOT, 'templates'),
            'pick_template': os.path.join(ROOT, 'templates', 'pick_filter.db3'),
            'runs_dir': os.path.join(work, 'runs'), 'skip': os.path.join(work, 'skip.json'),
            'close_delay_seconds': 0.5, 'login_timeout_minutes': 0.1, 'start_fail_wait_minutes': 0.1})
        os.environ['FAKE_LOW'] = 'fnuk04'
        os.environ['FAKE_PLAYED'] = 'fnuk06'
        os.environ['FAKE_NO_QUIT'] = 'fnuk07'      # ignores the graceful close: terminated after QUIT_WAIT
        os.environ['FAKE_NO_PLUGIN'] = 'fnuk08'    # the first time in the game without MoDungeon: opened again
        os.environ['FAKE_NO_CONNECT'] = 'fnuk09'   # the first time stuck at the login: restarted, then it plays
        os.environ['FAKE_CRASH'] = 'fnuk10'        # the first time its client crashes: a pause, then it plays
        rc.PLUGIN_WAIT = 4
        rc.LOCK_RECHECK = 1
        # a game folder whose Media.pk2 is held with write access for the first 5 s: no bot may open meanwhile
        client = os.path.join(work, 'client')
        os.makedirs(client)
        with open(os.path.join(client, 'Media.pk2'), 'wb') as f:
            f.write(b'pk2')
        rc.write_json(os.path.join(phbot, 'vSRO.json'), {'test': {'servers': ['Elysian'], 'path': client}})
        holder = open(os.path.join(client, 'Media.pk2'), 'r+b')
        threading.Timer(5, holder.close).start()
        logs = []
        r = rc.Runner(settings, log=logs.append)
        assert [c.character for c in r.chars] == ['fnuk%02d' % i for i in range(1, 13)], 'escort left out, once each'
        assert r.chars[11].passcode == r.chars[0].passcode == '01234567' and r.chars[11].always and not r.chars[10].always
        r.start()
        most = 0
        t0 = time.time()
        while time.time() - t0 < 90:
            with r.lock:
                most = max(most, r.open_count())
                if not r.running:
                    break
            time.sleep(0.3)
        r.shutdown()
        for l in logs:
            print(' ', l)
        res = r.results
        print('results:', {k: v['result'] for k, v in sorted(res.items())})
        assert most <= 3, 'more than 3 open: %d' % most
        locked = next(i for i, l in enumerate(logs) if l.startswith('Game files locked: Media.pk2'))
        free = next(i for i, l in enumerate(logs) if l.startswith('Game files free again'))
        first_open = next(i for i, l in enumerate(logs) if l.startswith('open '))
        assert locked < free < first_open, (locked, free, first_open)
        quit = sorted(f[5:-4] for f in os.listdir(phbot) if f.startswith('quit_'))
        assert quit == ['fnuk%02d' % i for i in range(1, 13) if i not in (7, 11)], quit   # the graceful close
        assert any('fnuk08: MoDungeon did not load - opening again' in l for l in logs), 'fnuk08 not reopened'
        assert any(l.startswith('fnuk09: not in the game after') and 'restarting its bot (1/10)' in l
                   for l in logs), 'fnuk09 not restarted'
        assert not any(l.startswith('fnuk09') and 'crashed' in l for l in logs), 'fnuk09 taken for a crash'
        assert any(l.startswith('fnuk10: its game client crashed at start') for l in logs), 'fnuk10 pause missing'
        assert not [c for c in r.chars if c.character == 'fnuk10'][0].restarts, 'the pause used a try'
        assert not r.closing, 'bots still closing'
        alive = [c.character for c in r.chars if c.proc and rc.process_alive(c.proc.pid)]
        assert not alive, alive
        assert res['fnuk04']['result'] == 'not ready', res['fnuk04']
        assert res['fnuk06']['result'] == 'no entries', res['fnuk06']
        assert all(res['fnuk%02d' % i]['result'] == 'done' for i in range(1, 13) if i not in (4, 6, 11))
        assert 'fnuk11' not in res, 'a not finished character was opened'
        prof = rc.read_json(os.path.join(phbot, 'Config', 'Elysian_fnuk01.json'))
        am = prof['Auto Mastery']
        assert 'Job Skills' in am['Mastery'] and 'Job - Trader Pouch' in am['Skill'] and 'Force' in am['Mastery']
        assert prof['Skills']['Imbue'] == 'Cloud Fire force', prof['Skills']['Imbue']
        md = rc.read_json(os.path.join(phbot, 'Config', 'MoDungeon_fnuk01.json'))
        assert md['profile'] == 'Isis' and md['auto_start'] and md['use_phbot'], md
        assert rc.read_json(os.path.join(phbot, 'Config', 'MoDungeon.json'))['runs_todo'] == 1
        print('OK - at most %d open' % most)
    finally:
        time.sleep(1)
        shutil.rmtree(work, ignore_errors=True)



def restart_test():
    """right-click Restart bot with the runner stopped: it opens anyway; restarting an open bot closes it and
    opens it again; the character then plays as usual"""
    work = tempfile.mkdtemp(prefix='mr_restart_')
    try:
        phbot = os.path.join(work, 'phBot')
        os.makedirs(os.path.join(phbot, 'Config'))
        os.makedirs(os.path.join(phbot, 'Log'))
        with open(os.path.join(work, 'accounts.csv'), 'w') as f:
            f.write('username,password,passcode,character\nu20,p,1,fnuk20\n')
        settings = os.path.join(work, 'settings.json')
        rc.write_json(settings, {
            'phbot_dir': phbot, 'phbot_dirs': [], 'phbot_command': [sys.executable, os.path.join(ROOT, 'tools',
                                                                                              'fake_phbot.py')],
            'login_args': ['--x'], 'accounts_csv': os.path.join(work, 'accounts.csv'), 'only_finished': False, 'find_massleveler': False,
            'finished_file': os.path.join(work, 'none.json'), 'launch_delay_seconds': 0.5,
            'builds_dir': os.path.join(ROOT, 'data', 'builds'), 'templates_dir': os.path.join(ROOT, 'templates'),
            'runs_dir': os.path.join(work, 'runs'), 'skip': os.path.join(work, 'skip.json'),
            'close_delay_seconds': 0.5})
        os.environ['FAKE_NO_CONNECT'] = 'fnuk20'        # the first bot never connects (stuck at the login)
        logs = []
        r = rc.Runner(settings, log=logs.append)
        r._ensure_loop()
        c = r.chars[0]
        r.restart_bot(c)                                # runner stopped: opens anyway
        t0 = time.time()
        while time.time() - t0 < 20 and not r.alive(c):
            time.sleep(0.2)
        assert r.alive(c), 'not opened while stopped: %s' % logs
        first = c.proc.pid
        time.sleep(1)
        r.restart_bot(c)                                # stuck: close it and open it again
        t0 = time.time()
        while time.time() - t0 < 40 and 'fnuk20' not in r.results:
            time.sleep(0.3)
        r.shutdown()
        opens = [l for l in logs if l.startswith('open fnuk20')]
        assert len(opens) == 2, logs
        assert r.results.get('fnuk20', {}).get('result') == 'done', (r.results, logs)
        assert not rc.process_alive(first), 'the stuck bot is still open'
        assert c.restarts == 0, 'Restart bot used a try'
        print('OK - Restart bot')
    finally:
        os.environ.pop('FAKE_NO_CONNECT', None)
        time.sleep(1)
        shutil.rmtree(work, ignore_errors=True)


def pair_run(work, n, env):
    """runs a party_size 2 event (the Maze) of n fake characters p01.., at most 4 open, with FAKE_ settings env;
    returns the runner, its log, the phBot folder and the most bots open at once"""
    phbot = os.path.join(work, 'phBot')
    os.makedirs(os.path.join(phbot, 'Config'))
    os.makedirs(os.path.join(phbot, 'Log'))
    rc.write_json(os.path.join(phbot, 'Config', 'MoDungeon.json'), {'go': [1], 'dungeon': [1], 'exit': [1]})
    with open(os.path.join(work, 'accounts.csv'), 'w') as f:
        f.write('username,password,passcode,character\n')
        for i in range(1, n + 1):
            f.write('u%02d,p,1,p%02d\n' % (i, i))
    settings = os.path.join(work, 'settings.json')
    rc.write_json(settings, {
        'phbot_dir': phbot, 'phbot_dirs': [], 'phbot_command': [sys.executable, os.path.join(ROOT, 'tools',
                                                                                          'fake_phbot.py')],
        'login_args': ['--x'], 'accounts_csv': os.path.join(work, 'accounts.csv'), 'only_finished': False,
        'find_massleveler': False, 'extra_accounts': os.path.join(work, 'extra.xlsx'),
        'finished_file': os.path.join(work, 'none.json'), 'max_open': 4, 'launch_delay_seconds': 0.5,
        'builds_dir': os.path.join(ROOT, 'data', 'builds'), 'templates_dir': os.path.join(ROOT, 'templates'),
        'pick_template': os.path.join(ROOT, 'templates', 'pick_filter.db3'),
        'runs_dir': os.path.join(work, 'runs'), 'skip': os.path.join(work, 'skip.json'),
        'close_delay_seconds': 0.5, 'event': 'Maze',
        'events': [{'name': 'Maze', 'type': 'MoDungeon', 'profile': 'Maze', 'runs': 1, 'timeout_minutes': 45,
                    'party_size': 2}]})
    for k in ('FAKE_LOW', 'FAKE_NO_QUIT', 'FAKE_NO_PLUGIN', 'FAKE_NO_CONNECT', 'FAKE_CRASH', 'FAKE_PLAYED',
              'FAKE_WAIT', 'FAKE_GATE'):
        os.environ.pop(k, None)
    os.environ.update(env)
    os.environ['FAKE_DUNGEON_SECONDS'] = '6'      # both of a pair are inside before either is done
    logs = []
    r = rc.Runner(settings, log=logs.append)
    r.start()
    most = 0
    t0 = time.time()
    while time.time() - t0 < 60:
        with r.lock:
            most = max(most, r.open_count())
            if not r.running:
                break
        time.sleep(0.3)
    r.shutdown()
    for k in list(env) + ['FAKE_DUNGEON_SECONDS']:
        os.environ.pop(k, None)
    return r, logs, phbot, most


def gate_test():
    """p01 waited at the entry NPC and its partner p02 did not come (MoDungeon: back to town and stop): p01 fails
    (Retry failed later) and closes; p02, not inside, closes too and plays with p05 instead"""
    work = tempfile.mkdtemp(prefix='mr_gate_')
    lines = rc.MODUNGEON_LINES[:]
    # the 90 s for the return scroll -> 2 s (the wait is in the line's table)
    rc.MODUNGEON_LINES[:] = [(rx, res, note, 2 if 'gate' in note else wait) for rx, res, note, wait in lines]
    try:
        r, logs, phbot, most = pair_run(work, 5, {'FAKE_GATE': 'p01', 'FAKE_WAIT': 'p02'})
        for l in logs:
            print(' ', l)
        res = {k: (v['result'], v['note']) for k, v in sorted(r.results.items())}
        print('results:', res)
        assert res['p01'] == ('failed', 'partner did not come to the gate'), res
        assert all(res[c][0] == 'done' for c in ('p02', 'p03', 'p04', 'p05')), res
        pairs = [l for l in logs if l.startswith('Pair: ')]
        assert pairs == ['Pair: p01 + p02', 'Pair: p03 + p04', 'Pair: p02 + p05'], pairs
        alive = [c.character for c in r.chars if c.proc and rc.process_alive(c.proc.pid)]
        assert not alive, alive
        print('OK - partner did not come to the gate')
    finally:
        rc.MODUNGEON_LINES[:] = lines
        time.sleep(1)
        shutil.rmtree(work, ignore_errors=True)


def pairs_test():
    """event with party_size 2 (the Maze), 6 characters, at most 4 open: they open two by two, the first of a pair
    invites the second in phBot and the second accepts only from it.
    p01 (a leader) already played today (271c) while p02 waits at the entry NPC: without its leader p02 cannot get in,
    so it is closed and plays with p05 instead. p04 (not a leader) already played today: it waits in town until its
    leader p03 is done, then the two close together. p06 is left without a partner"""
    work = tempfile.mkdtemp(prefix='mr_pairs_')
    no_entries_wait, rc.NO_ENTRIES_WAIT = rc.NO_ENTRIES_WAIT, 2
    try:
        r, logs, phbot, most = pair_run(work, 6, {'FAKE_PLAYED': 'p01,p04', 'FAKE_WAIT': 'p02'})
        for l in logs:
            print(' ', l)
        res = {k: v['result'] for k, v in sorted(r.results.items())}
        print('results:', res)
        pairs = [l for l in logs if l.startswith('Pair: ')]
        assert pairs == ['Pair: p01 + p02', 'Pair: p03 + p04', 'Pair: p02 + p05'], pairs
        assert any(l.startswith('p02: its partner p01 ended (no entries) before the dungeon') for l in logs), logs
        assert res == {'p01': 'no entries', 'p02': 'done', 'p03': 'done', 'p04': 'no entries', 'p05': 'done',
                       'p06': 'failed'}, res
        held = next(i for i, l in enumerate(logs) if l.startswith('p04:') and 'waits in town for p03' in l)
        p03_done = next(i for i, l in enumerate(logs) if l.startswith('p03 done - closed'))
        p04_closed = next(i for i, l in enumerate(logs) if l.startswith('p04 no entries') and l.endswith('closed'))
        assert held < p03_done and abs(p04_closed - p03_done) <= 2, (held, p03_done, p04_closed)
        t3 = r.results['p03']['at']
        t4 = r.results['p04']['at']
        assert abs(t3 - t4) < 3, 'p04 did not close with p03: %.1f s apart' % abs(t3 - t4)
        assert r.results['p06']['note'] == 'no partner left', r.results['p06']
        assert most <= 4, 'more than 4 open: %d' % most
        cfg = os.path.join(phbot, 'Config')
        lead = rc.read_json(os.path.join(cfg, 'Elysian_p03.json'))['Party']
        mem = rc.read_json(os.path.join(cfg, 'Elysian_p04.json'))['Party']
        assert lead['AutoInviteParty'] and lead['Invite From List'] and lead['InviteList'] == ['p04'], lead
        assert not lead['AutoAcceptParty'], lead
        assert mem['AutoAcceptParty'] and mem['Accept Invites From List'] and mem['Party Leader Names'] == ['p03'], mem
        assert not mem['AutoInviteParty'] and not mem['Accept All Invites'], mem
        p02 = rc.read_json(os.path.join(cfg, 'Elysian_p02.json'))['Party']
        assert p02['InviteList'] == ['p05'] and p02['AutoInviteParty'], 'p02 not the leader with p05: %s' % p02
        alive = [c.character for c in r.chars if c.proc and rc.process_alive(c.proc.pid)]
        assert not alive, alive
        print('OK - pairs (party of 2)')
    finally:
        rc.NO_ENTRIES_WAIT = no_entries_wait
        time.sleep(1)
        shutil.rmtree(work, ignore_errors=True)


def solo_party_test():
    """party_size 1 (Isis): every phBot profile stays solo"""
    c = rc.Char('u', 'p', '1', 'solo01', 'ch_nuker')
    work = tempfile.mkdtemp(prefix='mr_solo_')
    try:
        os.makedirs(os.path.join(work, 'Config'))
        rc.write_profile({'phbot_dir': work, 'server': 'Elysian', 'job_mastery': '', 'job_skills': []}, c, {}, {})
        p = rc.read_json(os.path.join(work, 'Config', 'Elysian_solo01.json'))['Party']
        assert not (p['AutoInviteParty'] or p['AutoAcceptParty'] or p['InviteList']), p
        print('OK - solo profile has no party')
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
    restart_test()
    solo_party_test()
    pairs_test()
    gate_test()
