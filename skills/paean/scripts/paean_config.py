"""Local path configuration. Importing this module does not read or write user files."""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS = {
    'source_root': None,
    'destination_dir': None,
    'work_dir': './paean-work',
    'music_dir': './music',
    'ffmpeg': 'ffmpeg',
    'ffprobe': 'ffprobe',
    'cache_dir': None,
    'drafts_dir': None,
    'track_aliases': {},
}
PATH_FIELDS = ('source_root', 'destination_dir', 'work_dir', 'music_dir', 'cache_dir', 'drafts_dir')


def load_config(filename=None):
    config_path = Path(filename).expanduser().resolve() if filename else Path.cwd() / '.paean-config.json'
    values = dict(DEFAULTS)
    base = Path.cwd()
    if filename or config_path.exists():
        with config_path.open(encoding='utf-8-sig') as handle:
            custom = json.load(handle)
        if not isinstance(custom, dict):
            raise ValueError('配置必须是 JSON 对象')
        unknown = set(custom) - set(DEFAULTS)
        if unknown:
            raise ValueError('不支持的配置字段；请按 config.example.json 配置')
        values.update(custom)
        base = config_path.parent
    for field in PATH_FIELDS:
        value = values[field]
        if value is None:
            if field in ('work_dir', 'music_dir'):
                raise ValueError('work_dir 和 music_dir 不能为空')
            continue
        if not isinstance(value, str) or not value.strip() or '://' in value:
            raise ValueError('目录配置必须是非空本机路径')
        values[field] = str((base / Path(value).expanduser()).resolve())
    for field in ('ffmpeg', 'ffprobe'):
        value = values[field]
        if not isinstance(value, str) or not value.strip() or '://' in value:
            raise ValueError('媒体工具必须是命令名称或本机可执行文件路径')
        if '/' in value or '\\' in value:
            values[field] = str((base / Path(value).expanduser()).resolve())
    aliases = values['track_aliases']
    if not isinstance(aliases, dict):
        raise ValueError('track_aliases 必须是曲目别名映射')
    for name, track in aliases.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError('曲目别名不能为空')
        if track is not None and (not isinstance(track, str) or not track.strip() or
                                  any(char in track for char in ('/', '\\', '\n', '\r'))):
            raise ValueError('曲目别名指向曲库文件名，不接受其他路径')
    return values
