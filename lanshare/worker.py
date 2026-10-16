import json
import logging
import os
import sys
import threading


def emit(kind, message=''):
    print(json.dumps({'kind': kind, 'message': message}, ensure_ascii=True), flush=True)


class EventLog(logging.Handler):
    def emit(self, record):
        emit('log', self.format(record))


def main():
    # Credentials arrive through an anonymous pipe, never command-line arguments.
    payload = json.loads(sys.stdin.readline())
    cfg = payload['config']
    def parent_watch():
        sys.stdin.read()
        os._exit(0)
    threading.Thread(target=parent_watch, daemon=True).start()
    logging.basicConfig(level=logging.INFO, handlers=[EventLog()], format='%(levelname)s %(message)s', force=True)
    logging.getLogger('paramiko').setLevel(logging.WARNING)
    try:
        if payload['protocol'] == 'ftp':
            from pyftpdlib.authorizers import DummyAuthorizer
            from pyftpdlib.handlers import FTPHandler
            from pyftpdlib.servers import FTPServer
            from pyftpdlib.filesystems import AbstractedFS
            class SafeFS(AbstractedFS):
                def validpath(self, path):
                    if os.name == 'nt' and os.path.isreserved(path):
                        return False
                    return super().validpath(path)
            authorizer = DummyAuthorizer()
            authorizer.add_user(cfg['username'], cfg['password'], cfg['folder'], perm='elradfmwMT' if cfg['write'] else 'elr')
            class Handler(FTPHandler):
                def on_connect(self):
                    logging.info('FTP 客户端连接：%s:%s', self.remote_ip, self.remote_port)
                def on_disconnect(self):
                    logging.info('FTP 客户端断开：%s', self.remote_ip)
            Handler.authorizer = authorizer
            Handler.abstracted_fs = SafeFS
            Handler.passive_ports = range(50000, 50101)
            Handler.banner = 'LAN Share FTP'
            Handler.permit_foreign_addresses = False
            server = FTPServer(('0.0.0.0', int(cfg['port'])), Handler)
            server.max_cons = 64
            server.max_cons_per_ip = 16
            emit('ready')
            server.serve_forever()
        else:
            from .sftp import serve
            serve(cfg, payload['key_path'], lambda: emit('ready'))
    except Exception as exc:
        emit('error', str(exc))
        raise SystemExit(1)
