# 局域网多协议文件共享工具

Windows 10/11 x64 桌面便携程序。FTP、SFTP、Windows 原生 SMB 三种模式严格互斥，提供单账号、只读/读写权限、IPv4 列表、状态与日志。

本目录为 GitHub 开发源码。与它同级的 `02_便携软件` 保存已经打包的可运行 EXE；此目录中的 `dist/` 是本地构建产物，不提交 GitHub。

## 上传 GitHub

源码包包含入口 `main.py`、`lanshare/`、`tests/`、依赖清单、构建脚本和说明文件，可将这些文件作为仓库根目录上传。开发源码 ZIP 已排除虚拟环境、构建缓存、可执行文件、测试临时文件、账号配置及服务器身份文件。

如果使用 GitHub Desktop，选择本目录建立本地仓库，再发布到自己的 GitHub 仓库即可。`.gitignore` 会忽略本地生成文件。使用网页上传时，请上传源码 ZIP 解压后的内容，不要只把 ZIP 本身上传为源码。

运行 `python export_source.py` 可重新生成干净的 `LANShare-GitHub-Source.zip`，便于备份或上传。该脚本使用源码文件清单导出，不包含本机缓存和运行配置。

已有的 `.venv/`、`build/`、`build-single/`、`dist/`、`.test-artifacts/`、`third-party-licenses/` 仅保留作本机开发缓存，均不会进入源码包。首次下载源码不需要它们，按下方构建命令即可重新生成。

## 使用

单文件版：复制 `dist/LANSharePortable.exe` 到其他电脑的可写入目录，双击启动，不需要安装 Python。配置和身份文件在 EXE 旁生成；要保留配置可一起复制这些文件。

目录版：解压 `dist/LANShare-Windows-x64.zip`，双击其中的 `LANShare/LANShare.exe`。移动时复制整个文件夹，必须保留 `_internal`。

SMB 需要右键以管理员身份运行，系统 SMB2/3 与 Server 服务可用，并手动放行专用网络 TCP445。FTP 被动数据端口为 50000–50100，SFTP 默认端口 2222。所有协议的实际网络连通性取决于目标电脑的防火墙与局域网配置。

配置保存在 EXE 旁的 `config.json`，密码明文。首次启动 SFTP 会生成 `sftp_host_key.pem`。SMB 运行期间创建 `.smb-session.json`，正常停止后删除。详细说明见 `使用说明.txt`。

## 开发和构建

使用 Python 3.13 x64，在 PowerShell 中运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

`build.ps1` 生成自带 Python、Tcl/Tk 和协议依赖的单文件 EXE、目录版 EXE 与 ZIP，并附第三方许可证。GUI 默认普通权限，SMB 按需使用系统右键管理员启动。不要将构建机器的测试配置或服务器身份文件加入发布包。

## 验证

```powershell
.\.venv\Scripts\python.exe tests\test_integration.py
.\.venv\Scripts\python.exe tests\test_smb.py
.\.venv\Scripts\python.exe tests\test_gui.py
$env:LANSHARE_TEST_EXE = (Resolve-Path 'dist\LANShare\LANShare.exe').Path
.\.venv\Scripts\python.exe tests\test_integration.py
.\.venv\Scripts\python.exe tests\test_gui.py --portable
.\.venv\Scripts\python.exe tests\test_gui.py --portable --single
```

集成测试会启动真实的本机 FTP/SFTP 服务，测试中文目录、读写传输、只读拒绝、认证失败、路径边界、互斥、进程退出和端口释放。SMB 测试使用模拟接口，避免创建系统共享；实际 SMB 手机联机仍需管理员环境验证。GUI 测试会短暂启动界面，不修改真实 SMB 共享。

## 实现

- `gui.py`：Tkinter 三标签页及日志、配置、IP、连接提示。
- `controller.py`：互斥、子进程状态、管道生命周期、SMB 恢复记录。
- `worker.py`：独立后台入口，pyftpdlib FTP 与 Paramiko SFTP。
- `sftp.py`：密码认证、共享目录约束与读写权限适配。
- `smb.py`：PowerShell 调用，按唯一描述标记及路径确认共享归属。

停止或关闭 GUI 时通过管道关闭 FTP/SFTP 后台进程；父进程异常终止也会触发后台退出。SMB 属于 Windows 原生共享，断电或强制终止后可能保留，由同目录恢复记录管理，恢复期间禁止启动其他协议。SMB 文件连接日志通过系统打开文件列表采样，短连接可能漏采。NTFS 原有权限不作修改。

Windows 未签名 EXE 可能触发 SmartScreen。已在当前 Win10 x64 环境完成构建与本地验证；用户已反馈 FTP、SFTP、SMB 三种协议正常工作。尚无另一台实体电脑或 Win11 上的专项验证记录。
## 备注
已经在另外一台电脑试验，可以正常使用，建议将exe放在一个文件内使用，因为会生成相关日志文件。
