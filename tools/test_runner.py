"""Mass Runner test with a fake phBot (tools/fake_phbot.py): no game, no real accounts.

py -I tools/test_runner.py
12 characters, 10 in the finished list, one of them level 95: at most 3 open at a time, the level-95 one is
closed as not ready, fnuk06 gets the entry NPC's 271c (played today) and is closed as no entries, the others
write MoDungeon's "All 1 run(s) done" line and are closed as done.
"""
import json
import os
import shutil
import sys
import tempfile
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
            for i in range(1, 13):
                f.write('u%02d,p,1234567,fnuk%02d,\n' % (i, i))
        rc.write_json(os.path.join(work, 'finished.json'), {'characters': {
            'fnuk%02d' % i: {'level': 100} for i in range(1, 11)}})
        settings = os.path.join(work, 'settings.json')
        rc.write_json(settings, {
            'phbot_dir': phbot, 'phbot_dirs': [], 'phbot_command': [sys.executable, os.path.join(ROOT, 'tools',
                                                                                              'fake_phbot.py')],
            'login_args': ['--x'], 'accounts_csv': os.path.join(work, 'accounts.csv'),
            'finished_file': os.path.join(work, 'finished.json'), 'max_open': 3, 'launch_delay_seconds': 0.5,
            'builds_dir': os.path.join(ROOT, 'data', 'builds'), 'templates_dir': os.path.join(ROOT, 'templates'),
            'pick_template': os.path.join(ROOT, 'templates', 'pick_filter.db3'),
            'runs_dir': os.path.join(work, 'runs'), 'skip': os.path.join(work, 'skip.json'),
            'close_delay_seconds': 0.5})
        os.environ['FAKE_LOW'] = 'fnuk04'
        os.environ['FAKE_PLAYED'] = 'fnuk06'
        os.environ['FAKE_NO_QUIT'] = 'fnuk07'      # ignores the graceful close: terminated after QUIT_WAIT
        logs = []
        r = rc.Runner(settings, log=logs.append)
        assert [c.character for c in r.chars][0] == 'fnuk01' and len(r.chars) == 12, 'escort left out'
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
        quit = sorted(f[5:-4] for f in os.listdir(phbot) if f.startswith('quit_'))
        assert quit == ['fnuk%02d' % i for i in range(1, 11) if i != 7], quit     # closed by the graceful close
        assert not r.closing, 'bots still closing'
        alive = [c.character for c in r.chars if c.proc and rc.process_alive(c.proc.pid)]
        assert not alive, alive
        assert res['fnuk04']['result'] == 'not ready', res['fnuk04']
        assert res['fnuk06']['result'] == 'no entries', res['fnuk06']
        assert all(res['fnuk%02d' % i]['result'] == 'done' for i in range(1, 11) if i not in (4, 6))
        assert 'fnuk11' not in res and 'fnuk12' not in res, 'not finished characters were opened'
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


if __name__ == '__main__':
    main()
