import sys


def main():
    if '--worker' in sys.argv:
        # PyInstaller windowed applications have no default standard streams.
        if sys.stdin is None or sys.stdout is None:
            import ctypes
            import msvcrt
            import os
            kernel = ctypes.windll.kernel32
            kernel.GetStdHandle.restype = ctypes.c_void_p
            kernel.GetStdHandle.argtypes = [ctypes.c_uint]
            input_fd = msvcrt.open_osfhandle(kernel.GetStdHandle(-10 & 0xffffffff), os.O_RDONLY)
            output_fd = msvcrt.open_osfhandle(kernel.GetStdHandle(-11 & 0xffffffff), os.O_WRONLY)
            sys.stdin = os.fdopen(input_fd, 'r', encoding='utf-8')
            sys.stdout = os.fdopen(output_fd, 'w', encoding='utf-8', buffering=1)
            sys.stderr = sys.stdout
        from lanshare.worker import main as worker_main
        worker_main()
    else:
        from lanshare.gui import main as gui_main
        gui_main()


if __name__ == '__main__':
    main()
