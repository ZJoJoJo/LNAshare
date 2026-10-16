import ftplib
import io
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import paramiko
from lanshare.core import validate, check_port, atomic_json, load_config
from lanshare.controller import Controller
from lanshare.smb import ps_literal


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Worker:
    def __init__(self, protocol, cfg, directory):
        exe = os.environ.get('LANSHARE_TEST_EXE')
        command = [exe, '--worker'] if exe else [sys.executable, str(ROOT / 'main.py'), '--worker']
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding='utf-8', cwd=ROOT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.events = queue.Queue()
        def reader():
            for line in self.proc.stdout:
                try:
                    self.events.put(json.loads(line))
                except ValueError:
                    self.events.put({'kind': 'log', 'message': line})
            self.events.put({'kind': 'exit'})
        threading.Thread(target=reader, daemon=True).start()
        self.proc.stdin.write(json.dumps({'protocol': protocol, 'config': cfg, 'key_path': str(directory / 'host_key.pem')}) + '\n')
        self.proc.stdin.flush()
        deadline = time.monotonic() + 35
        self.logs = []
        while time.monotonic() < deadline:
            event = self.events.get(timeout=35)
            self.logs.append(event)
            if event['kind'] == 'ready':
                return
            if event['kind'] in ('error', 'exit'):
                self.stop()
                raise RuntimeError(str(self.logs))
        self.stop()
        raise RuntimeError('worker startup timeout')

    def stop(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        self.proc.stdin.close()
        self.proc.stdout.close()


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.test-artifacts').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.test-artifacts')
        self.directory = Path(self.temp.name)
        self.share = self.directory / '共享目录'
        self.share.mkdir()
        (self.share / '中文文件.txt').write_bytes('你好，手机'.encode())
        (self.directory / 'outside.txt').write_text('private')
        self.workers = []

    def tearDown(self):
        for worker in self.workers:
            worker.stop()
        self.temp.cleanup()

    def cfg(self, write=False):
        return {'folder': str(self.share), 'port': str(free_port()), 'username': 'phone', 'password': 'Test密码123!', 'write': write}

    def start(self, protocol, write=False):
        cfg = self.cfg(write)
        worker = Worker(protocol, cfg, self.directory)
        self.workers.append(worker)
        return cfg, worker

    def ftp(self, cfg):
        client = ftplib.FTP()
        client.connect('127.0.0.1', int(cfg['port']), timeout=8)
        client.login(cfg['username'], cfg['password'])
        self.addCleanup(client.close)
        return client

    def sftp(self, cfg):
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect('127.0.0.1', int(cfg['port']), username=cfg['username'], password=cfg['password'], look_for_keys=False, allow_agent=False, timeout=8)
        client = ssh.open_sftp()
        self.addCleanup(ssh.close)
        self.addCleanup(client.close)
        return client, ssh

    def test_ftp_read_only_and_boundary(self):
        cfg, _ = self.start('ftp')
        ftp = self.ftp(cfg)
        self.assertIn('中文文件.txt', ftp.nlst())
        data = bytearray()
        ftp.retrbinary('RETR 中文文件.txt', data.extend)
        self.assertEqual(data, '你好，手机'.encode())
        with self.assertRaises(ftplib.error_perm):
            ftp.storbinary('STOR forbidden.txt', io.BytesIO(b'x'))
        with self.assertRaises(ftplib.error_perm):
            ftp.delete('中文文件.txt')
        with self.assertRaises(ftplib.error_perm):
            ftp.retrbinary('RETR ../outside.txt', lambda _: None)
        with self.assertRaises(ftplib.error_perm):
            ftp.retrbinary('RETR NUL', lambda _: None)
        ftp.quit()

    def test_ftp_read_write(self):
        cfg, _ = self.start('ftp', True)
        ftp = self.ftp(cfg)
        ftp.mkd('新目录')
        ftp.storbinary('STOR 新目录/upload.bin', io.BytesIO(b'hello' * 5000))
        ftp.rename('新目录/upload.bin', '新目录/renamed.bin')
        data = bytearray()
        ftp.retrbinary('RETR 新目录/renamed.bin', data.extend)
        self.assertEqual(bytes(data), b'hello' * 5000)
        ftp.delete('新目录/renamed.bin')
        ftp.rmd('新目录')
        ftp.quit()

    def test_ftp_bad_password(self):
        cfg, _ = self.start('ftp')
        ftp = ftplib.FTP()
        try:
            ftp.connect('127.0.0.1', int(cfg['port']), timeout=8)
            with self.assertRaises(ftplib.error_perm):
                ftp.login('phone', 'wrong')
        finally:
            ftp.close()

    def test_sftp_read_only_and_boundary(self):
        cfg, _ = self.start('sftp')
        sftp, _ = self.sftp(cfg)
        self.assertIn('中文文件.txt', sftp.listdir('/'))
        with sftp.open('/中文文件.txt', 'rb') as stream:
            self.assertEqual(stream.read(), '你好，手机'.encode())
        for path in ['/../outside.txt', '/C:/Windows/win.ini', '/中文文件.txt:stream', '\\..\\outside.txt', '/NUL', '/.. /outside.txt']:
            with self.assertRaises(OSError, msg=path):
                sftp.open(path, 'rb')
        with self.assertRaises(OSError):
            sftp.open('/forbidden.txt', 'wb')
        with self.assertRaises(OSError):
            sftp.remove('/中文文件.txt')
        with self.assertRaises(OSError):
            sftp.mkdir('/forbidden')
        sftp.close()

    def test_sftp_read_write(self):
        cfg, _ = self.start('sftp', True)
        sftp, ssh = self.sftp(cfg)
        sftp.mkdir('/目录')
        with sftp.open('/目录/test.bin', 'wb') as stream:
            stream.write(b'payload' * 10000)
        sftp.rename('/目录/test.bin', '/目录/renamed.bin')
        with sftp.open('/目录/renamed.bin', 'rb') as stream:
            self.assertEqual(stream.read(), b'payload' * 10000)
        with sftp.open('/目录/renamed.bin', 'ab') as stream:
            stream.write(b'end')
        self.assertEqual(sftp.stat('/目录/renamed.bin').st_size, 70003)
        sftp.remove('/目录/renamed.bin')
        sftp.rmdir('/目录')
        with self.assertRaises(OSError):
            sftp.rmdir('/')
        # No shell/exec/forwarding is exposed by the SSH transport.
        with self.assertRaises(paramiko.SSHException):
            ssh.exec_command('whoami')
        sftp.close()

    def test_sftp_bad_password(self):
        cfg, _ = self.start('sftp')
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            with self.assertRaises(paramiko.AuthenticationException):
                ssh.connect('127.0.0.1', int(cfg['port']), username='phone', password='wrong', look_for_keys=False, allow_agent=False, timeout=8)
        finally:
            ssh.close()

    def test_pipe_close_stops_worker(self):
        cfg, worker = self.start('ftp')
        worker.proc.stdin.close()
        worker.proc.wait(timeout=8)
        with socket.socket() as sock:
            self.assertNotEqual(sock.connect_ex(('127.0.0.1', int(cfg['port']))), 0)

    def test_preflight_and_password_config(self):
        cfg = self.cfg()
        validate('ftp', cfg)
        with socket.socket() as sock:
            sock.bind(('0.0.0.0', int(cfg['port'])))
            sock.listen()
            with self.assertRaises(ValueError):
                check_port(cfg['port'])
        for port in ('0', '65536', 'abc', '50001'):
            with self.assertRaises(ValueError):
                validate('ftp', dict(cfg, port=port))
        with self.assertRaises(ValueError):
            validate('sftp', dict(cfg, folder=str(self.directory / 'missing')))
        path = self.directory / 'config.json'
        atomic_json(path, {'ftp': cfg})
        self.assertEqual(load_config(path)['ftp']['password'], cfg['password'])
        self.assertIn(cfg['password'], path.read_text(encoding='utf-8'))

    def test_controller_mutex_stop_crash(self):
        # Controller launches source worker; frozen worker is covered separately.
        events = queue.Queue()
        controller = Controller(ROOT, lambda k, t: events.put((k, t)))
        def wait_for(kind):
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                event, text = events.get(timeout=35)
                if event == kind:
                    return text
                if event == 'failed' and kind != 'failed':
                    self.fail(text)
            self.fail('missing event ' + kind)
        cfg = self.cfg()
        try:
            controller.start('ftp', cfg)
            with self.assertRaisesRegex(ValueError, '已有服务正在运行'):
                controller.start('sftp', cfg)
            with self.assertRaisesRegex(ValueError, '已有服务正在运行'):
                controller.start('smb', cfg)
            wait_for('ready')
            controller.stop()
            wait_for('stopped')
            self.assertIsNone(controller.active)
            check_port(cfg['port'])
            controller.start('ftp', cfg)
            wait_for('ready')
            controller.proc.kill()
            wait_for('failed')
            self.assertIsNone(controller.active)
        finally:
            if controller.proc and controller.proc.poll() is None:
                controller.proc.kill()
                controller.proc.wait(timeout=8)

    def test_smb_command_quoting(self):
        self.assertEqual(ps_literal("x'; Stop-Service test; '"), "'x''; Stop-Service test; '''")


if __name__ == '__main__':
    unittest.main(verbosity=2)
