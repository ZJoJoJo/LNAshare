"""Windows native SMB management; never replaces a pre-existing share."""
import base64
import ctypes
import json
import os
import subprocess
import uuid

from .core import atomic_json


def is_admin():
    return os.name == 'nt' and bool(ctypes.windll.shell32.IsUserAnAdmin())


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def powershell(script):
    header = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
    encoded = base64.b64encode((header + script).encode('utf-16le')).decode('ascii')
    exe = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    result = subprocess.run([exe, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, encoding='utf-8', errors='replace', timeout=45,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'PowerShell 执行失败')
    return result.stdout.strip()


def create(cfg, journal):
    if not is_admin():
        raise ValueError('必须使用管理员权限启动软件才能创建 SMB 共享。请关闭软件后右键 EXE，选择“以管理员身份运行”。')
    name, folder, username = cfg['share'].strip(), os.path.abspath(cfg['folder']), cfg['username'].strip()
    if '\\' not in username and '@' not in username:
        username = os.environ.get('COMPUTERNAME', '.') + '\\' + username
    marker = 'LANSharePortable:' + uuid.uuid4().hex
    record = {'name': name, 'folder': folder, 'marker': marker}
    # Record ownership before creating the share so interruption can be recovered.
    atomic_json(journal, record)
    access = '-ChangeAccess' if cfg['write'] else '-ReadAccess'
    script = (
        "if((Get-Service LanmanServer).Status -ne 'Running'){throw 'Windows Server service is stopped. Enable file sharing manually.'}; "
        "if(!(Get-SmbServerConfiguration).EnableSMB2Protocol){throw 'SMB2/3 is disabled. Enable Windows SMB2/3 manually; SMB1 is not required.'}; "
        f"if(Get-SmbShare -Name {ps_literal(name)} -ErrorAction SilentlyContinue){{throw 'Share name already exists. Choose another name.'}}; "
        f"$account=[Security.Principal.NTAccount]::new({ps_literal(username)}); "
        "$null=$account.Translate([Security.Principal.SecurityIdentifier]); "
        f"New-SmbShare -Name {ps_literal(name)} -Path {ps_literal(folder)} -Description {ps_literal(marker)} "
        f"{access} {ps_literal(username)} | Out-Null"
    )
    powershell(script)
    return record


def remove(record, journal):
    if not is_admin():
        raise ValueError('移除本次共享需要管理员权限，请以管理员身份重新运行本软件。')
    # A unique marker plus path protects unrelated/replaced Windows shares.
    script = (
        f"$s=Get-SmbShare -Name {ps_literal(record['name'])} -ErrorAction SilentlyContinue; "
        "if($s){"
        f"if($s.Description -ne {ps_literal(record['marker'])} -or $s.Path -ne {ps_literal(record['folder'])})"
        "{throw 'Share ownership changed; refusing to remove another share.'}; "
        f"Remove-SmbShare -Name {ps_literal(record['name'])} -Force -Confirm:$false" + "}"
    )
    powershell(script)
    journal.unlink(missing_ok=True)


def inspect(record):
    script = (
        f"$s=Get-SmbShare -Name {ps_literal(record['name'])} -ErrorAction SilentlyContinue; "
        f"if($s -and $s.Description -eq {ps_literal(record['marker'])} -and $s.Path -eq {ps_literal(record['folder'])})"
        "{'owned'}else{'absent'}"
    )
    return powershell(script) == 'owned'


def connections(record):
    # Only files under our share are reported; system-wide SMB sessions are unrelated.
    folder = record['folder'].rstrip('\\') + '\\'
    output = powershell(
        f"@(Get-SmbOpenFile | Where-Object {{$_.Path.StartsWith({ps_literal(folder)}, [StringComparison]::OrdinalIgnoreCase)}} "
        "| Select-Object FileId,ClientComputerName,ClientUserName,ShareRelativePath) | ConvertTo-Json -Compress"
    )
    if not output:
        return []
    result = json.loads(output)
    return result if isinstance(result, list) else [result]
