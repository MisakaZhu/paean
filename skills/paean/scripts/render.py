# -*- coding: utf-8 -*-
"""批量给口播视频配 BGM；目录、曲库和导入目标由使用者配置。

  python render.py ./videos --list
  python render.py ./list.md 1- --config ./.paean-config.json
  python render.py ./videos 2=舒缓 --smr 11 --no-publish

日期/latest 写法需要 source_root；默认只输出本机工作区，不导入软件。
"""
import argparse
import datetime
import hashlib
import uuid
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse

import numpy as np

from paean_config import load_config

ROOT = None
DST = None
WORK = os.path.abspath('./paean-work')
MUSIC = os.path.abspath('./music')
TRACK_ALIASES = {}
PROBE = 'ffprobe'


AUDIO_EXT = ('.m4a', '.mp3', '.aac', '.wav', '.flac', '.ogg')
NAME_RE = re.compile(r'(\d+)\s*[-_.、．]\s*(.+?)\s*[（(]无音乐[）)]\.mp4', re.I)

# 选曲：标题（或话题）命中关键词就用对应曲目，按顺序第一个命中的算，都没命中用 似水
RULES = [  # (曲目, 标题关键词, 话题关键词)
    ('舒缓', r'婚恋|情感|恋爱|亲密|内耗|情绪|接纳|心理|自我|温柔|心气', r'情感共鸣|婚恋|亲密关系|心理健康'),
    ('五音', r'肝|肾|腰|坚持|长期', None),
    ('传说', r'误区|偏方|传说|古老|谣言|真相', None),
]
DEFAULT_TRACK = '似水'

FF = 'ffmpeg'
SR = 48000
TARGET_I = -16.0            # 成片整体响度 (LUFS)
SMR = 9.0                   # 人声比 BGM 响多少 (LU)，中老年观众人声要明显在上面
LEVEL_ALPHA = 0.5           # BGM 自身的大起伏压掉一半，避免中段突然盖过人声
FADE_IN, FADE_OUT = 1.0, 2.5
XFADE = 3.0                 # 曲子比视频短时首尾循环，接缝交叉淡化的秒数
LIMIT = 10 ** (-2.0 / 20)   # 限幅 -2 dBFS，AAC 编码后真峰值约 -1 dBTP 以内
TP_MAX = -1.0               # 真峰值上限 (dBTP)，超了就把限幅压低重做
VOICE_PRE = -20.0           # 压缩前先把人声对齐到 -20 LUFS
VOICE_FX = 'highpass=f=80,acompressor=threshold=0.178:ratio=2:attack=5:release=100:knee=2.83'
MUSIC_FX = 'aresample=48000:resampler=soxr,equalizer=f=2000:t=o:w=2:g=-3'  # 给人声让出 1~4kHz

# ITU-R BS.1770 K 计权 (48 kHz)
KB1, KA1 = [1.53512485958697, -2.69169618940638, 1.19839281085285], [1.0, -1.69065929318241, 0.73248077421585]
KB2, KA2 = [1.0, -2.0, 1.0], [1.0, -1.99004745483398, 0.99007225036621]


def configure(config):
    global ROOT, DST, WORK, MUSIC, TRACK_ALIASES, FF, PROBE
    ROOT = config['source_root']
    DST = config['destination_dir']
    WORK, MUSIC = config['work_dir'], config['music_dir']
    TRACK_ALIASES = config['track_aliases']
    FF, PROBE = config['ffmpeg'], config['ffprobe']


def run(cmd, inp=None):
    cmd = list(cmd)
    if cmd[0] == 'ffprobe':
        cmd[0] = PROBE
    if cmd[0] == 'ffmpeg':
        cmd[0] = FF
    if cmd[0] in (FF, PROBE):
        resolved = shutil.which(cmd[0])
        if not resolved:
            raise RuntimeError('缺少可执行媒体工具：%s' % cmd[0])
        cmd[0] = resolved
    result = subprocess.run(cmd, input=inp, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', 'replace')[-3000:])
    return result


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def decode(path, af=None, t=None):
    cmd = [FF, '-nostdin', '-v', 'error', '-i', path, '-vn']
    if af:
        # Essentials builds omit libsoxr; retain the built-in resampler fallback.
        if 'resampler=soxr' in af:
            cfg = run([FF, '-buildconf'])
            if b'--enable-libsoxr' not in cfg.stdout + cfg.stderr:
                af = af.replace(':resampler=soxr', '')
        cmd += ['-af', af]
    if t:
        cmd += ['-t', '%.6f' % t]
    cmd += ['-f', 'f32le', '-ac', '2', '-ar', str(SR), '-']
    return np.frombuffer(run(cmd).stdout, np.float32).reshape(-1, 2).astype(np.float64)


def kweight(x):
    n = len(x)
    N = 1 << int(np.ceil(np.log2(n + SR)))
    z = np.exp(-2j * np.pi * np.fft.rfftfreq(N))
    H = (np.polyval(KB1[::-1], z) / np.polyval(KA1[::-1], z)
         * np.polyval(KB2[::-1], z) / np.polyval(KA2[::-1], z))
    return np.fft.irfft(np.fft.rfft(x, N, axis=0) * H[:, None], N, axis=0)[:n]


def integrated(y):
    """y 为 K 计权后的信号，返回 BS.1770 综合响度 (LUFS)"""
    p = (y ** 2).sum(1)
    c = np.concatenate([[0.0], np.cumsum(p)])
    B, H = int(0.4 * SR), int(0.1 * SR)
    s = np.arange(0, len(p) - B + 1, H)
    z = (c[s + B] - c[s]) / B
    z = z[-0.691 + 10 * np.log10(z + 1e-20) > -70]
    if not len(z):
        return -np.inf
    gate = -0.691 + 10 * np.log10(z.mean()) - 10
    z = z[-0.691 + 10 * np.log10(z) > gate]
    return -0.691 + 10 * np.log10(z.mean())


def short_term(y, win=3.0, hop=0.1):
    """居中 3 秒窗的短时响度曲线"""
    p = (y ** 2).sum(1)
    c = np.concatenate([[0.0], np.cumsum(p)])
    W, H = int(win * SR), int(hop * SR)
    t = np.arange(0, len(p), H)
    a, b = np.clip(t - W // 2, 0, len(p)), np.clip(t + W // 2, 0, len(p))
    z = (c[b] - c[a]) / np.maximum(b - a, 1)
    return t / SR, -0.691 + 10 * np.log10(z + 1e-20)


def level_music(m):
    """慢速"推子"：BGM 短时响度偏离整体的部分拉回一半"""
    y = kweight(m)
    t, S = short_term(y)
    g = np.clip(-LEVEL_ALPHA * (S - integrated(y)), -4.0, 3.0)
    k = 11
    g = np.convolve(np.pad(g, k // 2, mode='edge'), np.ones(k) / k, mode='valid')
    return m * (10 ** (np.interp(np.arange(len(m)) / SR, t, g) / 20))[:, None]


def fades(n):
    if n <= 0:
        raise ValueError('音轨为空')
    env = np.ones(n)
    length = min(n, int(FADE_IN * SR))
    env[:length] *= np.sin(np.linspace(0, np.pi / 2, length)) ** 2
    length = min(n, int(FADE_OUT * SR))
    env[-length:] *= np.cos(np.linspace(0, np.pi / 2, length)) ** 2
    return env


def load_music(path, n):
    """曲子从头开始放；比视频短就切掉结尾渐弱和首尾静音，首尾相接循环（接缝等功率交叉淡化）"""
    m = decode(path, MUSIC_FX, t=n / SR + 1)
    if len(m) >= n:
        return m[:n], False
    t, S = short_term(kweight(m), win=1.0)
    body = np.flatnonzero(S > np.median(S) - 6)
    m = m[int(t[body[0]] * SR):int(t[body[-1]] * SR)]
    X = int(XFADE * SR)
    if len(m) < 4 * X:
        raise RuntimeError('曲子太短，没法循环')
    th = np.linspace(0, np.pi / 2, X)[:, None]
    out = m
    while len(out) < n:
        out = np.concatenate([out[:-X], out[-X:] * np.cos(th) + m[:X] * np.sin(th), m[X:]])
    return out[:n], True


def ebur128(path):
    s = run([FF, '-nostdin', '-hide_banner', '-nostats', '-i', path, '-vn',
             '-af', 'ebur128=peak=true', '-f', 'null', '-']).stderr.decode('utf-8', 'replace')
    s = s[s.rfind('Summary:'):]
    f = lambda k: float(re.search(k + r':\s+(-?[\d.]+)', s).group(1))
    return f('I'), f('LRA'), f('Peak')


def render(src, music, out, tmp, smr, target):
    """人声 + BGM 混好写到 out（画面直接复制），返回测量结果"""
    if os.path.exists(out) or os.path.realpath(out) in (os.path.realpath(src), os.path.realpath(music)):
        raise FileExistsError('渲染输出已存在或与输入重合')
    with open(out, 'xb'):
        pass
    with open(tmp, 'xb'):
        pass
    raw = decode(src)
    n = len(raw)
    I0 = integrated(kweight(raw))
    if not np.isfinite(I0):
        raise ValueError('源音轨没有可测量的人声响度')
    v = decode(src, 'volume=%.3fdB,%s' % (VOICE_PRE - I0, VOICE_FX))[:n]
    m, looped = load_music(music, n)
    assert len(v) == n and len(m) == n, (len(v), len(m), n)
    m = level_music(m) * fades(n)[:, None]

    vk, mk = kweight(v), kweight(m)
    Tv = target - 10 * np.log10(1 + 10 ** (-smr / 10))
    gv = 10 ** ((Tv - integrated(vk)) / 20)
    gm = 10 ** ((Tv - smr - integrated(mk)) / 20)
    mix = gv * v + gm * m
    trim = target - integrated(gv * vk + gm * mk)

    # 人声/BGM 随时间的差值（只看有人声、且不在淡入淡出里的部分）
    tv, Sv = short_term(gv * vk)
    _, Sm = short_term(gm * mk)
    sel = (Sv > Tv - 10) & (tv > FADE_IN + 1.5) & (tv < n / SR - FADE_OUT - 1.5)
    d = (Sv - Sm)[sel] if sel.any() else Sv - Sm

    limit = LIMIT
    for _ in range(4):
        (mix * 10 ** (trim / 20)).astype(np.float32).tofile(tmp)
        run([FF, '-nostdin', '-y', '-v', 'error', '-i', src,
             '-f', 'f32le', '-ar', str(SR), '-ch_layout', 'stereo', '-i', tmp,
             '-filter_complex', '[1:a]alimiter=limit=%.5f:attack=5:release=80:level=0:latency=1[a]' % limit,
             '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
             '-movflags', '+faststart', out])
        I, LRA, TP = ebur128(out)
        if abs(I - target) <= 0.15 and TP <= TP_MAX:
            break
        trim += target - I
        if TP > TP_MAX:
            limit *= 10 ** ((TP_MAX - 0.3 - TP) / 20)
    os.remove(tmp)
    return dict(src_I=round(I0, 1), voice_gain_dB=round(VOICE_PRE - I0 + 20 * np.log10(gv) + trim, 1),
                smr_LU=round(float(integrated(gv * vk) - integrated(gm * mk)), 1),
                smr_p5=round(float(np.percentile(d, 5)), 1), smr_med=round(float(np.median(d)), 1),
                I=I, LRA=LRA, TP=TP, looped=looped)


def streams(path):
    r = run(['ffprobe', '-v', 'error', '-count_packets', '-show_entries',
             'stream=codec_type,codec_name,duration,nb_read_packets', '-of', 'json', path]).stdout
    s = {}
    for st in json.loads(r)['streams']:
        s.setdefault(st['codec_type'], st)
    return s


def check(src, out, rec, target):
    """成品和原片逐项对比，返回不合格项"""
    a, b = streams(src), streams(out)
    dur = lambda s, k: float(s.get(k, {}).get('duration', 0))
    bad = []
    if not np.isfinite(rec['I']) or not np.isfinite(rec['TP']):
        bad.append('响度或真峰值不可测量')
    if (a['video'].get('nb_read_packets') != b.get('video', {}).get('nb_read_packets')
            or abs(dur(a, 'video') - dur(b, 'video')) > 0.05):
        bad.append('画面和原片不一致')
    if abs(dur(a, 'audio') - dur(b, 'audio')) > 0.1:
        bad.append('音频时长和原片不一致')
    if abs(rec['I'] - target) > 0.5:
        bad.append('响度 %.1f LUFS' % rec['I'])
    if rec['TP'] > TP_MAX:
        bad.append('真峰值 %.1f dBTP' % rec['TP'])
    source_video_hash = run([FF, '-nostdin', '-v', 'error', '-i', src, '-map', '0:v:0', '-c:v', 'copy', '-f', 'hash', '-hash', 'sha256', '-']).stdout.strip()
    output_video_hash = run([FF, '-nostdin', '-v', 'error', '-i', out, '-map', '0:v:0', '-c:v', 'copy', '-f', 'hash', '-hash', 'sha256', '-']).stdout.strip()
    if source_video_hash != output_video_hash:
        bad.append('视频流 SHA-256 与原片不一致')
    run([FF, '-nostdin', '-v', 'error', '-xerror', '-i', out, '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'])
    return bad


def publish(path, dst):
    """Exclusive creation on every platform; preserve source until copy is complete."""
    if os.path.realpath(path) == os.path.realpath(dst):
        raise ValueError('源与目标路径相同')
    with open(path, 'rb') as source, open(dst, 'xb') as target:
        shutil.copyfileobj(source, target, 16 << 20)
        target.flush()
        os.fsync(target.fileno())
    if file_hash(path) != file_hash(dst):
        raise RuntimeError('复制后校验不一致，保留源文件')
    os.remove(path)


def find_src(arg):
    if arg == 'latest':
        if not ROOT:
            sys.exit('latest 需要配置 source_root，或直接提供成片目录')
        folders = sorted(d for d in glob.glob(os.path.join(ROOT, '成片-*')) if os.path.isdir(d))
        if folders:
            return os.path.abspath(folders[-1])
    candidates = [arg]
    if ROOT:
        candidates += [os.path.join(ROOT, arg), os.path.join(ROOT, '成片-' + arg)]
    for directory in candidates:
        if os.path.isdir(directory):
            return os.path.abspath(directory)
    sys.exit('找不到成片文件夹：%s' % arg)


def scan(src_dir):
    """{序号: (标题, 路径)}，只认「序号-标题（无音乐）.mp4」"""
    vids = {}
    for p in sorted(glob.glob(os.path.join(src_dir, '*.mp4'))):
        m = NAME_RE.fullmatch(os.path.basename(p))
        if not m:
            continue
        k = int(m.group(1))
        if k in vids:
            print('注意：第 %d 条有两个文件，只用 %s' % (k, os.path.basename(vids[k][1])))
            continue
        vids[k] = (safe_title(m.group(2)), p)
    return vids


def read_tags(src_dir):
    """标题与话题-*.md 里每条的话题行"""
    tags = {}
    for md in glob.glob(os.path.join(src_dir, '标题与话题*.md')):
        k = None
        with open(md, encoding='utf-8') as f:
            for line in f:
                m = re.match(r'##\s*(\d+)\s', line)
                if m:
                    k = int(m.group(1))
                elif k is not None and line.startswith('#') and k not in tags:
                    tags[k] = line.strip()
    return tags


WIN_BAD = str.maketrans('\\/:*?"<>|', '＼／：＊？＂＜＞｜')  # 文件名里不能有的字符换成全角


def safe_title(value):
    value = re.sub(r'[\x00-\x1f\x7f]', '', str(value)).translate(WIN_BAD).strip().rstrip('. ')
    if not value or value in ('.', '..'):
        raise ValueError('标题为空或不能用于文件名')
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', value, re.I):
        value = '_' + value
    return value


def read_md(md):
    """清单 md 里的表格 → {序号: (标题, 视频路径)}, {序号: 话题}
    表头包含序号、标题（或生成标题）、话题；视频列为本机 .mp4 链接，支持相对路径与空格。"""
    base = os.path.dirname(os.path.abspath(md))
    vids, tags, cols = {}, {}, None
    with open(md, encoding='utf-8-sig') as f:
        for line in f:
            if not line.lstrip().startswith('|'):
                cols = None
                continue
            cells = [c.strip().replace(r'\|', '|') for c in re.split(r'(?<!\\)\|', line.strip().strip('|'))]
            if cols is None:  # 表头
                find = lambda ok: next((i for i, c in enumerate(cells) if ok(c)), None)
                t = find(lambda c: c == '标题')
                t = find(lambda c: '标题' in c and '原' not in c and '候选' not in c) if t is None else t
                cols = (find(lambda c: '序号' in c), t, find(lambda c: '话题' in c))
                continue
            (ni, ti, gi), m = cols, None
            if ni is None or ti is None or len(cells) <= max(ni, ti):
                continue
            num = re.fullmatch(r'0*(\d+)', cells[ni])
            if not num:
                continue
            for c in cells:
                m = re.search(r'\]\(\s*<(.+?\.mp4)>', c, re.I) or re.search(r'\]\(\s*(.+\.mp4)\s*\)', c, re.I)
                if m and not m.group(1).startswith('http'):
                    break
                m = None
            k, path = int(num.group(1)), None
            if m:
                path = os.path.normpath(os.path.join(base, m.group(1)))
                if not os.path.isfile(path) and os.path.isfile(urllib.parse.unquote(path)):
                    path = urllib.parse.unquote(path)
            if k in vids:
                print('注意：清单里第 %d 条出现了两次，只用第一次的' % k)
                continue
            vids[k] = (safe_title(cells[ti]), path)
            if gi is not None and gi < len(cells):
                tags[k] = cells[gi]
            if not (path and os.path.isfile(path)):
                print('注意：第 %d 条的视频找不到：%s' % (k, path or '（这一行没有视频链接）'))
    return vids, tags


def suggest(title, tags=''):
    for track, t_re, g_re in RULES:
        if re.search(t_re, title) or (g_re and re.search(g_re, tags)):
            return track
    return DEFAULT_TRACK


def library(music_dir):
    lib = {}
    for p in sorted(glob.glob(os.path.join(music_dir, '*'))):
        stem, ext = os.path.splitext(os.path.basename(p))
        if ext.lower() in AUDIO_EXT:
            lib[stem] = p
    if not lib:
        sys.exit('曲库是空的：%s' % music_dir)
    return lib


def pick_track(name, lib):
    name = TRACK_ALIASES.get(name) or name
    if name in lib:
        return name
    hits = [k for k in lib if name in k]
    if len(hits) == 1:
        return hits[0]
    sys.exit('曲目「%s」%s。曲库里有：%s' % (name, '对应好几首' if hits else '不在曲库里', ' / '.join(lib)))


def parse_picks(items, nums):
    """16-  16-20  17  17=似水  16-20=五音 → {序号: 指定曲目或 None(用建议)}"""
    picks = {}
    for a in items:
        m = re.fullmatch(r'(\d+)(-(\d*))?(=(.+))?', a)
        if not m:
            sys.exit('看不懂的参数：%s（写法：16-  16-20  17  17=似水  16-20=五音）' % a)
        lo = int(m.group(1))
        hi = lo if m.group(2) is None else int(m.group(3) or max(nums))
        sel = [k for k in nums if lo <= k <= hi]
        if not sel:
            sys.exit('成片文件夹里没有第 %s 条' % a.split('=')[0])
        for k in sel:
            if m.group(5) or k not in picks:
                picks[k] = m.group(5)
    return picks


def fmt_nums(nums):
    """[16,17,18,21] → 16~18、21"""
    nums, out, i = sorted(nums), [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else '%d~%d' % (nums[i], nums[j]))
        i = j + 1
    return '、'.join(out)


def last_line(e):
    lines = [s for s in str(e).strip().splitlines() if s.strip()]
    return (lines[-1] if lines else repr(e))[:200]


def show_list(src_dir, vids, tags, dst, only):
    print('成片来源：%s（共 %d 条）\n' % (src_dir, len(vids)))
    print('| 序号 | 标题 | 时长 | 建议曲目 | 导入目标 | 话题 |')
    print('|---|---|---|---|---|---|')
    for k in only or sorted(vids):
        title, path = vids[k]
        if not (path and os.path.isfile(path)):
            print('| %02d | %s | 找不到视频 | %s |  | %s |' % (k, title, suggest(title, tags.get(k, '')), tags.get(k, '')))
            continue
        dur = float(run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                         '-of', 'csv=p=0', path]).stdout)
        have = '已有同名' if dst and os.path.exists(os.path.join(dst, title + '.mp4')) else ''
        print('| %02d | %s | %d:%02d | %s | %s | %s |' % (
            k, title, dur // 60, dur % 60, suggest(title, tags.get(k, '')), have, tags.get(k, '')))


def append_record(work, src_dir, rows, smr, target, now):
    path = os.path.join(work, '配乐清单.md')
    label = os.path.basename(src_dir)
    label = label[:-3] if label.lower().endswith('.md') else label
    with open(path, 'a', encoding='utf-8') as f:
        if f.tell() == 0:
            f.write('# 配乐记录\n\n由 paean skill 的 render.py 每批自动追加。\n')
        f.write('\n## %s 第%s条（%s，人声高出 BGM %g LU，整体 %g LUFS）\n\n' % (
            label, fmt_nums([r['num'] for r in rows]), now.strftime('%Y-%m-%d %H:%M'), smr, target))
        f.write('| 序号 | 标题 | 配乐 | 结果 |\n|---|---|---|---|\n')
        for r in rows:
            f.write('| %02d | %s | %s | %s |\n' % (r['num'], r['title'], r['track'], r['status']))


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument('--config')
    config_arg, _ = pre.parse_known_args()
    configure(load_config(config_arg.config))
    ap = argparse.ArgumentParser(description='口播视频批量配 BGM', epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('src', help='成片文件夹：完整路径 / 日期如 2026-09-26 / latest；或清单 md 的路径')
    ap.add_argument('picks', nargs='*', help='要做哪几条：16-（到最后） 16-20  17  17=似水  16-20=五音')
    ap.add_argument('--list', action='store_true', help='只列清单和建议曲目，不渲染')
    ap.add_argument('--smr', type=float, default=SMR, help='人声比 BGM 高多少 LU，默认 9；BGM 要大一点用 7，小一点用 11')
    ap.add_argument('--target', type=float, default=TARGET_I, help='整体响度 LUFS，默认 -16')
    ap.add_argument('--config', help='本机 JSON 配置文件；默认读取工作目录的 .paean-config.json')
    ap.add_argument('--dst', default=DST, help='可选本机导入目录；未配置时只输出工作区')
    ap.add_argument('--work', default=WORK, help='工作区（output/ backup/ 配乐清单.md 在这里）')
    ap.add_argument('--music', default=MUSIC, help='曲库文件夹')
    ap.add_argument('--no-publish', action='store_true', help='只生成到 工作区/output，不导入')
    ap.add_argument('--replace', action='store_true', help='目标已有同名文件时先备份到 工作区/backup 再替换')
    a = ap.parse_intermixed_args()
    a.dst = os.path.abspath(os.path.expanduser(a.dst)) if a.dst else None
    a.work, a.music = (os.path.abspath(os.path.expanduser(p)) for p in (a.work, a.music))
    if not np.isfinite(a.smr) or not np.isfinite(a.target):
        ap.error('smr 和 target 必须是有限数值')
    if a.replace and (not a.dst or a.no_publish):
        ap.error('--replace 需要明确导入目录，且不能与 --no-publish 同用')

    if a.src.lower().endswith('.md') and os.path.isfile(a.src):
        src_dir = os.path.normpath(a.src)
        vids, tags = read_md(src_dir)
        if not vids:
            sys.exit('%s 里没找到「| 序号 | 标题 | 话题 | 成片 |」这样的表格' % src_dir)
    else:
        src_dir = find_src(a.src)
        vids = scan(src_dir)
        if not vids:
            sys.exit('%s 里没有「序号-标题（无音乐）.mp4」这样的视频' % src_dir)
        tags = read_tags(src_dir)
    picks = parse_picks(a.picks, sorted(vids)) if a.picks else {}
    if a.list:
        return show_list(src_dir, vids, tags, a.dst, sorted(picks))
    if not picks:
        sys.exit('请指定要做哪几条，例如 16-（第16条到最后）、17、17=似水；可以先用 --list 看清单')

    lib = library(a.music)
    plan = [(k, vids[k][0], vids[k][1], pick_track(picks[k] or suggest(vids[k][0], tags.get(k, '')), lib))
            for k in sorted(picks)]
    run_id = uuid.uuid4().hex
    out_dir, tmp_dir = os.path.join(a.work, 'output', run_id), os.path.join(a.work, 'tmp', run_id)
    if a.dst and not a.no_publish:
        os.makedirs(a.dst, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)
    now = datetime.datetime.now().astimezone()
    stamp = now.strftime('%Y%m%d-%H%M%S')
    print('成片来源：%s，共 %d 条；人声高出 BGM %g LU，整体 %g LUFS\n' % (src_dir, len(plan), a.smr, a.target), flush=True)

    rows = []
    for k, title, src, track in plan:
        row_dir = os.path.join(out_dir, '%02d' % k)
        os.makedirs(row_dir, exist_ok=False)
        out = os.path.join(row_dir, title + '.mp4')
        dst = os.path.join(a.dst, title + '.mp4') if a.dst else None
        rec = dict(run_id=run_id, time=now.isoformat(timespec='seconds'), src_dir=src_dir, num=k, title=title,
                   track=track, smr=a.smr, target=a.target, src=src)
        try:
            if not (src and os.path.isfile(src)):
                raise FileNotFoundError('找不到视频：%s' % src)
            if dst and os.path.realpath(dst) in {os.path.realpath(p) for _, _, p, _ in plan if p}:
                raise ValueError('导入目标与源视频重合，拒绝替换')
            source_before = file_hash(src)
            tmp = os.path.join(tmp_dir, 'mix_%d_%02d.f32' % (os.getpid(), k))
            rec.update(render(src, lib[track], out, tmp, a.smr, a.target))
            bad = check(src, out, rec, a.target)
            if file_hash(src) != source_before:
                bad.append('源视频在制作期间发生变化')
            rec.update(source_sha256=source_before, output_sha256=file_hash(out), validation_status='failed' if bad else 'passed')
            if bad:
                status = '校验未通过（%s），留在工作区 output' % '，'.join(bad)
            elif a.no_publish or not a.dst:
                status = '已生成在工作区 output（未导入）'
            elif os.path.exists(dst) and not a.replace:
                status = '目标文件夹已有同名文件，未覆盖；新版在工作区 output'
            else:
                if os.path.exists(dst):
                    bak = os.path.join(a.work, 'backup', stamp + '-' + run_id, '%02d' % k)
                    os.makedirs(bak, exist_ok=True)
                    backup_file = os.path.join(bak, title + '.mp4')
                    publish(dst, backup_file)
                    rec['backup_file'] = backup_file
                    status = '已替换（旧文件备份：%s）' % backup_file
                else:
                    status = '已导入 %s' % os.path.basename(os.path.normpath(a.dst))
                publish(out, dst)
                out = dst
        except Exception as e:
            status = '失败：%s' % last_line(e)
        rec.update(status=status, out=out)
        rows.append(rec)
        print('[%02d] %s | %s | %s LUFS | %s dBTP | %s' % (k, title, track, rec.get('I', '-'), rec.get('TP', '-'), status),
              flush=True)
        with open(os.path.join(a.work, 'render_log.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')

    append_record(a.work, src_dir, rows, a.smr, a.target, now)
    print('\n| 序号 | 标题 | 曲目 | 响度 LUFS | 真峰值 dBTP | 人声高出BGM（中位/最少） | 结果 |')
    print('|---|---|---|---|---|---|---|')
    for r in rows:
        print('| %02d | %s | %s%s | %s | %s | %s | %s |' % (
            r['num'], r['title'], r['track'], '（循环）' if r.get('looped') else '', r.get('I', '-'), r.get('TP', '-'),
            '%s / %s' % (r['smr_med'], r['smr_p5']) if 'smr_med' in r else '-', r['status']))
    if any(s in r['status'] for r in rows for s in ('失败', '校验未通过')):
        sys.exit(1)


if __name__ == '__main__':
    main()
