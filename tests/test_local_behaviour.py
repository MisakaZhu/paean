"""Verify local configuration, private-directory opt-in and file preservation."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'paean' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import paean_config as C
import render as R
import identify_music as I


class LocalBehaviour(unittest.TestCase):
    def test_defaults_do_not_discover_personal_directories(self):
        with tempfile.TemporaryDirectory() as folder, patch('pathlib.Path.cwd', return_value=Path(folder)):
            config = C.load_config()
            for field in ('source_root', 'destination_dir', 'cache_dir', 'drafts_dir'):
                self.assertIsNone(config[field])
            self.assertFalse(any(Path(folder).iterdir()))

    def test_relative_config_paths_and_invalid_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            config_file = Path(folder) / 'config.json'
            config_file.write_text(json.dumps({'music_dir': './tracks', 'track_aliases': {'舒缓': 'my-track'}}), encoding='utf-8')
            config = C.load_config(config_file)
            self.assertEqual(config['music_dir'], str((Path(folder) / 'tracks').resolve()))
            with patch.object(R, 'TRACK_ALIASES', config['track_aliases']):
                self.assertEqual(R.pick_track('舒缓', {'my-track': 'file.wav'}), 'my-track')
            for invalid in ({'unknown': 'x'}, {'work_dir': None}, {'music_dir': 'https://example.com'},
                            {'track_aliases': {'舒缓': '../private.wav'}}):
                config_file.write_text(json.dumps(invalid), encoding='utf-8')
                with self.assertRaises(ValueError):
                    C.load_config(config_file)

    def test_markdown_extra_columns_relative_spaces_and_pipe_title(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / 'source video.mp4').write_bytes(b'fixture')
            md = base / 'list.md'
            md.write_text('| 序号 | 原标题 | 生成标题 | 话题 | 来源 | 成片 | 备注 |\n'
                          '| --- | --- | --- | --- | --- | --- | --- |\n'
                          '| 01 | original | 呼吸\\|习惯 | #日常 | local | [视频](<source video.mp4>) | done |\n', encoding='utf-8')
            videos, tags = R.read_md(str(md))
            self.assertEqual(videos[1], ('呼吸｜习惯', str(base / 'source video.mp4')))
            self.assertEqual(tags[1], '#日常')
            self.assertEqual(R.safe_title('CON'), '_CON')
            with self.assertRaises(ValueError):
                R.safe_title('...')

    def test_publish_never_overwrites_and_verifies_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / 'new.mp4', Path(folder) / 'existing.mp4'
            source.write_bytes(b'new content')
            target.write_bytes(b'old content')
            with self.assertRaises(FileExistsError):
                R.publish(str(source), str(target))
            self.assertEqual(source.read_bytes(), b'new content')
            self.assertEqual(target.read_bytes(), b'old content')
            empty_target = Path(folder) / 'fresh.mp4'
            digest = R.file_hash(source)
            R.publish(str(source), str(empty_target))
            self.assertFalse(source.exists())
            self.assertEqual(R.file_hash(empty_target), digest)

    def test_cache_and_drafts_require_explicit_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            music, cache, drafts = (base / name for name in ('music', 'cache', 'drafts'))
            for directory in (music, cache, drafts / 'example'):
                directory.mkdir(parents=True)
            (music / 'mine.wav').write_bytes(b'library')
            (cache / 'cached.wav').write_bytes(b'cache')
            (drafts / 'example' / 'key_value.json').write_text(json.dumps({'1': {
                'materialName': 'example-song', 'materialCategory': 'audio',
                'materialSubcategory': 'music'}}), encoding='utf-8')
            with patch.object(I, 'CACHE', None), patch.object(I, 'DRAFTS', None):
                self.assertEqual(I.candidates(str(music)), [str(music / 'mine.wav')])
                self.assertEqual(I.draft_music('example'), [])
            with patch.object(I, 'CACHE', str(cache)), patch.object(I, 'DRAFTS', str(drafts)):
                self.assertEqual(len(I.candidates(str(music))), 2)
                self.assertIn('example-song', I.draft_music('example')[0])

    def test_short_audio_fades_do_not_fail(self):
        env = R.fades(R.SR // 2)
        self.assertEqual(len(env), R.SR // 2)
        self.assertEqual(env[0], 0)
        self.assertLess(env[-1], 1e-20)
        self.assertTrue(((0 <= env) & (env <= 1)).all())


if __name__ == '__main__':
    unittest.main()
