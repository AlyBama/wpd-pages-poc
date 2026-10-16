"""إيقاف خادم v2 بدقة: عبر ملف الـPID ثم عبر مسح العمليات التي تعمل
من مجلد المشروع (cwd) — لا يمس المشروع القديم ولا أي برنامج آخر.

الاستخدام: python3 tools/stopserver.py PROJ PORT
يطبع سطورًا ثم سطرًا أخيرًا: PORT-FREE أو PORT-BUSY أو PORT-UNKNOWN
"""
import os
import signal
import socket
import sys
import time


def cmdline(pid):
    try:
        with open('/proc/%s/cmdline' % pid, 'rb') as f:
            return f.read().replace(b'\0', b' ').decode('utf-8', 'replace')
    except OSError:
        return ''


def cwd_of(pid):
    try:
        return os.path.realpath(os.readlink('/proc/%s/cwd' % pid))
    except OSError:
        return ''


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def is_ours(pid, proj_real):
    if pid == str(os.getpid()):
        return False
    cl = cmdline(pid)
    if 'server.py' not in cl:
        return False
    if 'stopserver' in cl:  # هذا الملف نفسه
        return False
    return cwd_of(pid) == proj_real


def stop_pid(pid, proj_real):
    if not is_ours(pid, proj_real):
        return False
    print('kill %s' % pid)
    try:
        os.kill(int(pid), signal.SIGTERM)
    except OSError:
        return not alive(pid)
    for _ in range(10):
        time.sleep(0.3)
        if not alive(pid):
            return True
    try:
        os.kill(int(pid), signal.SIGKILL)
    except OSError:
        pass
    time.sleep(1)
    return not alive(pid)


def port_free(port):
    # الاتصال أدق من bind: بقايا TIME_WAIT تكذب على bind
    try:
        s = socket.create_connection(('127.0.0.1', port), timeout=3)
        s.close()
        return False
    except OSError:
        return True


def main():
    proj = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8081
    proj_real = os.path.realpath(proj)
    stopped_any = False

    # 1) ملف الـPID الذي كتبه run.sh
    pf = os.path.join(proj, 'data', 'server.pid')
    try:
        with open(pf) as f:
            saved = f.read().strip()
    except OSError:
        saved = ''
    if saved.isdigit():
        if stop_pid(saved, proj_real):
            stopped_any = True
            print('stopped pid-file %s' % saved)
        try:
            if not alive(saved):
                os.remove(pf)
        except OSError:
            pass
    elif saved:
        try:
            os.remove(pf)
        except OSError:
            pass

    # 2) مسح كل العمليات: server.py + يعمل من مجلد مشروعنا فقط
    try:
        pids = [p for p in os.listdir('/proc') if p.isdigit()]
    except OSError:
        pids = []
    for pid in sorted(pids):
        if pid == str(os.getpid()):
            continue
        if is_ours(pid, proj_real):
            if stop_pid(pid, proj_real):
                stopped_any = True
                print('stopped %s' % pid)

    if stopped_any:
        print('STOPPED-SOME')
    else:
        print('STOPPED-NONE')
    print('PORT-FREE' if port_free(port) else 'PORT-BUSY')
    return 0


if __name__ == '__main__':
    sys.exit(main())
