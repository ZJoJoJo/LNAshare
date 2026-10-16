"""Paramiko SFTP adapter: one password account and a confined shared directory."""
import hmac
import logging
import os
import socket
import stat
import threading
from pathlib import Path

import paramiko

log = logging.getLogger('sftp')


class PasswordServer(paramiko.ServerInterface):
    def __init__(self, cfg):
        self.cfg = cfg

    def get_allowed_auths(self, username):
        return 'password'

    def check_auth_password(self, username, password):
        good = hmac.compare_digest(username.encode(), self.cfg['username'].encode())
        good &= hmac.compare_digest(password.encode(), self.cfg['password'].encode())
        log.info('SFTP 密码认证%s', '成功' if good else '失败')
        return paramiko.AUTH_SUCCESSFUL if good else paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == 'session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class FileHandle(paramiko.SFTPHandle):
    def __init__(self, flags, filename, writable):
        super().__init__(flags)
        self.filename = filename
        self.writable = writable

    def stat(self):
        try:
            return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def chattr(self, attr):
        # Changing file attributes is deliberately unsupported; uploads use open/write.
        return paramiko.SFTP_PERMISSION_DENIED if not self.writable else paramiko.SFTP_OP_UNSUPPORTED

    def close(self):
        super().close()
        log.info('SFTP 文件传输关闭：%s', self.filename)


class RootedSFTP(paramiko.SFTPServerInterface):
    def __init__(self, server, *args, cfg=None, **kwargs):
        super().__init__(server, *args, **kwargs)
        self.root = Path(cfg['folder']).resolve()
        self.writable = cfg['write']

    def local(self, path):
        # Reject Windows drive names, alternate streams and reparse points.
        path = path.replace('\\', '/')
        if ':' in path or '\x00' in path:
            raise PermissionError(13, '非法路径')
        if os.name == 'nt' and any(os.path.isreserved(part) for part in path.split('/') if part not in ('', '.', '..')):
            raise PermissionError(13, '不允许 Windows 设备名或保留路径')
        target = self.root / path.lstrip('/')
        resolved = target.resolve()
        if not resolved.is_relative_to(self.root):
            raise PermissionError(13, '路径超出共享目录')
        current = self.root
        for part in target.relative_to(self.root).parts:
            current = current / part
            if current.exists() or current.is_symlink():
                st = current.lstat()
                if stat.S_ISLNK(st.st_mode) or getattr(st, 'st_file_attributes', 0) & 0x400:
                    raise PermissionError(13, '不允许访问链接或目录联接')
        return resolved

    def canonicalize(self, path):
        import posixpath
        return posixpath.normpath('/' + path.replace('\\', '/').lstrip('/'))

    def list_folder(self, path):
        try:
            result = []
            for child in self.local(path).iterdir():
                st = child.lstat()
                if stat.S_ISLNK(st.st_mode) or getattr(st, 'st_file_attributes', 0) & 0x400:
                    continue
                item = paramiko.SFTPAttributes.from_stat(st)
                item.filename = child.name
                result.append(item)
            log.info('SFTP 浏览目录：%s', path)
            return result
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def stat(self, path):
        try:
            return paramiko.SFTPAttributes.from_stat(self.local(path).stat())
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    lstat = stat

    def open(self, path, flags, attr):
        writing = flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC | os.O_EXCL)
        if writing and not self.writable:
            return paramiko.SFTP_PERMISSION_DENIED
        try:
            local = self.local(path)
            fd = os.open(local, flags | getattr(os, 'O_BINARY', 0), 0o666)
            mode = 'r+b' if flags & os.O_RDWR else ('wb' if flags & os.O_WRONLY else 'rb')
            file = os.fdopen(fd, mode)
            handle = FileHandle(flags, path, bool(writing))
            handle.readfile = file
            if writing:
                handle.writefile = file
            log.info('SFTP %s文件：%s', '上传/写入' if writing else '下载/读取', path)
            return handle
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def mutate(self, operation, *paths):
        if not self.writable:
            return paramiko.SFTP_PERMISSION_DENIED
        try:
            targets = [self.local(p) for p in paths]
            if self.root in targets:
                return paramiko.SFTP_PERMISSION_DENIED
            operation(*targets)
            log.info('SFTP %s：%s', operation.__name__, ' → '.join(paths))
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def remove(self, path):
        return self.mutate(os.remove, path)

    def rename(self, oldpath, newpath):
        return self.mutate(os.rename, oldpath, newpath)

    def mkdir(self, path, attr):
        return self.mutate(os.mkdir, path)

    def rmdir(self, path):
        return self.mutate(os.rmdir, path)

    def chattr(self, path, attr):
        return paramiko.SFTP_PERMISSION_DENIED if not self.writable else paramiko.SFTP_OP_UNSUPPORTED


def serve(cfg, key_path, ready):
    key_path = Path(key_path)
    if key_path.exists():
        key = paramiko.RSAKey.from_private_key_file(str(key_path))
    else:
        key = paramiko.RSAKey.generate(3072)
        temp = key_path.with_suffix('.tmp')
        key.write_private_key_file(str(temp))
        os.replace(temp, key_path)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == 'nt':
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    listener.bind(('0.0.0.0', int(cfg['port'])))
    listener.listen(32)
    ready()

    def client(conn, addr):
        log.info('SFTP 客户端连接：%s:%s', *addr)
        transport = None
        try:
            conn.settimeout(30)
            transport = paramiko.Transport(conn)
            transport.add_server_key(key)
            transport.set_subsystem_handler('sftp', paramiko.SFTPServer, RootedSFTP, cfg=cfg)
            transport.start_server(server=PasswordServer(cfg))
            while transport.is_active():
                threading.Event().wait(0.5)
        except Exception as exc:
            log.warning('SFTP 连接异常：%s', exc)
        finally:
            if transport:
                transport.close()
            conn.close()
            log.info('SFTP 客户端断开：%s:%s', *addr)

    while True:
        conn, addr = listener.accept()
        threading.Thread(target=client, args=(conn, addr), daemon=True).start()
