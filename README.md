# Mass Runner

Plays an event (Isis dungeon first) with the characters MassLeveler Escort finished on the other PC.
All characters are in one list; at most **Max open** (7) bots are open at a time. A character that is done is
closed (phBot terminated) and the next one opens in its place, until everyone has played.

## How it works

1. **Start** opens the ticked characters that are in the finished list and have no result yet, one every
   `launch_delay_seconds` (20 s), with auto login (accounts.csv, same file as MassLeveler Escort; the escort row
   is left out).
2. Before a character opens, its files in the runner's phBot (`D:/Games/silkroad/bots/phBot Runner`) are written:
   - `Config/Elysian_<char>.json`: the build's template (`templates/<build>.json`, default `ch_nuker`), solo,
     the skills of the build's last bracket, **Auto Mastery** masteries + skills of the build **plus the job
     mastery and job skills** (`job_mastery`, `job_skills`), Stat Points.
   - `Config/MoDungeon_<char>.json`: the event's MoDungeon profile, **Auto start** on, skills from phBot.
3. MoDungeon starts by itself in the game. Mass Runner reads phBot's log: `All N run(s) done` (written after the
   pet's items went to the inventory) = done → the bot is closed 5 s later.
   `No more entries` = done (entry limit), closed 90 s later after MoDungeon's return scroll.
   `NPC refused, code 271c` = the entry NPC says the character already played today (MoDungeon would only retry):
   "no entries", closed 5 s later. (291c = the dungeon is full for now: MoDungeon retries, it gets in later.)
   Died / no route / did not get in / error / no result after the event's timeout = failed, closed.
4. A character below `min_level` (100) is closed again as **not ready**. A character already logged in from
   another phBot folder (the farm bots) is never opened (it would be kicked there).
5. **Stuck at the login** (no server in the list, the character missing from the character list, never
   connected): after `login_timeout_minutes` (4) its bot is restarted by itself, up to `login_restarts` (10) times,
   then failed. Right-click **Restart bot** does it at once (also while stopped, no try used).
6. **Game files locked**: before each bot opens, the client's .pk2 files are opened the way a starting client
   opens them. While one is held with write access, every new game client crashes at start (GFXFileManager.dll,
   `Dump\0N[...].dmp` in the client folder) and its phBot stays "Disconnected". Then no bot opens, the log names
   who has the file open, and the window shows GAME FILES LOCKED until it is free. On 2 and 3 Nov it was freed by
   closing the other phBot Manager (its long-running clients).

## Finished list (link with the other PC)

MassLeveler Escort writes `<OneDrive>/MassLeveler/finished.json` on the other PC. Mass Runner reads the same file
here (`finished_file` = another path); the OneDrive app must be signed in to the same account on both PCs.
A MassLeveler Escort on **this** PC counts too: its `data/progress.json` (done / finished) is read directly from
the `MassLeveler Escort` folder next to Mass Runner and from every folder in settings `massleveler_dirs`.
Untick **Only finished characters** to play everyone in accounts.csv.

## Accounts

Mass Runner takes the characters from:
- **MassLeveler Escort's `accounts.csv`**, read where it is (never changed): the `MassLeveler Escort` folder next to
  Mass Runner's folder or one level higher (`D:\silkroad\new\mass runner\Mass Runner` finds
  `D:\silkroad\new\MassLeveler Escort`), or the folder set in Settings. Its finished characters play.
- `accounts.csv` in Mass Runner's folder, if there is one (old setups).
- **Accounts** button: the Excel sheet `extra_accounts.xlsx` in Mass Runner's folder (made the first time, opened
  in Excel). One row per account: `username, password, passcode, character` (`build` optional, default
  `ch_nuker`). Save it and the list updates by itself. These characters play **without** the finished list
  (Leveling shows "added by hand"); a character in both lists counts as added by hand.

The escort row (`escort` = yes) never plays. Close a character everywhere else before it plays (a farm bot on
another PC kicks it out, and with --relogdc kicks it back).

## Events

**Events...**: name, plugin (MoDungeon), MoDungeon profile, runs per character, give up after (min). A new dungeon =
save its route as a profile in MoDungeon (Profiles button) in the runner's phBot, then add an event with that
profile. Results of every event are kept in `data/runs/<event>.json`: **New run** forgets them (next day),
**Retry failed** plays the failed / not ready ones again, right-click → **Play again** for one character.

## Updates (GitHub)

The program is on GitHub: https://github.com/mo-animation/Mass-Runner. **Update** (top right) gets what changed
there and reopens Mass Runner; open bots keep playing. On a new PC: copy the folder (or only `Mass Runner.exe` +
`tools/`), press Update once (it installs Git if needed and links the folder). `settings.json`, `accounts.csv`,
`data/skip.json` and `data/runs` stay as each PC has them; accounts.csv is never uploaded.
A new PC also needs its own phBot copy for the runner (phBot + MoDungeon.py + `Config/MoDungeon.<profile>.json`)
at the path in settings `phbot_dir` / `phbot_dirs`. Mass Runner runs as administrator (phBot requires it).

## Settings

**Settings** (right side, under New run): the phBot folder Mass Runner opens (shown on the left of that line; it must have phBot.exe,
and Plugins\MoDungeon.py for MoDungeon events), the finished list file (empty = OneDrive), a MassLeveler Escort
folder on this PC, the timings, the lowest level that plays and hiding the game clients. Saved in settings.json.

## Files

- `runner/runner_core.py` (the runner), `runner/mass_runner.py` (the window)
- `py -I tools/test_runner.py`: test with a fake phBot; `py -I tools/build_exe.py`: rebuild `Mass Runner.exe`
