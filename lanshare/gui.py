import ctypes
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .core import app_dir, load_config, DEFAULTS, atomic_json, ipv4_addresses
from .controller import Controller
from .smb import is_admin


class App:
    def __init__(self, root):
        self.root = root
        self.directory = app_dir()
        self.path = self.directory / 'config.json'
        self.events = queue.Queue()
        self.save_job = None
        self.closing = False
        self.failed = False
        self.ips = []
        self.running_cfg = None
        self.running_protocol = None
        self.ip_busy = False
        self.config_error = ''
        try:
            self.config = load_config(self.path)
        except Exception as exc:
            self.config = json.loads(json.dumps(DEFAULTS))
            self.config_error = f'配置读取失败：{exc}。原文件保留，修改配置后会重新保存。'
        self.vars = {}
        self.panels = {}
        self.start_buttons = {}
        self.stop_buttons = {}
        self.fields = {}
        self.build()
        try:
            self.controller = Controller(self.directory, lambda k, t: self.events.put((k, t)))
        except Exception as exc:
            messagebox.showerror('共享恢复记录损坏', f'为避免遗留共享与其他协议同时运行，程序停止启动。\n请检查 {self.directory / ".smb-session.json"}\n{exc}')
            root.destroy()
            return
        for section in self.vars.values():
            for var in section.values():
                var.trace_add('write', self.changed)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.log('程序已就绪。手机和电脑必须连接同一局域网 WiFi。')
        self.log('配置中的密码按要求明文保存。FTP 被动数据端口：50000–50100。')
        if self.config_error:
            self.log(self.config_error)
            messagebox.showwarning('配置提示', self.config_error)
        self.refresh_ips()
        self.poll()
        self.update_controls()

    def build(self):
        root = self.root
        root.title('局域网文件共享工具')
        root.geometry('920x850')
        root.minsize(780, 760)
        root.configure(bg='#f2f5fa')
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('TFrame', background='#f2f5fa')
        style.configure('TLabel', background='#f2f5fa', foreground='#27334a')
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 21, 'bold'), foreground='#14223b')
        style.configure('Sub.TLabel', foreground='#5d6b83')
        style.configure('TButton', padding=(14, 7))
        style.configure('Primary.TButton', background='#2365ce', foreground='white')
        style.map('Primary.TButton', background=[('active', '#154da7'), ('disabled', '#a0acbd')])
        style.configure('TNotebook', background='#f2f5fa', borderwidth=0)
        style.configure('TNotebook.Tab', padding=(30, 10), font=('Microsoft YaHei UI', 11, 'bold'))
        style.map('TNotebook.Tab', background=[('selected', '#ffffff')], foreground=[('selected', '#2365ce')])
        outer = ttk.Frame(root, padding=24)
        outer.pack(fill='both', expand=True)
        header = ttk.Frame(outer)
        header.pack(fill='x')
        ttk.Label(header, text='局域网文件共享', style='Title.TLabel').pack(side='left')
        ttk.Label(header, text='管理员模式' if is_admin() else '普通模式 · SMB 需管理员', style='Sub.TLabel').pack(side='right')
        ttk.Label(outer, text='选择一个协议，让手机访问电脑中的文件。每次仅运行一种服务。', style='Sub.TLabel').pack(anchor='w', pady=(4, 16))
        notebook = ttk.Notebook(outer)
        notebook.pack(fill='x')
        for protocol in ('ftp', 'sftp', 'smb'):
            panel = ttk.Frame(notebook, padding=(18, 14))
            notebook.add(panel, text=protocol.upper())
            panel.columnconfigure(1, weight=1)
            self.panels[protocol] = panel
            cfg = self.config[protocol]
            vs = {key: tk.BooleanVar(value=value) if key == 'write' else tk.StringVar(value=value) for key, value in cfg.items()}
            self.vars[protocol] = vs
            self.fields[protocol] = []
            def entry(row, label, key, show=None):
                ttk.Label(panel, text=label).grid(row=row, column=0, sticky='w', padx=(0, 15), pady=5)
                widget = ttk.Entry(panel, textvariable=vs[key], show=show or '')
                widget.grid(row=row, column=1, sticky='ew', pady=5)
                self.fields[protocol].append(widget)
                return widget
            entry(0, '共享文件夹', 'folder')
            choose = ttk.Button(panel, text='选择文件夹', command=lambda p=protocol: self.choose(p))
            choose.grid(row=0, column=2, padx=(10, 0))
            self.fields[protocol].append(choose)
            if protocol != 'smb':
                entry(1, '监听端口', 'port')
                entry(2, '用户名', 'username')
                entry(3, '密码', 'password', show='●')
                if protocol == 'sftp':
                    ttk.Label(panel, text='认证方式：密码认证', style='Sub.TLabel').grid(row=1, column=2, padx=(10, 0), sticky='w')
                else:
                    ttk.Label(panel, text='被动模式：50000–50100', style='Sub.TLabel').grid(row=1, column=2, padx=(10, 0), sticky='w')
            else:
                entry(1, '共享名称', 'share')
                entry(2, 'Windows 本机账号', 'username')
                ttk.Label(panel, text='填写本机用户名或 电脑名\\用户名，不能自定义虚拟账号。', style='Sub.TLabel').grid(row=3, column=1, columnspan=2, sticky='w', pady=7)
            ttk.Label(panel, text='访问权限').grid(row=4, column=0, sticky='w', pady=8)
            rights = ttk.Frame(panel)
            rights.grid(row=4, column=1, sticky='w')
            for label, value in [('只读', False), ('读写', True)]:
                radio = ttk.Radiobutton(rights, text=label, variable=vs['write'], value=value)
                radio.pack(side='left', padx=(0, 22))
                self.fields[protocol].append(radio)
            actions = ttk.Frame(panel)
            actions.grid(row=5, column=1, columnspan=2, sticky='w', pady=(9, 0))
            self.start_buttons[protocol] = ttk.Button(actions, text='创建SMB共享' if protocol == 'smb' else f'启动{protocol.upper()}服务', style='Primary.TButton', command=lambda p=protocol: self.start(p))
            self.start_buttons[protocol].pack(side='left', padx=(0, 10))
            self.stop_buttons[protocol] = ttk.Button(actions, text='移除SMB共享' if protocol == 'smb' else f'停止{protocol.upper()}服务', command=lambda: self.stop())
            self.stop_buttons[protocol].pack(side='left')
        common = ttk.Frame(outer)
        common.pack(fill='x', pady=(16, 8))
        self.status = tk.StringVar(value='● 空闲')
        ttk.Label(common, textvariable=self.status, font=('Microsoft YaHei UI', 12, 'bold')).pack(side='left')
        ttk.Button(common, text='刷新本机 IP', command=self.refresh_ips).pack(side='right')
        self.ip_text = tk.StringVar(value='正在读取本机 IPv4…')
        ttk.Label(outer, textvariable=self.ip_text, wraplength=820, style='Sub.TLabel').pack(fill='x')
        self.connection = tk.StringVar(value='启动服务后，这里会显示手机连接地址和账号。')
        connection_row = ttk.Frame(outer)
        connection_row.pack(fill='x', pady=(8, 6))
        ttk.Label(connection_row, textvariable=self.connection, wraplength=690).pack(side='left', fill='x', expand=True)
        ttk.Button(connection_row, text='复制连接信息', command=self.copy_info).pack(side='right')
        ttk.Label(outer, text='FTP/SFTP：安卓 Solid Explorer、AndFTP；iPhone FileBrowser\nSMB：安卓 Solid Explorer；iPhone 自带“文件”APP。手机电脑必须处于同一局域网 WiFi。', style='Sub.TLabel').pack(anchor='w', pady=(2, 9))
        log_bar = ttk.Frame(outer)
        log_bar.pack(fill='x')
        ttk.Label(log_bar, text='运行日志', font=('Microsoft YaHei UI', 11, 'bold')).pack(side='left')
        ttk.Button(log_bar, text='清空日志', command=self.clear_log).pack(side='right')
        log_frame = ttk.Frame(outer)
        log_frame.pack(fill='both', expand=True, pady=(6, 0))
        self.logs = tk.Text(log_frame, height=9, state='disabled', wrap='word', bg='#152238', fg='#dce6f5', font=('Consolas', 10), relief='flat', padx=12, pady=10)
        scrollbar = ttk.Scrollbar(log_frame, command=self.logs.yview)
        self.logs.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.logs.pack(fill='both', expand=True)

    def collect(self):
        return {p: {k: v.get() for k, v in section.items()} for p, section in self.vars.items()}

    def changed(self, *_):
        if self.save_job:
            self.root.after_cancel(self.save_job)
        self.save_job = self.root.after(450, self.save)

    def save(self):
        self.save_job = None
        try:
            atomic_json(self.path, self.collect())
            return True
        except OSError as exc:
            self.log(f'配置保存失败：{exc}')
            messagebox.showerror('无法保存配置', f'请将软件放在可写入的目录（例如桌面）。\n{exc}')
            return False

    def choose(self, protocol):
        folder = filedialog.askdirectory(title='选择共享文件夹', mustexist=True)
        if folder:
            self.vars[protocol]['folder'].set(folder)

    def start(self, protocol):
        if self.controller.active or self.controller.busy:
            messagebox.showwarning('服务互斥', '已有服务正在运行，请先停止当前服务')
            return
        if not self.save():
            return
        cfg = self.collect()[protocol]
        if protocol == 'smb':
            if not is_admin():
                messagebox.showwarning('需要管理员权限', '必须使用管理员权限启动软件才能创建 SMB 共享。\n请关闭软件，右键 EXE 选择“以管理员身份运行”。')
                return
            messagebox.showinfo('SMB 连接准备', '请手动在 Windows 防火墙中允许专用网络上的“文件和打印机共享”（TCP 445）。\n手机使用本机 Windows 账号和该账号的登录密码，不是 PIN。\n共享权限同时受文件夹原有 NTFS 权限限制。')
        try:
            self.running_cfg, self.running_protocol = cfg, protocol
            self.controller.start(protocol, cfg)
            self.update_controls()
        except Exception as exc:
            self.status.set('● 启动失败')
            self.log(str(exc))
            messagebox.showerror('无法启动', str(exc))

    def stop(self):
        try:
            self.controller.stop()
            self.update_controls()
        except Exception as exc:
            messagebox.showwarning('无法停止', str(exc))

    def update_controls(self):
        active, busy = self.controller.active, self.controller.busy
        for p in self.vars:
            self.start_buttons[p].configure(state='disabled' if busy or active == p else 'normal')
            self.stop_buttons[p].configure(state='normal' if active == p and not busy else 'disabled')
            for field in self.fields[p]:
                field.configure(state='disabled' if active == p else 'normal')

    def refresh_ips(self):
        if self.ip_busy:
            return
        self.ip_busy = True
        def work():
            try:
                self.events.put(('ips', ipv4_addresses()))
            except Exception as exc:
                self.events.put(('ip_error', str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def update_connection(self):
        if not self.controller.active:
            self.connection.set('启动服务后，这里会显示手机连接地址和账号。')
            return
        protocol = self.controller.active
        cfg = self.running_cfg or self.collect()[protocol]
        addresses = [ip for _, ip in self.ips] or ['本机IP']
        if protocol == 'smb':
            name = self.controller.record['name'] if self.controller.record else cfg['share']
            text = '  |  '.join('\\\\' + ip + '\\' + name for ip in addresses)
            self.connection.set(text + '\n使用 Windows 本机账号及登录密码连接。')
        else:
            text = '  |  '.join(f'{protocol}://{ip}:{cfg["port"]}' for ip in addresses)
            self.connection.set(text + f'\n用户名：{cfg["username"]}　权限：' + ('读写' if cfg['write'] else '只读'))

    def copy_info(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.connection.get())

    def log(self, text):
        self.logs.configure(state='normal')
        self.logs.insert('end', time.strftime('[%H:%M:%S] ') + str(text) + '\n')
        if int(self.logs.index('end-1c').split('.')[0]) > 2000:
            self.logs.delete('1.0', '201.0')
        self.logs.see('end')
        self.logs.configure(state='disabled')

    def clear_log(self):
        self.logs.configure(state='normal')
        self.logs.delete('1.0', 'end')
        self.logs.configure(state='disabled')

    def poll(self):
        for _ in range(150):
            try:
                kind, text = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'ips':
                self.ip_busy = False
                self.ips = text
                self.ip_text.set('本机 IPv4：' + ('　|　'.join(f'{name}：{ip}' for name, ip in text) if text else '未发现可用地址，请连接局域网后刷新'))
                self.update_connection()
            elif kind == 'ip_error':
                self.ip_busy = False
                self.log('读取 IP 失败：' + text)
            elif kind == 'starting':
                self.status.set(f'● 正在启动 {text.upper()}…')
                self.log(f'正在启动 {text.upper()}')
            elif kind == 'ready':
                self.status.set(f'● 运行中 · {text.upper()}')
                self.log(f'{text.upper()} 启动成功')
                self.update_connection()
                if text == 'ftp':
                    self.log('手机连接失败时，请手动放行 FTP 控制端口及被动数据端口 50000–50100，或允许此程序通过专用网络防火墙。')
            elif kind in ('failed', 'stop_error'):
                self.status.set('● 启动失败' if kind == 'failed' else '● 停止失败 · 共享仍受管理')
                self.log(text)
                if self.closing:
                    self.closing = False
                self.update_connection()
                messagebox.showerror('服务错误', text)
            elif kind == 'recovery':
                self.status.set('● 运行中 · SMB 待恢复清理')
                self.log(text)
                self.update_connection()
                messagebox.showwarning('SMB 恢复', text)
            elif kind == 'stopping':
                self.status.set('● 正在停止…')
            elif kind == 'stopped':
                self.status.set('● 空闲')
                self.log('服务已停止；本地共享文件夹及文件保留。')
                self.running_cfg = None
                self.update_connection()
                if self.closing:
                    self.root.destroy()
                    return
            else:
                self.log(text)
            self.update_controls()
        self.root.after(100, self.poll)

    def close(self):
        if self.controller.busy:
            messagebox.showinfo('请稍候', '服务正在启动或停止，请完成后再关闭窗口。')
            return
        self.save()
        if self.controller.active:
            if self.controller.active == 'smb' and not is_admin():
                if messagebox.askyesno('需要管理员清理共享', '当前 SMB 共享需要管理员权限才能移除。\n是否退出并保留恢复记录，以便管理员重新打开本软件完成清理？\n退出后该 SMB 共享可能仍然存在。'):
                    self.root.destroy()
                return
            self.closing = True
            self.stop()
        else:
            self.root.destroy()


def main():
    if os.name != 'nt':
        raise SystemExit('仅支持 Windows 10/11')
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    mutex_api = ctypes.windll.kernel32
    mutex_api.CreateMutexW.restype = ctypes.c_void_p
    mutex_api.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    mutex = mutex_api.CreateMutexW(None, False, 'Local\\LANSharePortable.SingleInstance.v1')
    exists = mutex_api.GetLastError() == 183
    root = tk.Tk()
    if not mutex or exists:
        root.withdraw()
        messagebox.showinfo('程序已运行', '已有一个局域网文件共享工具窗口正在运行，请使用已有窗口。')
        root.destroy()
        return
    try:
        App(root)
        root.mainloop()
    finally:
        mutex_api.CloseHandle.argtypes = [ctypes.c_void_p]
        mutex_api.CloseHandle(mutex)
