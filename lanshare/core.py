import json
import os
import socket
import sys
from pathlib import Path


def app_dir():
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent


DEFAULTS = {
    'ftp': {'folder': '', 'port': '21', 'username': 'phone', 'password': '', 'write': False},
    'sftp': {'folder': '', 'port': '2222', 'username': 'phone', 'password': '', 'write': False},
    'smb': {'folder': '', 'share': 'PhoneShare', 'username': os.environ.get('USERNAME', ''), 'write': False},
}


def load_config(path):
    result = json.loads(json.dumps(DEFAULTS))
    if path.exists():
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError('config.json 必须是 JSON 对象')
        for protocol, defaults in result.items():
            section = data.get(protocol, {})
            if isinstance(section, dict):
                for key in defaults:
                    value = section.get(key, defaults[key])
                    defaults[key] = bool(value) if key == 'write' else str(value)
    return result


def atomic_json(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def validate(protocol, cfg):
    if not cfg['folder'].strip() or not Path(cfg['folder']).is_dir():
        raise ValueError('文件夹不存在，请重新选择目录')
    if not cfg['username'].strip():
        raise ValueError('请输入用户名')
    if protocol != 'smb':
        try:
            port = int(cfg['port'])
            if not 1 <= port <= 65535:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('端口必须是 1–65535 的整数') from None
        if not cfg['password']:
            raise ValueError('请输入密码')
        if protocol == 'ftp' and 50000 <= port <= 50100:
            raise ValueError('FTP 控制端口不能与被动数据端口 50000–50100 重叠，请更换端口')
    else:
        name = cfg['share'].strip()
        if not name or len(name) > 80 or any(c in name for c in '\\/:*?"<>|;=,+[]') or name.endswith(('.', ' ')):
            raise ValueError('共享名称不能为空、不能超过80个字符或含路径及特殊字符')


def check_port(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if os.name == 'nt':
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind(('0.0.0.0', int(port)))
        except OSError:
            raise ValueError(f'端口 {port} 已被占用或被系统保留，建议更换端口') from None


def ipv4_addresses():
    import psutil
    result = []
    stats = psutil.net_if_stats()
    for name, addresses in psutil.net_if_addrs().items():
        if name in stats and not stats[name].isup:
            continue
        for addr in addresses:
            if addr.family == socket.AF_INET and not addr.address.startswith(('127.', '169.254.')):
                result.append((name, addr.address))
    return result
