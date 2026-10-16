import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from .core import validate, check_port
from . import smb


class Controller:
    def __init__(self, directory, callback):
        self.directory = Path(directory)
        self.callback = callback
        self.active = None
        self.busy = False
        self.proc = None
        self.stopping = False
        self.record = None
        self.config = None
        self.lock = threading.RLock()
        self.journal = self.directory / '.smb-session.json'
        if self.journal.exists():
            self.record = json.loads(self.journal.read_text(encoding='utf-8'))
            self.active = 'smb'
            self.callback('recovery', '发现上次 SMB 共享记录。请以管理员权限点击“移除SMB共享”后再启动服务。')

    def emit(self, kind, text=''):
        self.callback(kind, text)

    def start(self, protocol, cfg):
        with self.lock:
            if self.active or self.busy:
                raise ValueError('已有服务正在运行，请先停止当前服务')
            validate(protocol, cfg)
            if protocol != 'smb':
                check_port(cfg['port'])
            self.active, self.busy, self.config = protocol, True, dict(cfg)
            self.stopping = False
        self.emit('starting', protocol)
        threading.Thread(target=self._start, args=(protocol, dict(cfg)), daemon=True).start()

    def _start(self, protocol, cfg):
        try:
            if protocol == 'smb':
                self.record = smb.create(cfg, self.journal)
                self.busy = False
                self.emit('ready', 'smb')
                threading.Thread(target=self._monitor_smb, daemon=True).start()
                return
            command = [sys.executable, '--worker'] if getattr(sys, 'frozen', False) else [sys.executable, str(self.directory / 'main.py'), '--worker']
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding='utf-8', errors='replace', bufsize=1,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), cwd=self.directory)
            self.proc = proc
            proc.stdin.write(json.dumps({'protocol': protocol, 'config': cfg, 'key_path': str(self.directory / 'sftp_host_key.pem')}) + '\n')
            proc.stdin.flush()
            def process_exited():
                # Windows venv launchers may have an additional Python child.
                # Closing its parent pipe also shuts that service down.
                proc.wait()
                try:
                    proc.stdin.close()
                except (OSError, ValueError):
                    pass
            threading.Thread(target=process_exited, daemon=True).start()
            ready = threading.Event()
            timed_out = threading.Event()
            def timeout():
                if not ready.wait(25) and proc.poll() is None:
                    timed_out.set()
                    proc.terminate()
            threading.Thread(target=timeout, daemon=True).start()
            error = ''
            for line in proc.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    self.emit('log', line.rstrip())
                    continue
                kind, message = event['kind'], event.get('message', '')
                if kind == 'ready':
                    ready.set()
                    self.busy = False
                    self.emit('ready', protocol)
                elif kind == 'error':
                    error = message
                    self.emit('log', message)
                else:
                    self.emit('log', message)
            proc.wait()
            ready.set()
            with self.lock:
                if self.proc is proc:
                    stopping = self.stopping
                    if not stopping:
                        self.proc = None
                        self.active = None
                        self.busy = False
                        self.emit('failed', error or ('服务启动超时' if timed_out.is_set() else f'{protocol.upper()} 服务进程意外退出，退出码 {proc.returncode}'))
            proc.stdout.close()
            proc.stdin.close()
        except Exception as exc:
            if protocol == 'smb' and self.journal.exists():
                try:
                    record = json.loads(self.journal.read_text(encoding='utf-8'))
                    if smb.inspect(record):
                        self.record = record
                        self.busy = False
                        self.emit('recovery', f'SMB 启动结果待清理：{exc}。请点击移除共享。')
                        return
                    self.journal.unlink(missing_ok=True)
                except Exception:
                    self.record = json.loads(self.journal.read_text(encoding='utf-8'))
                    self.busy = False
                    self.emit('recovery', f'SMB 状态无法确认：{exc}。保留共享记录，请以管理员权限移除。')
                    return
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                self.proc.wait(timeout=10)
            self.proc, self.active, self.busy = None, None, False
            self.emit('failed', str(exc))

    def stop(self):
        with self.lock:
            if self.busy:
                raise ValueError('正在启动或停止，请稍候')
            if not self.active:
                return
            self.busy = True
            self.stopping = True
        self.emit('stopping')
        threading.Thread(target=self._stop, daemon=True).start()

    def _stop(self):
        try:
            if self.active == 'smb':
                # An absent or replaced share can simply release our stale record.
                if not smb.is_admin():
                    raise ValueError('移除 SMB 共享需要管理员权限，请以管理员身份重新运行本软件。')
                if smb.inspect(self.record):
                    smb.remove(self.record, self.journal)
                else:
                    self.journal.unlink(missing_ok=True)
                self.record = None
            else:
                proc = self.proc
                if proc:
                    proc.stdin.close()
                    try:
                        proc.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        import psutil
                        try:
                            for child in psutil.Process(proc.pid).children(recursive=True):
                                child.kill()
                        except psutil.NoSuchProcess:
                            pass
                        proc.kill()
                        proc.wait(timeout=5)
            with self.lock:
                self.proc, self.active, self.busy = None, None, False
                self.stopping = False
            self.emit('stopped')
        except Exception as exc:
            self.busy = False
            self.stopping = False
            self.emit('stop_error', str(exc))

    def _monitor_smb(self):
        previous = set()
        record = self.record
        while self.active == 'smb' and self.record is record:
            time.sleep(5)
            if self.busy or self.active != 'smb':
                continue
            try:
                if not smb.inspect(record):
                    self.journal.unlink(missing_ok=True)
                    self.active, self.record = None, None
                    self.emit('failed', 'SMB 共享已被系统或其他程序移除')
                    return
                files = smb.connections(record)
                current = {str(f['FileId']) for f in files}
                for f in files:
                    if str(f['FileId']) not in previous:
                        self.emit('log', f"SMB 文件连接：{f['ClientComputerName']} / {f['ClientUserName']} / {f['ShareRelativePath']}")
                for file_id in previous - current:
                    self.emit('log', f'SMB 文件连接关闭：{file_id}')
                previous = current
            except Exception as exc:
                self.emit('log', f'SMB 状态查询失败：{exc}')
                # Keep ownership and mutual exclusion on an uncertain system state.
                return
