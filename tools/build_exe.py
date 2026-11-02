"""Builds Mass Runner.exe in the Mass Runner folder with PyInstaller.

Run after changing anything in runner/:   py -I tools/build_exe.py
The exe reads settings.json, accounts.csv, data/ and templates/ from the folder it sits in.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = 'Mass Runner'      # its own program, not MassLeveler


def make_icon(path):
    """dark square with a green play arrow"""
    from PIL import Image, ImageDraw
    img = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, 248, 248), radius=48, fill=(30, 30, 30, 255), outline=(46, 125, 50, 255), width=10)
    d.polygon([(88, 56), (204, 128), (88, 200)], fill=(129, 199, 132, 255))
    img.save(path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def main():
    work = tempfile.mkdtemp(prefix='_build_', dir=ROOT)      # same drive as the exe, so it can be moved in place
    icon = os.path.join(work, 'massrunner.ico')
    try:
        make_icon(icon)
        icon_args = ['--icon', icon]
    except ImportError:
        icon_args = []
    cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed', '--name', NAME,
           '--uac-admin',     # phBot.exe requires administrator: only an elevated runner can open it
          
           '--paths', os.path.join(ROOT, 'runner'),
           '--distpath', os.path.join(work, 'dist'), '--workpath', os.path.join(work, 'build'),
           '--specpath', work] + icon_args + [os.path.join(ROOT, 'runner', 'mass_runner.py')]
    exe, old = os.path.join(ROOT, NAME + '.exe'), os.path.join(ROOT, NAME + '.old.exe')
    try:
        subprocess.run(cmd, check=True)
        if os.path.isfile(old):
            try:
                os.remove(old)
            except OSError:
                pass
        try:
            os.replace(os.path.join(work, 'dist', NAME + '.exe'), exe)
        except PermissionError:
            # the dashboard is open: Windows lets a running exe be renamed, the new one is used from its next start
            os.replace(exe, old if not os.path.isfile(old) else exe[:-4] + '.old%d.exe' % os.getpid())
            os.replace(os.path.join(work, 'dist', NAME + '.exe'), exe)
            print('Mass Runner is open - the new exe is used the next time you open it')
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('Built', exe)


if __name__ == '__main__':
    main()
