"""Exercise the real CLI with generated media, without private videos or songs."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'paean' / 'scripts'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg and ffprobe required')
class SyntheticMedia(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        cls.videos, cls.music = cls.base / 'videos', cls.base / 'music'
        cls.videos.mkdir()
        cls.music.mkdir()
        cls.source = cls.videos / '01-合成习惯（无音乐）.mp4'
        cls.track = cls.music / 'synthetic-track.wav'
        cls.media(['-f', 'lavfi', '-i', 'testsrc2=size=160x240:rate=10:duration=8',
                   '-f', 'lavfi', '-i', 'sine=frequency=330:sample_rate=48000:duration=8',
                   '-c:v', 'mpeg4', '-q:v', '6', '-c:a', 'aac', '-shortest', str(cls.source)])
        cls.media(['-f', 'lavfi', '-i',
                   'aevalsrc=0.12*sin(2*PI*(180*t+5*t*t))+0.05*sin(2*PI*660*t):s=48000:d=20',
                   '-c:a', 'pcm_s16le', str(cls.track)])
        cls.source_hash, cls.track_hash = sha(cls.source), sha(cls.track)
        cls.config = cls.base / 'config.json'
        cls.config.write_text(json.dumps({'music_dir': './music',
            'track_aliases': {'似水': 'synthetic-track'},
            'ffmpeg': shutil.which('ffmpeg'), 'ffprobe': shutil.which('ffprobe')}), encoding='utf-8')

    @classmethod
    def media(cls, args):
        result = subprocess.run([shutil.which('ffmpeg'), '-nostdin', '-v', 'error', *args], capture_output=True)
        if result.returncode:
            raise AssertionError(result.stderr.decode('utf-8', 'replace'))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def cli(self, script, args, expected=0):
        env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-B', str(SCRIPTS / script), *map(str, args)],
            cwd=self.base, capture_output=True, text=True, encoding='utf-8', env=env, timeout=120)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def records(self, work):
        return [json.loads(line) for line in (work / 'render_log.jsonl').read_text(encoding='utf-8').splitlines()]

    def render_args(self, work):
        return [self.videos, '1', '--config', self.config, '--work', work]

    def test_list_does_not_create_work_or_destination(self):
        work, destination = self.base / 'list-work', self.base / 'list-dst'
        self.cli('render.py', [self.videos, '--list', '--config', self.config,
                              '--work', work, '--dst', destination])
        self.assertFalse(work.exists())
        self.assertFalse(destination.exists())

    def test_preview_validates_audio_and_preserves_both_inputs(self):
        work, destination = self.base / 'preview-work', self.base / 'preview-dst'
        self.cli('render.py', self.render_args(work) + ['--no-publish', '--dst', destination])
        record = self.records(work)[0]
        self.assertEqual(record['validation_status'], 'passed')
        self.assertLessEqual(abs(record['I'] + 16), 0.5)
        self.assertLessEqual(record['TP'], -1)
        self.assertEqual(record['source_sha256'], self.source_hash)
        self.assertEqual(record['output_sha256'], sha(record['out']))
        self.assertTrue(Path(record['out']).is_file())
        self.assertFalse(destination.exists())
        self.assertEqual(sha(self.source), self.source_hash)
        self.assertEqual(sha(self.track), self.track_hash)
        self.cli('identify_music.py', [record['out'], '--config', self.config, '--source', self.source])

    def test_existing_destination_is_preserved_then_explicit_replace_backs_up(self):
        work, destination = self.base / 'replace-work', self.base / 'import'
        destination.mkdir()
        old = destination / '合成习惯.mp4'
        old.write_bytes(b'previous file must survive')
        old_hash = sha(old)
        args = self.render_args(work) + ['--dst', destination]
        self.cli('render.py', args)
        first = self.records(work)[0]
        self.assertEqual(sha(old), old_hash)
        self.assertIn('未覆盖', first['status'])
        self.assertTrue(Path(first['out']).is_file())
        self.cli('render.py', args + ['--replace'])
        second = self.records(work)[1]
        self.assertEqual(sha(second['backup_file']), old_hash)
        self.assertEqual(Path(second['out']), old)
        self.assertNotEqual(second['run_id'], first['run_id'])
        self.assertEqual(sha(old), second['output_sha256'])
        self.assertEqual(sha(self.source), self.source_hash)

    def test_duplicate_titles_have_independent_previews(self):
        md, work = self.base / 'duplicate.md', self.base / 'duplicate-work'
        md.write_text('| 序号 | 标题 | 话题 | 成片 |\n| --- | --- | --- | --- |\n'
                      '| 1 | same title | #日常 | [视频](<videos/01-合成习惯（无音乐）.mp4>) |\n'
                      '| 2 | same title | #日常 | [视频](<videos/01-合成习惯（无音乐）.mp4>) |\n', encoding='utf-8')
        self.cli('render.py', [md, '1-2', '--config', self.config, '--work', work, '--no-publish'])
        records = self.records(work)
        self.assertEqual(len(records), 2)
        self.assertNotEqual(records[0]['out'], records[1]['out'])
        for record in records:
            self.assertEqual(record['validation_status'], 'passed')
            self.assertTrue(Path(record['out']).is_file())

    def test_cache_save_requires_explicit_match_confirmation(self):
        result = self.cli('identify_music.py', [self.source, '--save', 'new-name', '--config', self.config], expected=2)
        self.assertIn('--confirm-match', result.stderr)
        self.assertFalse((self.music / 'new-name.wav').exists())


if __name__ == '__main__':
    unittest.main()
