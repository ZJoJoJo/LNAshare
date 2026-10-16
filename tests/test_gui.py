import contextlib
import json
from pathlib import Path
import socket
import sys
import tempfile
import time
import tkinter as tk
import subprocess
import ctypes
import shutil
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
(ROOT / '.test-artifacts').mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))
from lanshare.gui import App


def pump(root, predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError('GUI operation timed out')


with tempfile.TemporaryDirectory(dir=ROOT / '.test-artifacts') as folder:
    directory = Path(folder)
    with patch('lanshare.gui.app_dir', return_value=directory), contextlib.ExitStack() as stack:
        info = stack.enter_context(patch('lanshare.gui.messagebox.showinfo'))
        warning = stack.enter_context(patch('lanshare.gui.messagebox.showwarning'))
        error = stack.enter_context(patch('lanshare.gui.messagebox.showerror'))
        root = tk.Tk()
        root.withdraw()
        app = App(root)
        # Config goes into the temporary portable directory; source worker is real.
        app.controller.directory = ROOT
        cfg = app.vars['ftp']
        cfg['folder'].set(folder)
        cfg['username'].set('phone')
        cfg['password'].set('gui-password')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cfg['port'].set(str(sock.getsockname()[1]))
        pump(root, lambda: (directory / 'config.json').exists())
        assert json.loads((directory / 'config.json').read_text(encoding='utf-8'))['ftp']['password'] == 'gui-password'
        app.start('ftp')
        pump(root, lambda: '运行中' in app.status.get())
        assert 'FTP' in app.status.get()
        assert app.start_buttons['ftp'].instate(['disabled'])
        assert app.stop_buttons['ftp'].instate(['!disabled'])
        app.start('sftp')
        assert warning.called
        assert '已有服务正在运行' in warning.call_args.args[1]
        assert '启动成功' in app.logs.get('1.0', 'end')
        app.stop()
        pump(root, lambda: app.status.get() == '● 空闲')
        assert not error.called, error.call_args
        app.start('smb')
        assert warning.called
        assert not app.controller.active
        if '--preview' in sys.argv:
            # Pillow is provided by the host Python; used only for our own GUI QA.
            sys.path.append(str(Path(sys.base_prefix) / 'Lib' / 'site-packages'))
            from PIL import ImageGrab
            root.deiconify()
            root.attributes('-topmost', True)
            root.update()
            for p in ('ftp', 'sftp', 'smb'):
                app.panels[p].master.select(app.panels[p])
                root.update()
                time.sleep(.3)
                x, y = root.winfo_rootx(), root.winfo_rooty()
                ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(ROOT / '.test-artifacts' / f'preview-{p}.png')
        app.close()
print('GUI configuration, mutex, FTP start/stop, logs, SMB admin guard: PASS')

if '--portable' in sys.argv:
    with tempfile.TemporaryDirectory(prefix='搬移测试 ', dir=ROOT / '.test-artifacts', ignore_cleanup_errors=True) as folder:
        directory = Path(folder) / '软件目录'
        if '--single' in sys.argv:
            directory.mkdir()
            shutil.copy2(ROOT / 'dist' / 'LANSharePortable.exe', directory / 'LANShare.exe')
        else:
            shutil.copytree(ROOT / 'dist' / 'LANShare', directory)
        environment = dict(__import__('os').environ)
        environment.pop('PYTHONHOME', None)
        environment.pop('PYTHONPATH', None)
        environment['PATH'] = __import__('os').environ.get('SystemRoot', r'C:\Windows') + '\\System32'
        proc = subprocess.Popen([str(directory / 'LANShare.exe')], cwd=folder, env=environment)
        user = ctypes.windll.user32
        kernel = ctypes.windll.kernel32
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        handles = []
        import psutil
        def each(hwnd, _):
            pid = ctypes.c_ulong()
            user.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
            length = user.GetWindowTextLengthW(ctypes.c_void_p(hwnd))
            title = ctypes.create_unicode_buffer(length + 1)
            user.GetWindowTextW(ctypes.c_void_p(hwnd), title, length + 1)
            family = {proc.pid} | {child.pid for child in psutil.Process(proc.pid).children(recursive=True)}
            if pid.value in family and title.value == '局域网文件共享工具':
                handles.append(hwnd)
            return True
        callback = callback_type(each)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not handles:
                user.EnumWindows(callback, None)
                if proc.poll() is not None:
                    raise AssertionError(f'Portable GUI exited: {proc.returncode}')
                time.sleep(.1)
            assert handles, 'Portable GUI window not found'
            time.sleep(.5)
            user.PostMessageW(ctypes.c_void_p(handles[0]), 0x0010, 0, 0)
            assert proc.wait(timeout=15) == 0
            assert (directory / 'config.json').is_file(), 'Config must be beside the relocated EXE'
            print('Relocated Windows EXE GUI with Unicode/space path, clean PATH and automatic config: PASS')
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
