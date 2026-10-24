"""Tests for dupfinder. Run from the repository root: python3 -m unittest"""

import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from collections import Counter, defaultdict
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, 'dupfinder.py')
sys.path.insert(0, ROOT)

import dupfinder as df  # noqa: E402

B = df.BLOCKSIZE
READRANGE = df.readrange


def flip(data: bytes, pos: int) -> bytes:
    """Returns data with one bit changed at pos"""
    b = bytearray(data)
    b[pos] ^= 1
    return bytes(b)


def by_inode(dups):
    """Duplicate groups as sets of inodes; which name of a hard link is kept depends on walk order"""
    return {digest: frozenset(os.lstat(f).st_ino for f in files) for digest, files in dups.items()}


class TreeCase(unittest.TestCase):
    """A test with a temporary directory tree"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name

    def write(self, name: str, data: bytes) -> str:
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        return path

    def brute_force(self, hashfunc=hashlib.sha256):
        """Full hash of every regular non-empty file, one name per inode"""
        groups = defaultdict(set)
        for dirpath, _, filenames in os.walk(self.root):
            for name in filenames:
                path = os.path.join(dirpath, name)
                st = os.lstat(path)
                if os.path.islink(path) or st.st_size == 0:
                    continue
                with open(path, 'rb') as f:
                    groups[hashfunc(f.read()).hexdigest()].add(st.st_ino)
        return {digest: frozenset(inodes) for digest, inodes in groups.items() if len(inodes) > 1}

    def cli(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, SCRIPT, '--path', self.root, *args],
                              capture_output=True, text=True, check=True)


class TestLockstep(TreeCase):

    def setUp(self):
        super().setUp()
        # a small chunk cap keeps the files small while still reaching the cap
        patcher = mock.patch.object(df, 'MAXCHUNK', 4 * B)
        patcher.start()
        self.addCleanup(patcher.stop)

    def record_reads(self):
        """Patches readrange to log every (offset, length) read per file"""
        ranges = defaultdict(list)

        def recording(filename, offset, length, stats):
            ranges[filename].append((offset, length))
            return READRANGE(filename, offset, length, stats)

        patcher = mock.patch.object(df, 'readrange', recording)
        patcher.start()
        self.addCleanup(patcher.stop)
        return ranges

    def test_matches_brute_force_and_reads_each_byte_once(self):
        rng = random.Random(1)
        bounds = [B * (2 ** k - 1) for k in range(1, 4)]  # ends of the doubling chunks
        n = 0
        for size in (1, 100, B - 1, B, B + 1, 2 * B + 1, 3 * B + 7, 20000, 200003):
            base = rng.randbytes(size)
            positions = {0, size // 2, size - 1, size - B - 1, size - B, rng.randrange(size)}
            positions |= {p for b in bounds for p in (b - 1, b)}
            variants = [base] * 3 + [flip(base, p) for p in sorted(positions) if 0 <= p < size]
            variants += [flip(base, size // 2)]  # a twin of one variant
            for data in variants:
                n += 1
                self.write(f"f{n:04d}", data)

        for name, hashfunc in df.HASHES.items():
            for jobs in (1, 4):
                with self.subTest(hash=name, jobs=jobs):
                    self.check_against_brute_force(hashfunc, jobs)

    def check_against_brute_force(self, hashfunc, jobs):
        ranges = self.record_reads()
        stats = Counter()
        dups = df.dupfinder(self.root, stats=stats, hashfunc=hashfunc, jobs=jobs)

        self.assertEqual(by_inode(dups), self.brute_force(hashfunc))
        for filename, reads in ranges.items():
            size = os.path.getsize(filename)
            end = 0
            for offset, length in sorted(reads):
                self.assertGreaterEqual(offset, end, f"{filename} read twice: {reads}")
                self.assertLessEqual(offset + length, size, filename)
                end = offset + length
        for files in dups.values():
            for filename in files:
                self.assertEqual(sum(length for _, length in ranges[filename]),
                                 os.path.getsize(filename), filename)
        self.assertEqual(stats['bytes_read'],
                         sum(length for reads in ranges.values() for _, length in reads))

    def test_parallel_jobs_give_the_same_result_and_stats(self):
        rng = random.Random(4)
        for i in range(20):
            data = rng.randbytes(1000 * (i + 1))
            self.write(f"dup{i}a", data)
            self.write(f"dup{i}b", data)
            self.write(f"near{i}", flip(data, len(data) // 2))
        results = {}
        for jobs in (1, 2, 8):
            stats = Counter()
            results[jobs] = (list(df.dupfinder(self.root, stats=stats, jobs=jobs).items()), stats)
        self.assertEqual(results[1], results[2])
        self.assertEqual(results[1], results[8])
        sizes = [os.path.getsize(files[0]) for _, files in results[1][0]]
        self.assertEqual(sizes, sorted(sizes, reverse=True), "biggest groups first")

    def test_difference_at_the_end_costs_one_block(self):
        data = random.Random(2).randbytes(200003)
        self.write('a', data)
        self.write('b', flip(data, len(data) - 1))
        stats = Counter()
        self.assertEqual(df.dupfinder(self.root, stats=stats), {})
        self.assertEqual(stats['bytes_read'], 2 * B)

    def test_keys_are_hex_digests_of_the_file(self):
        data = random.Random(3).randbytes(50000)
        self.write('a', data)
        self.write('b', data)
        for name, hashfunc in df.HASHES.items():
            with self.subTest(hash=name):
                self.assertEqual(list(df.dupfinder(self.root, hashfunc=hashfunc)),
                                 [hashfunc(data).hexdigest()])
        self.assertEqual(list(df.dupfinder(self.root)), [df.HASHES[df.DEFAULT_HASH](data).hexdigest()])


class TestWalk(TreeCase):

    def test_exclude_matches_directory_names_and_patterns(self):
        self.root = os.path.join(self.root, 'git-scan')  # scan root contains an excluded word
        for name in ('a', 'digital/b', '.git/objects/c', 'sub/node_modules/d'):
            self.write(name, b'same')
        dups = df.dupfinder(self.root, exclude=['.git', 'git', 'node_*'])
        self.assertEqual([sorted(os.path.relpath(f, self.root) for f in files) for files in dups.values()],
                         [['a', os.path.join('digital', 'b')]])

    def test_symlinks_hard_links_and_empty_files(self):
        a = self.write('a', b'data')
        os.symlink('a', os.path.join(self.root, 'symlink'))
        os.link(a, os.path.join(self.root, 'hardlink'))
        self.write('empty1', b'')
        self.write('empty2', b'')
        self.assertEqual(df.dupfinder(self.root), {})
        self.write('copy', b'data')
        self.assertEqual(len(next(iter(df.dupfinder(self.root).values()))), 2)

    @unittest.skipIf(hasattr(os, 'geteuid') and os.geteuid() == 0, "root can read anything")
    def test_unreadable_file_is_reported_and_skipped(self):
        self.write('a', b'data')
        self.write('b', b'data')
        locked = self.write('locked', b'data')
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, 0o644)
        with self.assertLogs('dupfinder', 'WARNING') as logs:
            dups = df.dupfinder(self.root)
        self.assertIn('locked', logs.output[0])
        self.assertEqual(sorted(map(os.path.basename, next(iter(dups.values())))), ['a', 'b'])

    def test_minsize(self):
        for name in ('small1', 'small2'):
            self.write(name, b'x' * 100)
        for name in ('big1', 'big2'):
            self.write(name, b'y' * 1000)
        dups = df.dupfinder(self.root, minsize=500)
        self.assertEqual([sorted(map(os.path.basename, files)) for files in dups.values()],
                         [['big1', 'big2']])


class TestCli(TreeCase):

    def setUp(self):
        super().setUp()
        self.write('a', b'data')
        self.write('b', b'data')

    def test_formats(self):
        txt = self.cli().stdout.splitlines()
        self.assertEqual([line[0] for line in txt], ['┏', '┗'])
        self.assertEqual(sorted(map(os.path.basename, *json.loads(self.cli('--format', 'json').stdout).values())),
                         ['a', 'b'])
        self.assertEqual(self.cli('--format', 'quiet').stdout, '')

    def test_stats_and_minsize(self):
        result = self.cli('--format', 'quiet', '--stats')
        self.assertIn('duplicates:       2 files in 1 groups', result.stderr)
        result = self.cli('--format', 'quiet', '--stats', '--minsize', '1K')
        self.assertIn('candidates:       0 files', result.stderr)

    def test_hash_option(self):
        keys = json.loads(self.cli('--format', 'json', '--hash', 'sha256').stdout)
        self.assertEqual(list(keys), [hashlib.sha256(b'data').hexdigest()])
        if 'blake3' in df.HASHES:
            self.assertEqual(df.DEFAULT_HASH, 'blake3')
            keys = json.loads(self.cli('--format', 'json', '--hash', 'blake3').stdout)
            self.assertEqual(list(keys), [df.HASHES['blake3'](b'data').hexdigest()])
        else:
            self.assertEqual(df.DEFAULT_HASH, 'sha256')
            result = subprocess.run([sys.executable, SCRIPT, '--path', self.root, '--hash', 'blake3'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('blake3 is not installed', result.stderr)

    def test_parsesize(self):
        for text, value in [('100', 100), ('4k', 4096), ('1.5M', 1536 * 1024), ('2GiB', 2 << 30), ('10MB', 10 << 20)]:
            self.assertEqual(df.parsesize(text), value, text)
        for bad in ('', 'M', 'ten'):
            with self.assertRaises(ValueError):
                df.parsesize(bad)


if __name__ == '__main__':
    unittest.main()
