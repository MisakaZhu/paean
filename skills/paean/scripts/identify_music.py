# -*- coding: utf-8 -*-
"""
识别一条成片用的是哪首配乐：和曲库、剪映音乐缓存里的每首曲子做互相关；
能找到对应的无音乐原片时，再估算人声 / BGM 各自的音量和两者差多少（SMR）。

  python identify_music.py ./output/成片.mp4
  python identify_music.py 成片.mp4 --source "原片（无音乐）.mp4"
  python identify_music.py 成片.mp4 --cache-dir ./authorized-cache
  python identify_music.py 成片.mp4 --cache-dir ./authorized-cache --save 曲名 --confirm-match
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import sys

import numpy as np

sys.dont_write_bytecode = True  # 别在 skill 文件夹里留 __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render as R  # noqa: E402

CACHE = None
DRAFTS = None


LO = 8000  # 找曲子用 8 kHz 单声道就够了


def pcm(path):
    r = R.run([R.FF, '-nostdin', '-v', 'error', '-i', path, '-vn', '-ac', '1', '-ar', str(LO), '-f', 'f32le', '-'])
    return np.frombuffer(r.stdout, np.float32).astype(np.float64)


def best_offset(seg, y):
    """seg 在 y 里最像的位置 → (归一化相关系数, 在 y 里的秒数)"""
    L = len(seg)
    N = 1 << int(np.ceil(np.log2(L + len(y))))
    c = np.fft.irfft(np.fft.rfft(y, N) * np.conj(np.fft.rfft(seg, N)), N)[:len(y)]
    e = np.concatenate([[0.0], np.cumsum(y ** 2)])
    k = np.arange(len(y))
    w = e[np.minimum(k + L, len(y))] - e[k]
    nc = c / np.sqrt(w * np.sum(seg ** 2) + 1e-12)
    nc[w < 1e-3 * w.max()] = 0
    i = int(np.argmax(nc))
    return nc[i], i / LO


def candidates(music_dir):
    """曲库 + 剪映缓存；同一个文件只算一次，优先用曲库里的名字"""
    seen, out = set(), []
    for p in sorted(glob.glob(os.path.join(music_dir, '*'))) + (sorted(glob.glob(os.path.join(CACHE, '*'))) if CACHE else []):
        if os.path.splitext(p)[1].lower() not in R.AUDIO_EXT:
            continue
        with open(p, 'rb') as f:
            h = hashlib.md5(f.read()).hexdigest()
        if h not in seen:
            seen.add(h)
            out.append(p)
    return out


def title_of(path):
    s = os.path.splitext(os.path.basename(path))[0]
    s = re.sub(r'^\d+\s*[-_.、．]\s*', '', s)
    return re.sub(r'\s*[（(]无音乐[）)]$', '', s)


def logged_src(title):
    """render.py 的记录里这个标题最近一次用的无音乐原片（清单 md 那种批次，原片不在 成片-* 文件夹里）"""
    src = None
    try:
        with open(R.WORK + '/render_log.jsonl', encoding='utf-8') as f:
            for line in f:
                r = json.loads(line)
                if r.get('title') == title and r.get('src') and os.path.isfile(r['src']):
                    src = r['src']
    except (OSError, ValueError):
        pass
    return src


def draft_music(title):
    """剪映草稿里和这个标题对得上的草稿，用了哪些音乐"""
    found = []
    if not DRAFTS:
        return found
    for kv in glob.glob(DRAFTS + '/*/key_value.json'):
        try:
            with open(kv, encoding='utf-8') as f:
                items = [v for v in json.load(f).values() if isinstance(v, dict)]
        except Exception:
            continue
        folder = os.path.basename(os.path.dirname(kv))
        if title in folder or any(title in str(v.get('materialName', '')) for v in items):
            found += ['%s（草稿「%s」）' % (v.get('materialName'), folder) for v in items
                      if v.get('materialCategory') == 'audio' and v.get('materialSubcategory') == 'music']
    return list(dict.fromkeys(found))


def lag(a, b, maxlag=LO // 2):
    """b 比 a 晚多少个采样"""
    n = min(len(a), len(b))
    N = 1 << int(np.ceil(np.log2(2 * n)))
    c = np.fft.irfft(np.fft.rfft(b[:n], N) * np.conj(np.fft.rfft(a[:n], N)), N)
    return int(np.argmax(np.concatenate([c[-maxlag:], c[:maxlag + 1]]))) - maxlag


def shift(x, k, n):
    """x 往后挪 k 个采样（k<0 往前），截成 n 长"""
    y = np.zeros(n)
    s = x[max(-k, 0):][:n - max(k, 0)]
    y[max(k, 0):max(k, 0) + len(s)] = s
    return y


def fit_gains(fin, voice, music):
    """成片 ≈ a·人声 + b·BGM，按 2 秒一段做最小二乘，取中位数"""
    W, ab = 2 * LO, []
    for s in range(0, len(fin) - W, W):
        M = np.stack([voice[s:s + W], music[s:s + W]], 1)
        if np.abs(M).max() > 0:
            ab.append(np.linalg.lstsq(M, fin[s:s + W], rcond=None)[0])
    if not ab:
        raise ValueError('可用于估计的音频片段不足，不能给出可靠增益')
    return np.median(np.array(ab), 0)


def main():
    global CACHE, DRAFTS
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument('--config')
    config_arg, _ = pre.parse_known_args()
    config = R.load_config(config_arg.config)
    R.configure(config)
    ap = argparse.ArgumentParser(description='识别成片用的配乐', epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('video', help='带配乐的本机成片')
    ap.add_argument('--config', help='本机 JSON 配置')
    ap.add_argument('--cache-dir', default=config['cache_dir'], help='显式允许读取的音乐缓存目录；默认不读')
    ap.add_argument('--drafts-dir', default=config['drafts_dir'], help='显式允许读取的草稿目录；默认不读')
    ap.add_argument('--confirm-match', action='store_true', help='已人工核对当前最高匹配且有权保存该音频')
    ap.add_argument('--source', help='对应的无音乐原片（不给就按标题去 成片-* 文件夹和 render_log.jsonl 里找）')
    ap.add_argument('--music', default=R.MUSIC, help='曲库文件夹')
    ap.add_argument('--save', metavar='曲名', help='把识别出的曲子复制进曲库，存成这个名字')
    a = ap.parse_args()
    CACHE = os.path.abspath(os.path.expanduser(a.cache_dir)) if a.cache_dir else None
    DRAFTS = os.path.abspath(os.path.expanduser(a.drafts_dir)) if a.drafts_dir else None
    a.music = os.path.abspath(os.path.expanduser(a.music))
    if a.save and not a.confirm_match:
        ap.error('--save 需要先核对匹配与音频使用权，再加 --confirm-match')
    for p in (a.video, a.source):
        if p and not os.path.isfile(p):
            sys.exit('找不到文件：%s' % p)

    fin = pcm(a.video)
    title = title_of(a.video)
    s0 = min(5.0, max(0.0, (len(fin) / LO - 20) / 2))
    seg = fin[int(s0 * LO):int((s0 + 20) * LO)]
    res = []
    for p in candidates(a.music):
        y = pcm(p)
        if len(y) > LO:
            c, pos = best_offset(seg, y)
            res.append((c, pos - s0, p))
    res.sort(reverse=True)
    I, LRA, TP = R.ebur128(a.video)
    print('成片：%s（%.1f 秒，%.1f LUFS，真峰值 %.1f dBTP）' % (a.video, len(fin) / LO, I, TP))
    print('最像的曲子（相关系数越大越像，正确的一般比第二名高出好几倍）：')
    for c, off, p in res[:3]:
        where = '曲库' if os.path.normcase(os.path.dirname(os.path.abspath(p))) == os.path.normcase(os.path.abspath(a.music)) else '剪映缓存'
        print('  %.3f  %s（%s）  成片开头对应曲子第 %.1f 秒' % (c, os.path.basename(p), where, off))
    names = draft_music(title)
    if names:
        print('剪映草稿里记录的音乐：' + '；'.join(names))
    if not res:
        return
    c, off, track = res[0]

    src = a.source
    if not src:
        hits = sorted(glob.glob('%s/成片-*/*%s（无音乐）.mp4' % (R.ROOT, glob.escape(title)))) if R.ROOT else []
        src = os.path.normpath(hits[-1]) if hits else logged_src(title)
    if src:
        v, m = pcm(src), pcm(track)
        n = len(fin)
        v = shift(v, lag(v, fin), n)
        m = shift(m, -int(round(off * LO)), n)
        ga, gb = fit_gains(fin, v, m)
        v48, m48 = R.decode(src), R.decode(track)
        k = int(round(off * R.SR))
        m48 = m48[max(k, 0):] if k >= 0 else np.concatenate([np.zeros((-k, 2)), m48])
        m48 = np.pad(m48[:len(v48)], ((0, max(0, len(v48) - len(m48))), (0, 0)))
        smr = R.integrated(R.kweight(ga * v48)) - R.integrated(R.kweight(gb * m48))
        print('无音乐原片：%s' % src)
        print('人声音量 %+.1f dB，BGM 音量 %+.1f dB（相对原始文件）→ 人声比 BGM 高 %.1f LU（render.py 默认 9）'
              % (20 * np.log10(abs(ga)), 20 * np.log10(abs(gb)), smr))

    if a.save:
        if os.path.normcase(os.path.dirname(os.path.abspath(track))) == os.path.normcase(os.path.abspath(a.music)):
            print('这首已经在曲库里了：%s' % os.path.basename(track))
            return
        fmt = R.run(['ffprobe', '-v', 'error', '-show_entries', 'format=format_name', '-of', 'csv=p=0', track]).stdout
        dst = os.path.join(a.music, R.safe_title(a.save) + ('.m4a' if b'mp4' in fmt else os.path.splitext(track)[1]))
        if os.path.exists(dst):
            sys.exit('曲库里已经有 %s，换个名字' % dst)
        os.makedirs(a.music, exist_ok=True)
        with open(track, 'rb') as source, open(dst, 'xb') as target:
            shutil.copyfileobj(source, target)
        if R.file_hash(track) != R.file_hash(dst):
            raise RuntimeError('曲库复制校验失败，源音频保留')
        print('已存进曲库：%s（render.py 里写「%s」就能用）' % (dst, a.save))


if __name__ == '__main__':
    main()
