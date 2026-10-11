# -*- coding: utf-8 -*-
"""
LanShare 自测脚本：真实启动两个节点（两个进程），验证
  浏览 / 上传(分片+续传) / 下载(Range 断点续传) / 设备添加 / 推送 / 拉取 /
  待确认接收 / 跳过相同文件 / 路径穿越防护 / 密钥鉴权 / 中文与特殊字符文件名 / 大文件
用法：python test_lanshare.py
"""
import hashlib
import base64
import http.client
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
APP = os.path.join(HERE, 'lanshare.py')
PASS, FAIL = [], []


def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print(('  [OK]   ' if cond else '  [FAIL] ') + name + (('  -> ' + str(extra)) if (extra and not cond) else ''))


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_up(port, timeout=25, key=''):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            c = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
            c.request('GET', '/api/ping', headers={'X-LanShare-Key': key})
            if c.getresponse().status == 200:
                c.close()
                return True
            c.close()
        except Exception:
            time.sleep(0.25)
    return False


class Node:
    def __init__(self, name, root, port, key='', auto_accept=True, extra=None, discovery=False):
        self.name, self.root, self.port, self.key = name, root, port, key
        self.cfg = os.path.join(root, 'cfg-%s.json' % name)
        # 默认用 --shared-only 把范围固定到测试目录（测试针对共享模式的边界，如穿越防护），
        # 避免去扫真实磁盘。extra 里带 --whole-disk 或 --no-fix 时不加，
        # 以便测全盘模式、以及测「旧配置自动升级」不受命令行干扰。
        args = [PY, APP, '--port', str(port), '--name', name, '--config', self.cfg]
        if not any(f in (extra or []) for f in ('--whole-disk', '--no-fix')):
            args += ['--root', root, '--shared-only']
        if not discovery:
            args.append('--no-discovery')
        # --no-fix 表示「不要替我做任何命令行兜底」，用于验证配置迁移本身
        clean = [a for a in (extra or []) if a != '--no-fix']
        if '--no-fix' not in (extra or []):
            args += (['--key', key] if key else ['--no-auth'])
        args += clean
        self.log = open(os.path.join(root, 'log-%s.txt' % name), 'w', encoding='utf-8')
        self.proc = subprocess.Popen(args, stdout=self.log, stderr=subprocess.STDOUT, cwd=HERE)

    def api(self, method, path, body=None, key=None, raw=None, headers=None, timeout=60):
        path = urllib.parse.quote(path, safe='/?&=%+')
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=timeout)
        h = {'X-LanShare-Key': key if key is not None else self.key}
        h.update(headers or {})
        payload = raw
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
            h['Content-Type'] = 'application/json; charset=utf-8'
            h['Content-Length'] = str(len(payload))
        elif raw is not None:
            h['Content-Length'] = str(len(payload))
        c.request(method, path, body=payload, headers=h)
        r = c.getresponse()
        data = r.read()
        out = None
        try:
            out = json.loads(data.decode('utf-8'))
        except Exception:
            out = data
        c.close()
        return r.status, out

    def get(self, path, **kw):
        return self.api('GET', path, **kw)

    def post(self, path, body=None, **kw):
        return self.api('POST', path, body=body or {}, **kw)

    def upload(self, rel, data, offset=0, key=None):
        q = '/api/fs/upload?path=' + urllib.parse.quote(rel) + '&offset=%d' % offset
        return self.api('PUT', q, raw=data, key=key,
                        headers={'Content-Type': 'application/octet-stream'})

    def download(self, rel, rng=None, key=None):
        q = '/api/fs/download?path=' + urllib.parse.quote(rel)
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=120)
        h = {'X-LanShare-Key': key if key is not None else self.key}
        if rng:
            h['Range'] = rng
        c.request('GET', q, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data, dict(r.getheaders())

    def stop(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=8)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        try:
            self.log.close()
        except Exception:
            pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_bytes(path):
    with open(path, 'rb') as f:
        return f.read()


def wait_task(node, tid, timeout=180, want=('done', 'error', 'canceled')):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        st, r = node.get('/api/tasks')
        for t in (r.get('tasks') or []):
            if t.get('id') == tid:
                last = t
                if t.get('status') in want:
                    return t
        time.sleep(0.3)
    return last


def main():
    tmp = tempfile.mkdtemp(prefix='lanshare-test-')
    print('测试目录：%s\n' % tmp)
    A_root = os.path.join(tmp, 'A')
    B_root = os.path.join(tmp, 'B')
    os.makedirs(A_root)
    os.makedirs(B_root)

    # ---- A 端准备测试数据 ----
    os.makedirs(os.path.join(A_root, 'docs', '深层'))
    big = os.urandom(5 * 1024 * 1024 + 12345)          # 5MB+ 随机文件
    with open(os.path.join(A_root, 'docs', 'big.bin'), 'wb') as f:
        f.write(big)
    with open(os.path.join(A_root, 'docs', '中文名称 测试.txt'), 'w', encoding='utf-8') as f:
        f.write('你好，局域网！hello lan-share\n' * 10)
    with open(os.path.join(A_root, 'docs', '深层', 'deep.txt'), 'w', encoding='utf-8') as f:
        f.write('deep file\n')
    with open(os.path.join(A_root, 'empty.txt'), 'wb') as f:
        f.write(b'')
    with open(os.path.join(A_root, '.hidden'), 'w', encoding='utf-8') as f:
        f.write('hidden\n')

    key = os.urandom(8).hex()
    pa, pb = free_port(), free_port()
    A = Node('测试A', A_root, pa, key=key)
    B = Node('测试B', B_root, pb, key=key)
    try:
        check('节点A 启动', wait_up(pa, key=key))
        check('节点B 启动', wait_up(pb, key=key))
        if FAIL:
            return report(tmp)

        # ---- 1. 鉴权 ----
        st, r = A.get('/api/fs/list?path=', key='')
        check('无密钥访问被拒绝(401)', st == 401 and r.get('auth_required') is True, (st, r))
        st, r = A.get('/api/fs/list?path=', key='wrong-key')
        check('错误密钥被拒绝(401)', st == 401, (st, r))
        st, r = A.get('/api/status', key='')
        check('状态探测无需密钥(公开)', st == 200 and r.get('auth_required') is True, (st, r))
        st, r = A.get('/api/ping', key='')
        check('ping 受密钥保护', st == 401, (st, r))
        st, r = A.get('/api/ping')
        check('带密钥 ping 返回节点信息', st == 200 and r.get('id') and r.get('authed') is True, (st, r))

        # ---- 1b. 非 ASCII 密钥（真实故障回归）----
        # 浏览器无法把中文放进 HTTP 头，前端会 base64 编码并加 'b64:' 前缀；
        # 另外 secrets.compare_digest 对含非 ASCII 的 str 会抛 TypeError，
        # 曾经导致连接被直接关闭（表现为「网络仍不可达 / other side closed」）。
        st, r = A.post('/api/config', {'key': '中文密码🔒'})
        check('可以设置含中文/emoji 的密钥', st == 200 and r['config']['key'] == '中文密码🔒', (st, r))
        b64 = lambda s: 'b64:' + base64.b64encode(s.encode('utf-8')).decode('ascii')
        st, r = A.get('/api/fs/list?path=', key=b64('中文密码🔒'))
        check('用 base64 编码的中文密钥可访问', st == 200 and r.get('ok'), (st, r))
        st, r = A.get('/api/fs/list?path=', key=b64('中文密码错误'))
        check('中文密钥不匹配返回 401（不崩溃）', st == 401, (st, r))
        st, r = A.get('/api/fs/list?path=', key='')
        check('中文密钥模式下无密钥被拒', st == 401, (st, r))
        # 显式不带请求头，才能真正走到 query 参数的鉴权分支（头优先于 query）
        st, r = A.get('/api/fs/list?path=&key=' + urllib.parse.quote('中文密码🔒'), key='')
        check('query 里带中文密钥也可访问', st == 200 and r.get('ok'), (st, r))
        st, r = A.post('/api/config', {'key': key}, key=b64('中文密码🔒'))
        check('可以把密钥改回普通值', st == 200 and r['config']['key'] == key, (st, r))

        # ---- 2. 浏览 ----
        st, r = A.get('/api/fs/list?path=')
        names = {e['name'] for e in r.get('entries', [])}
        check('根目录浏览', st == 200 and 'docs' in names and 'empty.txt' in names, names)
        check('默认隐藏隐藏文件', '.hidden' not in names, names)
        st, r = A.get('/api/fs/list?path=&hidden=1')
        check('显示隐藏文件开关', '.hidden' in {e['name'] for e in r.get('entries', [])})
        st, r = A.get('/api/fs/list?path=docs')
        check('子目录浏览', st == 200 and '中文名称 测试.txt' in {e['name'] for e in r.get('entries', [])})
        st, r = A.get('/api/fs/list?path=docs/深层')
        check('中文深层目录', st == 200 and {e['name'] for e in r.get('entries', [])} == {'deep.txt'})

        # ---- 3. 路径穿越防护 ----
        for bad in ['../', '..%2f..', 'a/../../..', '....//....//', '/etc/passwd', 'C:/Windows/win.ini',
                    '..\\..\\secret']:
            st, r = A.get('/api/fs/list?path=' + urllib.parse.quote(bad))
            ok = st in (400, 404) or (st == 200 and not r.get('ok', True))
            if st == 200 and r.get('ok'):
                # 规范后落在共享目录内且确实存在，才算安全（如 'a/../../..' -> 根）
                inner = r.get('path', '')
                ok = '..' not in inner
            check('拒绝路径穿越: %r' % bad, ok, (st, r))
        st, r, _h = A.download('../lanshare.config.json')
        check('下载穿越被拒', st == 404, st)

        # ---- 4. 上传 + 断点续传 ----
        data = os.urandom(3 * 1024 * 1024 + 777)
        part = data[:1024 * 1024]
        st, r = B.upload('up.bin', part, offset=0)
        check('分片上传第一段', st == 200 and r.get('written') == len(part), (st, r))
        st, r = B.api('GET', '/api/fs/stat?path=up.bin')
        check('续传前大小正确', r.get('size') == len(part), r)
        st, r = B.upload('up.bin', data[len(part):], offset=len(part))
        check('断点续传后半段', st == 200 and r.get('written') == len(data), (st, r))
        got = read_bytes(os.path.join(B_root, 'up.bin'))
        check('续传后内容一致', sha(got) == sha(data), '%d vs %d' % (len(got), len(data)))
        st, r = B.upload('sub/dir/嵌套.bin', b'nested', offset=0)
        check('上传自动建目录+中文名', st == 200 and read_bytes(os.path.join(B_root, 'sub', 'dir', '嵌套.bin')) == b'nested')
        st, r = B.upload('', b'x', offset=0)
        check('空路径上传被拒', st == 400, st)

        # ---- 5. 下载 + Range ----
        st, body, hdr = A.download('docs/big.bin')
        check('完整下载一致', st == 200 and sha(body) == sha(big))
        check('下载响应带 Accept-Ranges', hdr.get('Accept-Ranges') == 'bytes', hdr)
        st, body, hdr = A.download('docs/big.bin', rng='bytes=1000-1999')
        check('Range 断点下载', st == 206 and body == big[1000:2000] and
              hdr.get('Content-Range') == 'bytes 1000-1999/%d' % len(big), (st, hdr.get('Content-Range')))
        st, body, hdr = A.download('docs/big.bin', rng='bytes=%d-' % (len(big) - 10))
        check('Range 尾部下载', st == 206 and body == big[-10:], st)
        st, body, hdr = A.download('docs/big.bin', rng='bytes=%d-' % (len(big) + 5))
        check('Range 越界 416', st == 416, st)
        st, body, hdr = A.download('docs/中文名称 测试.txt')
        check('中文名下载', st == 200 and '局域网' in body.decode('utf-8'))
        check('Content-Disposition 编码正确', "filename*=UTF-8''" in (hdr.get('Content-Disposition') or ''),
              hdr.get('Content-Disposition'))

        # ---- 6. 目录操作 ----
        st, r = B.post('/api/fs/mkdir', {'path': '新建目录'})
        check('新建文件夹', st == 200 and os.path.isdir(os.path.join(B_root, '新建目录')))
        st, r = B.post('/api/fs/rename', {'path': 'up.bin', 'name': 'renamed.bin'})
        check('重命名', st == 200 and os.path.isfile(os.path.join(B_root, 'renamed.bin')))
        st, r = B.post('/api/fs/rename', {'path': 'renamed.bin', 'name': 'sub'})
        check('重命名到已存在名称被拒', st == 400, (st, r))
        st, r = B.post('/api/fs/delete', {'path': '新建目录'})
        check('删除目录', st == 200 and not os.path.exists(os.path.join(B_root, '新建目录')))
        st, r = B.post('/api/fs/delete', {'path': ''})
        check('禁止删除共享根', st == 400, st)

        # ---- 7. 设备发现（手动添加） ----
        st, r = A.post('/api/devices', {'host': '127.0.0.1', 'port': pb})
        check('手动添加设备成功', st == 200 and r['device']['name'] == '测试B', (st, r))
        b_id = r['device']['id'] if st == 200 else None
        st, r = A.post('/api/devices', {'host': '127.0.0.1', 'port': pb, 'key': 'bad'})
        check('添加设备时密钥错误被提示', st == 401, (st, r))
        st, r = A.get('/api/devices')
        check('设备列表含B且在线', any(d['id'] == b_id and d['online'] for d in r['devices']), r)
        if b_id:
            st, r = A.get('/api/remote/list?id=%s&path=' % urllib.parse.quote(b_id))
            check('浏览远程设备目录', st == 200 and 'renamed.bin' in {e['name'] for e in r.get('entries', [])}, (st, r))

        # ---- 8. 推送：单文件 ----
        st, r = A.post('/api/send', {'path': 'empty.txt', 'targets': [b_id], 'dest': '收件箱'})
        check('创建推送任务', st == 200 and r['tasks'][0].get('task'), (st, r))
        t = wait_task(A, r['tasks'][0]['task'])
        check('空文件推送完成', t and t['status'] == 'done', t)
        check('空文件到达B', os.path.isfile(os.path.join(B_root, '收件箱', 'empty.txt')))

        # ---- 9. 推送：大文件 + 中文名 ----
        st, r = A.post('/api/send', {'path': 'docs', 'targets': [b_id], 'dest': '收件箱'})
        tid = r['tasks'][0]['task']
        t = wait_task(A, tid, timeout=240)
        check('目录推送完成', t and t['status'] == 'done', t)
        check('目录结构正确', os.path.isfile(os.path.join(B_root, '收件箱', 'docs', '深层', 'deep.txt')),
              os.listdir(os.path.join(B_root, '收件箱')) if os.path.isdir(os.path.join(B_root, '收件箱')) else '无收件箱')
        check('大文件内容一致(B端)', sha(read_bytes(os.path.join(B_root, '收件箱', 'docs', 'big.bin'))) == sha(big))
        check('中文文件名保持一致',
              os.path.isfile(os.path.join(B_root, '收件箱', 'docs', '中文名称 测试.txt')))
        st, r = A.get('/api/devices')
        # 重复推送应全部跳过
        st, r = A.post('/api/send', {'path': 'docs', 'targets': [b_id], 'dest': '收件箱'})
        t2 = wait_task(A, r['tasks'][0]['task'])
        check('重复推送全部跳过', t2 and t2['status'] == 'done' and t2.get('skipped') == 3,
              (t2 or {}).get('skipped'))
        # 任务必须记录 dest_base / src_device，否则网页上的「重试」会传错目录
        check('发送任务记录 dest_base', t2 and t2.get('dest_base') == '收件箱', (t2 or {}).get('dest_base'))
        check('发送任务记录 src_device', t2 and t2.get('src_device') == '', (t2 or {}).get('src_device'))
        # 模拟网页「重试」：用任务里的字段重新发起，应落到同一目录且全部跳过
        st, r = A.post('/api/send', {'path': t2['path'], 'targets': [t2['remote_id']],
                                     'dest': t2.get('dest_base', ''), 'overwrite': t2.get('overwrite', 'skip'),
                                     'src_device': t2.get('src_device', '')})
        t3 = wait_task(A, r['tasks'][0]['task'])
        check('按任务字段重试成功且不重复传输',
              t3 and t3['status'] == 'done' and t3.get('skipped') == 3, (t3 or {}).get('skipped'))
        check('重试没有产生嵌套目录',
              os.path.isfile(os.path.join(B_root, '收件箱', 'docs', 'big.bin')) and
              not os.path.isdir(os.path.join(B_root, '收件箱', '收件箱')),
              os.listdir(os.path.join(B_root, '收件箱')) if os.path.isdir(os.path.join(B_root, '收件箱')) else '无')

        # ---- 10. 推送中断后续传 ----
        with open(os.path.join(A_root, 'resume.bin'), 'wb') as f:
            f.write(os.urandom(2 * 1024 * 1024))
        # 人为在B端放一个半截文件，模拟中断
        os.makedirs(os.path.join(B_root, '续传'), exist_ok=True)
        half = read_bytes(os.path.join(A_root, 'resume.bin'))[:900 * 1024]
        with open(os.path.join(B_root, '续传', 'resume.bin'), 'wb') as f:
            f.write(half)
        st, r = A.post('/api/send', {'path': 'resume.bin', 'targets': [b_id], 'dest': '续传'})
        t = wait_task(A, r['tasks'][0]['task'])
        final = read_bytes(os.path.join(B_root, '续传', 'resume.bin'))
        check('断点续传后续内容一致', t and t['status'] == 'done' and
              sha(final) == sha(read_bytes(os.path.join(A_root, 'resume.bin'))),
              (t or {}).get('status'))

        # ---- 11. 拉取 ----
        with open(os.path.join(B_root, 'fromB.txt'), 'w', encoding='utf-8') as f:
            f.write('这是B上的文件\n')
        st, r = A.post('/api/pull', {'device_id': b_id, 'paths': ['fromB.txt'], 'dest': '从B拉取'})
        check('创建拉取任务', st == 200, (st, r))
        t = wait_task(A, r['task'])
        check('拉取完成', t and t['status'] == 'done', t)
        check('拉取内容正确', os.path.isfile(os.path.join(A_root, '从B拉取', 'fromB.txt')))
        st, r = A.post('/api/pull', {'device_id': b_id, 'paths': ['收件箱/docs'], 'dest': '从B拉来'})
        t = wait_task(A, r['task'], timeout=240)
        check('拉取整个目录完成', t and t['status'] == 'done', t)
        dest_dir = os.path.join(A_root, '从B拉来', 'docs')   # 远端目录名保持不变
        check('拉取目录内容一致',
              os.path.isfile(os.path.join(dest_dir, '深层', 'deep.txt')) and
              sha(read_bytes(os.path.join(dest_dir, 'big.bin'))) == sha(big),
              os.listdir(os.path.join(A_root, '从B拉来')) if os.path.isdir(os.path.join(A_root, '从B拉来')) else '未创建')
        check('拉取中文文件名正确', os.path.isfile(os.path.join(dest_dir, '中文名称 测试.txt')))
        st, r = A.post('/api/pull', {'device_id': b_id, 'paths': ['不存在的路径'], 'dest': ''})
        t = wait_task(A, r['task'])
        check('拉取不存在路径报错', t and t['status'] == 'error', (t or {}).get('status'))

        # ---- 12. 任务管理 ----
        st, r = A.get('/api/tasks')
        check('任务列表返回', st == 200 and len(r['tasks']) >= 4, len(r.get('tasks') or []))
        done_id = [t for t in r['tasks'] if t['status'] == 'done'][0]['id']
        st, r = A.post('/api/tasks', {'action': 'delete', 'id': done_id})
        check('删除任务记录', st == 200)
        st, r = A.post('/api/tasks', {'action': 'delete', 'id': 'nonexistent'})
        check('删除不存在的任务返回404', st == 404, st)

        # ---- 13. 配置 ----
        st, r = A.get('/api/info')
        check('info 含密钥与地址', st == 200 and r['key'] == key and r['urls'], (st, r.get('name')))
        st, r = A.post('/api/config', {'name': '改名节点', 'incoming_dir': '新接收目录'})
        check('修改配置', st == 200 and r['config']['name'] == '改名节点', (st, r))
        st, r = A.get('/api/info')
        check('配置已生效', r['name'] == '改名节点' and r['incoming_dir'] == '新接收目录', r.get('name'))

        # ---- 14. 大文件（50MB）速度与完整性 ----
        big50 = os.urandom(50 * 1024 * 1024)
        with open(os.path.join(A_root, 'big50.bin'), 'wb') as f:
            f.write(big50)
        t0 = time.time()
        st, r = A.post('/api/send', {'path': 'big50.bin', 'targets': [b_id], 'dest': '大文件'})
        t = wait_task(A, r['tasks'][0]['task'], timeout=300)
        dt = time.time() - t0
        okbig = os.path.isfile(os.path.join(B_root, '大文件', 'big50.bin')) and \
            sha(read_bytes(os.path.join(B_root, '大文件', 'big50.bin'))) == sha(big50)
        check('50MB 文件传输完整', t and t['status'] == 'done' and okbig,
              '%s %.1fs' % ((t or {}).get('status'), dt))
        print('         50MB 耗时 %.1fs（%.1f MB/s，含哈希校验）' % (dt, 50 / max(dt, 0.01)))

        # ---- 15. 网页与静态资源 ----
        c = http.client.HTTPConnection('127.0.0.1', pa, timeout=10)
        c.request('GET', '/')
        rr = c.getresponse()
        page = rr.read().decode('utf-8', 'replace')
        check('首页可访问(免密钥)', rr.status == 200 and 'LanShare' in page, rr.status)
        c.close()
        c = http.client.HTTPConnection('127.0.0.1', pa, timeout=10)
        c.request('GET', '/?key=' + key)
        rr = c.getresponse()
        cookie = rr.getheader('Set-Cookie') or ''
        rr.read()
        check('带密钥打开会下发 Cookie', 'ls_key=' in cookie, cookie)
        c.close()
        c = http.client.HTTPConnection('127.0.0.1', pa, timeout=10)
        c.request('GET', '/favicon.ico')
        rr = c.getresponse()
        check('favicon 可用', rr.status == 200 and 'svg' in (rr.getheader('Content-Type') or ''))
        c.close()

        # ---- 16. 待确认接收（对方未开启自动接收） ----
        B.stop()
        Bc = Node('测试B确认', B_root, pb, key=key, extra=['--confirm'])
        try:
            check('需确认模式节点启动', wait_up(pb, key=key))
            st, r = A.post('/api/devices', {'host': '127.0.0.1', 'port': pb})
            check('重新添加设备', st == 200, (st, r))
            b2 = r['device']['id']
            with open(os.path.join(A_root, '需确认.txt'), 'w', encoding='utf-8') as f:
                f.write('等待对方确认\n')
            st, r = A.post('/api/send', {'path': '需确认.txt', 'targets': [b2], 'dest': '确认收到'})
            tid = r['tasks'][0]['task']
            t = wait_task(A, tid, timeout=30)
            check('未开启自动接收时挂起等待确认', t and t['status'] == 'done' and '等待对方' in (t.get('note') or ''),
                  (t or {}).get('note'))
            st, r = Bc.get('/api/tasks')
            pend = [x for x in r['tasks'] if x['status'] == 'pending']
            check('接收端出现待确认记录', st == 200 and len(pend) == 1, r.get('tasks'))
            check('待确认记录含文件名', pend and pend[0].get('files') and
                  pend[0]['files'][0]['rel'] == '需确认.txt', pend[0].get('files') if pend else None)
            if pend:
                st, r = Bc.post('/api/tasks', {'action': 'accept', 'id': pend[0]['id']})
                check('确认接收调用成功', st == 200, (st, r))
                t = wait_task(Bc, pend[0]['id'], timeout=60)
                check('确认后文件成功拉取', t and t['status'] == 'done', t)
                check('确认后文件落盘（按对方原路径）',
                      os.path.isfile(os.path.join(B_root, '确认收到', '需确认.txt')),
                      os.listdir(os.path.join(B_root, '确认收到')) if os.path.isdir(os.path.join(B_root, '确认收到')) else '无目录')
                check('确认后没有多余嵌套目录',
                      not os.path.isdir(os.path.join(B_root, '确认收到', 'LanShare')),
                      os.listdir(os.path.join(B_root, '确认收到')))
        finally:
            Bc.stop()
        B = Node('测试B2', B_root, pb, key=key)
        check('节点B 恢复自动接收', wait_up(pb, key=key))

        # ---- 17. 拒绝接收 ----
        st, r = A.post('/api/devices', {'host': '127.0.0.1', 'port': pb})
        b3 = r['device']['id']
        B.stop()
        Bd = Node('测试B拒绝', B_root, pb, key=key, extra=['--confirm'])
        try:
            check('拒绝模式节点启动', wait_up(pb, key=key))
            st, r = A.post('/api/devices', {'host': '127.0.0.1', 'port': pb})
            b4 = r['device']['id']
            st, r = A.post('/api/send', {'path': '需确认.txt', 'targets': [b4], 'dest': '不该出现'})
            wait_task(A, r['tasks'][0]['task'], timeout=30)
            st, r = Bd.get('/api/tasks')
            pend = [x for x in r['tasks'] if x['status'] == 'pending']
            if pend:
                Bd.post('/api/tasks', {'action': 'reject', 'id': pend[0]['id']})
                time.sleep(0.5)
            check('拒绝后文件未落盘', not os.path.exists(os.path.join(B_root, '不该出现')))
        finally:
            Bd.stop()
        B = Node('测试B3', B_root, pb, key=key)
        check('节点B 再次恢复', wait_up(pb, key=key))

        # ---- 18. UDP 广播自动发现 ----
        p1, p2 = free_port(), free_port()
        D_root = os.path.join(tmp, 'D')
        E_root = os.path.join(tmp, 'E')
        os.makedirs(D_root, exist_ok=True)
        os.makedirs(E_root, exist_ok=True)
        D = Node('发现D', D_root, p1, key=key, discovery=True)
        E = Node('发现E', E_root, p2, key=key, discovery=True)
        try:
            check('发现节点D启动', wait_up(p1, key=key))
            check('发现节点E启动', wait_up(p2, key=key))
            found = None
            t0 = time.time()
            while time.time() - t0 < 15:
                for node, other in ((D, '发现E'), (E, '发现D')):
                    st, r = node.get('/api/devices')
                    for d in (r.get('devices') or []):
                        if d.get('name') == other:
                            found = (node.name, other, d)
                if found:
                    break
                time.sleep(0.5)
            check('UDP 广播互相发现设备', bool(found), '15 秒内未发现（可能被防火墙拦截）')
            if found:
                host_ok = found[2].get('host') in ('127.0.0.1',) or found[2].get('host', '').startswith('192.168')
                check('发现结果含可用 IP', host_ok, found[2].get('host'))
                lo = D if found[0] == '发现D' else E
                st, r = lo.get('/api/remote/list?id=%s&path=' % urllib.parse.quote(found[2]['id']))
                check('可直接浏览自动发现的设备', st == 200, (st, r))
        finally:
            D.stop()
            E.stop()
        # ---- 19. 全盘模式（新默认）----
        p3 = free_port()
        W_root = os.path.join(tmp, 'W')
        os.makedirs(W_root, exist_ok=True)
        Wh = Node('全盘节点', W_root, p3, key='', extra=['--whole-disk'])
        try:
            check('全盘节点启动（免密钥）', wait_up(p3, key=''))
            st, r = Wh.get('/api/info')
            check('全盘模式已开启', st == 200 and r.get('allow_whole_disk') is True, r.get('allow_whole_disk'))
            check('无需密钥即可访问', r.get('auth_required') is False, r.get('auth_required'))
            check('返回磁盘列表', bool(r.get('drives')), r.get('drives'))
            st, r = Wh.get('/api/fs/list?path=')
            drives = [e for e in r.get('entries', []) if e.get('drive')]
            check('根目录列出的是磁盘分区', st == 200 and len(drives) >= 1,
                  [e.get('name') for e in r.get('entries', [])])
            check('磁盘条目带容量信息', bool(drives) and drives[0].get('total'),
                  drives[0] if drives else None)
            # 进入系统盘，随便看一个真实目录
            sysdrive = (os.environ.get('SystemDrive') or 'C:')
            st, r = Wh.get('/api/fs/list?path=%s/' % sysdrive)
            check('可以浏览系统盘根目录', st == 200 and len(r.get('entries', [])) > 0, st)
            # 读取一个真实存在的系统文件
            probe = None
            for cand in ('%s/Windows/win.ini' % sysdrive, '%s/Windows/System32/drivers/etc/hosts' % sysdrive):
                full = cand.replace('/', os.sep)
                if os.path.isfile(full):
                    probe = cand
                    break
            if probe:
                st, body, hdr = Wh.download(probe)
                real = read_bytes(probe.replace('/', os.sep))
                check('可下载本机任意位置的真实文件', st == 200 and body == real,
                      '%s/%s' % (st, len(body) if body else 0))
            else:
                check('可下载本机任意位置的真实文件', True, '（本机无探测文件，跳过）')
            # 穿越防护在全盘模式下依然有效
            st, r = Wh.get('/api/fs/list?path=%s/../Windows' % sysdrive)
            check('全盘模式下仍拦截 ../ 穿越',
                  st in (400, 404) or not r.get('ok'), (st, r))
            st, r = Wh.get('/api/fs/list?path=/etc')
            check('全盘模式下拒绝绝对路径写法',
                  st in (400, 404) or not r.get('ok'), (st, r))
            # 不允许把整个「此电脑」当源发送
            st, r = Wh.post('/api/send', {'path': '', 'targets': ['whatever']})
            check('拒绝把「此电脑」根当发送源', st == 400 and '此电脑' in str(r.get('error', '')), (st, r))
            # 默认接收目录必须带盘符（否则无从定位）
            st, r = Wh.get('/api/ping')
            di = r.get('default_incoming') or ''
            check('默认接收目录带盘符', ':' in di, di)
        finally:
            Wh.stop()

        # ---- 20. 旧配置自动升级（免密钥 + 全盘）----
        p4 = free_port()
        V_root = os.path.join(tmp, 'V')
        os.makedirs(V_root, exist_ok=True)
        old_cfg = os.path.join(V_root, 'old.json')
        with open(old_cfg, 'w', encoding='utf-8') as f:
            json.dump({'name': '旧配置节点', 'port': p4, 'root': V_root,
                       'key': 'oldkey123', 'allow_whole_disk': False,
                       'discovery': False, 'auto_accept': True,
                       'incoming_dir': 'LanShare 接收'}, f, ensure_ascii=False)
        Vo = Node('旧配置节点', V_root, p4, key='', extra=['--config', old_cfg, '--no-fix'])
        try:
            check('旧配置节点启动', wait_up(p4, key=''))
            st, r = Vo.get('/api/info')
            check('旧配置自动升级为免密钥', r.get('auth_required') is False, r.get('auth_required'))
            check('旧配置自动升级为全盘', r.get('allow_whole_disk') is True, r.get('allow_whole_disk'))
            with open(old_cfg, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            check('升级结果已写回配置文件', saved.get('config_version') == 2 and saved.get('key') == '',
                  saved)
        finally:
            Vo.stop()
    finally:
        try:
            A.stop()
        except Exception:
            pass
        try:
            B.stop()
        except Exception:
            pass

    return report(tmp)


def report(tmp):
    print('\n' + '=' * 60)
    print('通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    if FAIL:
        print('失败项：')
        for f in FAIL:
            print('  - ' + f)
    print('=' * 60)
    print('测试临时目录：%s（可手动删除）' % tmp)
    return 1 if FAIL else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
