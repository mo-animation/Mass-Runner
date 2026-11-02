"""Mass Runner dashboard: pick an event, Start, and every finished character plays it, at most 7 at a time.

Run:  py runner/mass_runner.py      (or Mass Runner.exe, built by tools/build_exe.py)
"""
import ctypes
import os
import queue
import shutil
import subprocess
import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runner_core as rc  # noqa: E402

DARK = {'bg': '#1e1e1e', 'panel': '#252526', 'field': '#2d2d30', 'line': '#3f3f46', 'fg': '#f0f0f0',
        'dim': '#8a8a8a', 'accent': '#0e639c', 'select': '#094771', 'hover': '#3e3e42',
        'done': '#81c784', 'bad': '#f14c4c', 'open': '#f2cc60'}
COLUMNS = (('ready', 'Leveling', 110), ('level', 'Lv', 40), ('state', 'Now', 260), ('result', 'Result', 260),
           ('at', 'When', 110))


def apply_dark(root):
    c = DARK
    st = ttk.Style(root)
    st.theme_use('clam')
    st.configure('.', background=c['bg'], foreground=c['fg'], fieldbackground=c['field'], bordercolor=c['line'],
                 darkcolor=c['bg'], lightcolor=c['bg'], troughcolor=c['panel'], selectbackground=c['select'],
                 selectforeground='#ffffff', insertcolor=c['fg'], arrowcolor=c['fg'], focuscolor=c['accent'])
    st.map('.', foreground=[('disabled', c['dim'])])
    st.configure('TButton', background=c['field'], padding=(10, 2))
    st.map('TButton', background=[('pressed', c['accent']), ('active', c['hover'])])
    st.configure('Accent.TButton', background=c['accent'], bordercolor=c['accent'], foreground='#ffffff')
    st.map('Accent.TButton', background=[('pressed', c['select']), ('active', '#1177bb')])
    st.configure('Treeview', background=c['panel'], fieldbackground=c['panel'], foreground=c['fg'], rowheight=20)
    st.map('Treeview', background=[('selected', c['select'])], foreground=[('selected', '#ffffff')])
    st.configure('Treeview.Heading', background=c['field'], foreground=c['fg'], relief='flat')
    st.map('Treeview.Heading', background=[('active', c['hover'])])
    for w in ('TEntry', 'TCombobox', 'TSpinbox'):
        st.configure(w, fieldbackground=c['field'], background=c['field'], foreground=c['fg'])
        st.map(w, fieldbackground=[('readonly', c['field'])], foreground=[('readonly', c['fg'])])
    st.map('TCheckbutton', background=[('active', c['bg'])])
    root.configure(bg=c['bg'])
    for key, val in (('*Background', c['bg']), ('*Foreground', c['fg']), ('*selectBackground', c['select']),
                     ('*selectForeground', '#ffffff'), ('*insertBackground', c['fg']),
                     ('*activeBackground', c['select']), ('*activeForeground', '#ffffff'),
                     ('*TCombobox*Listbox.background', c['field'])):
        root.option_add(key, val)


def dark_title(win):
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        v = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), 4)     # dark title bar
    except Exception:
        pass


class EventsWindow:
    """the events of the menu: name, type, MoDungeon profile, runs per character, timeout"""

    def __init__(self, app):
        self.app = app
        self.events = [dict(e) for e in app.runner.s.get('events') or []]
        top = self.top = tk.Toplevel(app.root)
        top.title('Events')
        top.geometry('640x340')
        dark_title(top)
        top.transient(app.root)
        left = ttk.Frame(top, padding=8)
        left.pack(side='left', fill='y')
        self.lst = tk.Listbox(left, width=24, bg=DARK['panel'], fg=DARK['fg'], highlightthickness=0,
                              exportselection=False)
        self.lst.pack(fill='y', expand=True)
        self.lst.bind('<<ListboxSelect>>', lambda e: self.show())
        row = ttk.Frame(left)
        row.pack(fill='x', pady=(6, 0))
        ttk.Button(row, text='Add', command=self.add).pack(side='left')
        ttk.Button(row, text='Delete', command=self.delete).pack(side='left', padx=4)
        form = ttk.Frame(top, padding=8)
        form.pack(side='left', fill='both', expand=True)
        self.vars = {k: tk.StringVar() for k in ('name', 'type', 'profile', 'runs', 'timeout_minutes')}
        profiles = rc.modungeon_profiles(app.runner.s)
        fields = (('name', 'Name', None), ('type', 'Plugin', list(rc.EVENT_TYPES)),
                  ('profile', 'MoDungeon profile', profiles), ('runs', 'Runs per character', None),
                  ('timeout_minutes', 'Give up after (min)', None))
        for i, (k, label, values) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky='w', pady=4)
            w = ttk.Combobox(form, textvariable=self.vars[k], values=values, width=30) if values else \
                ttk.Entry(form, textvariable=self.vars[k], width=33)
            w.grid(row=i, column=1, sticky='w', pady=4, padx=6)
        ttk.Label(form, foreground=DARK['dim'], wraplength=360, justify='left',
                  text='MoDungeon profiles are the routes saved in the MoDungeon plugin of the runner\'s phBot '
                       '(Profiles button). Every character loads the event\'s profile and starts by itself.'
                  ).grid(row=len(fields), column=0, columnspan=2, sticky='w', pady=(10, 0))
        bar = ttk.Frame(form)
        bar.grid(row=len(fields) + 1, column=0, columnspan=2, sticky='e', pady=(16, 0))
        ttk.Button(bar, text='Save', style='Accent.TButton', command=self.save).pack(side='left')
        ttk.Button(bar, text='Cancel', command=top.destroy).pack(side='left', padx=6)
        self.cur = None
        self.fill()
        if self.events:
            self.lst.selection_set(0)
            self.show()

    def fill(self):
        self.lst.delete(0, 'end')
        for e in self.events:
            self.lst.insert('end', e.get('name', '?'))

    def keep(self):
        if self.cur is None or self.cur >= len(self.events):
            return
        e = self.events[self.cur]
        e['name'] = self.vars['name'].get().strip() or 'Event'
        e['type'] = self.vars['type'].get().strip() or 'MoDungeon'
        e['profile'] = self.vars['profile'].get().strip() or 'Default'
        for k, d in (('runs', 1), ('timeout_minutes', 45)):
            v = self.vars[k].get().strip()
            e[k] = int(v) if v.isdigit() else d
        self.lst.delete(self.cur)
        self.lst.insert(self.cur, e['name'])

    def show(self):
        sel = self.lst.curselection()
        if not sel:
            return
        self.keep()
        self.cur = sel[0]
        self.lst.selection_set(self.cur)
        e = self.events[self.cur]
        for k in self.vars:
            self.vars[k].set(str(e.get(k, '')))

    def add(self):
        self.keep()
        self.events.append({'name': 'New event', 'type': 'MoDungeon', 'profile': 'Default', 'runs': 1,
                            'timeout_minutes': 45})
        self.fill()
        self.cur = None
        self.lst.selection_set(len(self.events) - 1)
        self.show()

    def delete(self):
        if self.cur is None:
            return
        del self.events[self.cur]
        self.cur = None
        self.fill()

    def save(self):
        self.keep()
        names = [e['name'] for e in self.events]
        if len(set(names)) != len(names):
            messagebox.showerror('Events', 'Two events have the same name.', parent=self.top)
            return
        changes = {'events': self.events}
        if self.app.runner.s.get('event') not in names and names:
            changes['event'] = names[0]
        self.app.runner.save_settings(changes)
        self.app.fill_events()
        self.top.destroy()


class App:
    def __init__(self, root):
        self.root = root
        self.logq = queue.Queue()
        root.title('Mass Runner')
        root.geometry('1000x680')
        apply_dark(root)
        dark_title(root)
        self.logfile = rc.path_in_root('data/mass_runner.log')
        self.runner = rc.Runner(log=self.log)

        bar = ttk.Frame(root, padding=(8, 8, 8, 4))
        bar.pack(fill='x')
        ttk.Label(bar, text='Event').pack(side='left')
        self.ev_var = tk.StringVar()
        self.ev_box = ttk.Combobox(bar, textvariable=self.ev_var, state='readonly', width=24)
        self.ev_box.pack(side='left', padx=(6, 4))
        self.ev_box.bind('<<ComboboxSelected>>', self.on_event)
        ttk.Button(bar, text='Events...', command=lambda: EventsWindow(self)).pack(side='left')
        ttk.Separator(bar, orient='vertical').pack(side='left', fill='y', padx=10)
        ttk.Button(bar, text='Start', style='Accent.TButton', command=self.runner.start).pack(side='left')
        ttk.Button(bar, text='Stop', command=lambda: self.runner.stop(False)).pack(side='left', padx=4)
        ttk.Button(bar, text='Close all', command=self.close_all).pack(side='left')
        ttk.Separator(bar, orient='vertical').pack(side='left', fill='y', padx=10)
        ttk.Label(bar, text='Max open').pack(side='left')
        self.max_var = tk.StringVar(value=str(self.runner.s['max_open']))
        sp = ttk.Spinbox(bar, from_=1, to=20, width=4, textvariable=self.max_var, command=self.on_max)
        sp.pack(side='left', padx=(6, 0))
        sp.bind('<FocusOut>', lambda e: self.on_max())
        self.only_var = tk.BooleanVar(value=bool(self.runner.s['only_finished']))
        ttk.Checkbutton(bar, text='Only finished characters', variable=self.only_var,
                        command=self.on_only).pack(side='left', padx=10)
        ttk.Button(bar, text='Update', command=self.on_update).pack(side='right')
        ttk.Button(bar, text='Reload', command=self.reload).pack(side='right', padx=4)

        bar2 = ttk.Frame(root, padding=(8, 0, 8, 4))
        bar2.pack(fill='x')
        self.dot = tk.Canvas(bar2, width=12, height=12, highlightthickness=0, bg=DARK['bg'])
        self.dot.pack(side='left')
        self.counts = ttk.Label(bar2, text='')
        self.counts.pack(side='left', padx=6)
        ttk.Button(bar2, text='New run', command=self.new_run).pack(side='right')
        ttk.Button(bar2, text='Retry failed', command=self.runner.retry_failed).pack(side='right', padx=4)
        self.fin_label = ttk.Label(bar2, foreground=DARK['dim'], text='')
        self.fin_label.pack(side='right', padx=10)

        pane = ttk.Panedwindow(root, orient='vertical')
        pane.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        frame = ttk.Frame(pane)
        self.tree = ttk.Treeview(frame, columns=[c for c, _, _ in COLUMNS], selectmode='extended')
        self.tree.heading('#0', text='Character')
        self.tree.column('#0', width=120, stretch=False)
        for c, h, w in COLUMNS:
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, stretch=c in ('state', 'result'))
        for tag, color in (('done', DARK['done']), ('bad', DARK['bad']), ('open', DARK['open']),
                           ('off', DARK['dim'])):
            self.tree.tag_configure(tag, foreground=color)
        sb = ttk.Scrollbar(frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        pane.add(frame, weight=4)
        self.logbox = tk.Text(pane, height=9, bg=DARK['panel'], fg=DARK['fg'], relief='flat', wrap='word',
                              font=('Consolas', 9))
        pane.add(self.logbox, weight=1)

        self.menu = tk.Menu(root, tearoff=0, bg=DARK['field'], fg=DARK['fg'])
        self.menu.add_command(label='Tick (plays)', command=lambda: self.each(lambda c: self.runner.set_skip(c, False)))
        self.menu.add_command(label='Untick (never opens)', command=lambda: self.each(lambda c: self.runner.set_skip(c, True)))
        self.menu.add_separator()
        self.menu.add_command(label='Play again (forget its result)', command=lambda: self.each(self.runner.reset_result))
        self.menu.add_command(label='Close its bot', command=lambda: self.each(lambda c: self.runner.close(c, 'closed by hand')))
        self.menu.add_separator()
        self.menu.add_command(label='Show phBot', command=lambda: self.each(lambda c: c.proc and rc.show_bot(c.proc.pid, True)))
        self.menu.add_command(label='Hide phBot', command=lambda: self.each(lambda c: c.proc and rc.show_bot(c.proc.pid, False)))
        self.menu.add_command(label='Show client', command=lambda: self.each(lambda c: c.proc and rc.show_client(c.proc.pid, True)))
        self.menu.add_command(label='Hide client', command=lambda: self.each(lambda c: c.proc and rc.show_client(c.proc.pid, False)))
        self.tree.bind('<Button-3>', self.on_menu)

        self.fill_events()
        root.protocol('WM_DELETE_WINDOW', self.on_close)
        self.runner._ensure_loop()
        if '--start' in sys.argv:
            root.after(1500, self.runner.start)
        self.update()

    def log(self, msg):
        """the window's log + data/mass_runner.log (from any thread)"""
        self.logq.put(msg)
        try:
            with open(self.logfile, 'a', encoding='utf-8') as f:
                f.write(time.strftime('[%d/%m %H:%M:%S] ') + msg + '\n')
        except OSError:
            pass

    # -------------------------------------------------- top bar
    def fill_events(self):
        names = [e['name'] for e in self.runner.s.get('events') or []]
        self.ev_box['values'] = names
        ev = self.runner.event()
        self.ev_var.set(ev['name'] if ev else '')

    def on_event(self, _=None):
        if self.runner.running:
            messagebox.showinfo('Mass Runner', 'Stop first - the open characters play the current event.')
            self.fill_events()
            return
        self.runner.save_settings({'event': self.ev_var.get()})

    def on_max(self):
        v = self.max_var.get().strip()
        if v.isdigit() and int(v) > 0 and int(v) != self.runner.s['max_open']:
            self.runner.save_settings({'max_open': int(v)})

    def on_only(self):
        self.runner.save_settings({'only_finished': bool(self.only_var.get())})

    def reload(self):
        self.runner.reload()
        self.fill_events()
        self.max_var.set(str(self.runner.s['max_open']))
        self.only_var.set(bool(self.runner.s['only_finished']))

    def close_all(self):
        if messagebox.askyesno('Mass Runner', 'Stop and terminate every open bot?'):
            self.runner.stop(True)

    def new_run(self):
        ev = self.runner.event()
        if ev and messagebox.askyesno('Mass Runner', 'Forget every result of "%s"?\nEvery character plays it '
                                                     'again on the next Start.' % ev['name']):
            self.runner.new_run()

    # -------------------------------------------------- list
    def selected(self):
        by = {c.character: c for c in self.runner.chars}
        return [by[i] for i in self.tree.selection() if i in by]

    def each(self, fn):
        for c in self.selected():
            fn(c)

    def on_menu(self, e):
        row = self.tree.identify_row(e.y)
        if row and row not in self.tree.selection():
            self.tree.selection_set(row)
        if self.tree.selection():
            self.menu.tk_popup(e.x_root, e.y_root)

    def update(self):
        while True:
            try:
                msg = self.logq.get_nowait()
            except queue.Empty:
                break
            self.logbox.insert('end', time.strftime('[%H:%M:%S] ') + msg + '\n')
            self.logbox.see('end')
        r = self.runner
        with r.lock:
            rows = []
            for c in r.chars:
                res = r.results.get(c.character) or {}
                why = r.ready(c)
                lvl = (r.finished or {}).get(c.character)
                ready = 'unticked' if c.skip else ('lv %d' % lvl if lvl else 'finished') \
                    if r.finished and c.character in r.finished else why or ''
                result = res.get('result', '') + (' - ' + res['note'] if res.get('note') else '')
                at = time.strftime('%d/%m %H:%M', time.localtime(res['at'])) if res.get('at') else ''
                alive = r.alive(c)
                state = c.state if alive or c.finish else 'waiting to open' if c.queued else \
                    (c.state if c.state and not res else '')
                tag = 'open' if alive else 'done' if res.get('result') in ('done', 'no entries') else \
                    'bad' if res else 'off' if why else ''
                rows.append((c.character, (ready, c.level or '', state, result, at), tag))
            n_open = r.open_count()
            todo = len(r.todo())
            done = sum(1 for x in r.results.values() if x.get('result') in ('done', 'no entries'))
            bad = len(r.results) - done
            fin = r.finished
            path = rc.finished_path(r.s)
            ev = r.event()
            running = r.running
        have = set(self.tree.get_children())
        for name, vals, tag in rows:
            if name in have:
                self.tree.item(name, values=vals, tags=(tag,))
            else:
                self.tree.insert('', 'end', iid=name, text=name, values=vals, tags=(tag,))
        for gone in have - {x[0] for x in rows}:
            self.tree.delete(gone)
        self.dot.delete('all')
        self.dot.create_oval(2, 2, 11, 11, fill=DARK['done'] if running else DARK['dim'], outline='')
        self.counts.config(text='%s   open %d/%s   left %d   done %d   failed %d' % (
            ev['name'] if ev else 'no event', n_open, r.s['max_open'], todo, done, bad))
        self.fin_label.config(text=('Finished list: %d characters' % len(fin)) if fin is not None else
                              'Finished list missing: %s' % path)
        self.root.after(1000, self.update)

    def on_update(self):
        """GitHub: pull the files changed there since the last update. tools/update.bat does it once this window
        is closed (the running exe cannot be replaced); open bots keep playing and are taken over when it opens
        again. On a PC without Git or the link it installs Git and links the folder first."""
        git = next((p for p in (shutil.which('git'), os.path.expandvars(r'%ProgramW6432%\Git\cmd\git.exe'),
                                r'C:\Program Files\Git\cmd\git.exe',
                                os.path.expandvars(r'%LOCALAPPDATA%\Programs\Git\cmd\git.exe'))
                    if p and os.path.isfile(p)), None)
        # safe.directory: git refuses a folder another Windows user owns (Mass Runner runs as administrator);
        # no fsync: some drives refuse it; unpackLimit 1: a fetch keeps GitHub's pack file
        run = lambda *a: subprocess.run([git, '-c', 'safe.directory=*', '-c', 'core.fsyncObjectFiles=false',
                                         '-c', 'core.fsync=none', '-c', 'fetch.unpackLimit=1'] + list(a),
                                        cwd=rc.ROOT, capture_output=True, text=True,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if not (git and os.path.isdir(rc.path_in_root('.git')) and
                run('rev-parse', '-q', '--verify', '@{u}').returncode == 0):
            if messagebox.askyesno('Update', 'First update on this PC: Mass Runner closes, Git is installed if it '
                                   'is missing (click Yes if Windows asks), the program files come from GitHub '
                                   'and Mass Runner opens again.\n\nsettings.json, accounts.csv, data\\skip.json '
                                   'and data\\runs stay as they are; open bots keep playing.\n\nContinue?'):
                self.close_and_run(r'tools\update.bat', git or '')
            return
        r = run('fetch', '--quiet')
        if r.returncode:
            messagebox.showwarning('Update', 'Could not reach GitHub:\n' + (r.stderr or r.stdout)[-500:])
            return
        files = run('diff', '--name-only', 'HEAD', '@{u}').stdout.split()
        if not files:
            messagebox.showinfo('Update', 'Already up to date.')
            return
        if not messagebox.askyesno('Update', '%d file(s) changed on GitHub:\n\n%s\n\nUpdate now? Mass Runner closes '
                                   'and opens again by itself; open bots keep playing.' % (
                                       len(files), '\n'.join(files[:20]) + ('\n...' if len(files) > 20 else ''))):
            return
        self.close_and_run(r'tools\update.bat', git)

    def close_and_run(self, script, *args):
        """run a tools .bat in its own window and close. PyInstaller's own variables stay out of its environment:
        the exe the .bat opens again would take this closed one for its parent and stop"""
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith('_PYI_')}
        env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        subprocess.Popen([rc.path_in_root(script)] + list(args), cwd=rc.ROOT, env=env,
                         creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
        self.runner.shutdown()
        self.root.destroy()

    def on_close(self):
        if self.runner.open_count() and not messagebox.askyesno(
                'Mass Runner', 'Bots are still open. Close Mass Runner anyway?\n(The bots keep playing; '
                               'reopen Mass Runner to take them over.)'):
            return
        self.runner.shutdown()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
