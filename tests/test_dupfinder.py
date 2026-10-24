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


class TestDelete(TreeCase):

    def scan(self):
        snapshots = {}
        return df.dupfinder(self.root, snapshots=snapshots), snapshots

    def remaining(self):
        return sorted(os.path.relpath(os.path.join(d, f), self.root)
                      for d, _, files in os.walk(self.root) for f in files)

    def test_rmfrom_keeps_copies_elsewhere_and_dry_run_deletes_nothing(self):
        self.write('keep/a', b'one')
        self.write('trash/a', b'one')
        self.write('trash/b', b'two')  # no copy anywhere, so not a duplicate
        dups, snapshots = self.scan()
        plan = list(df.deletions(dups, df.under(os.path.join(self.root, 'trash'))))
        self.assertEqual(df.rmduplicates(plan, snapshots), plan)
        self.assertEqual(self.remaining(), ['keep/a', 'trash/a', 'trash/b'])
        stats = Counter()
        df.rmduplicates(plan, snapshots, delete=True, stats=stats)
        self.assertEqual(self.remaining(), ['keep/a', 'trash/b'])
        self.assertEqual((stats['deleted'], stats['deleted_bytes']), (1, 3))

    def test_one_copy_is_kept_when_all_are_selected(self):
        for name in ('x/deep/c', 'x/b', 'x/a'):
            self.write(name, b'same')
        dups, snapshots = self.scan()
        df.rmduplicates(df.deletions(dups, df.under(self.root)), snapshots, delete=True)
        self.assertEqual(self.remaining(), ['x/a'], "the shallowest, then first in sort order")

    def test_combined_tests_never_remove_every_copy(self):
        # the local version ran --rmfrom and --rmre one after the other on the same
        # duplicate list, so together they could delete both copies
        self.write('trash/a', b'same')
        self.write('keep/a.bak', b'same')
        dups, snapshots = self.scan()
        tests = [df.under(os.path.join(self.root, 'trash')), df.matching(r'\.bak$')]
        plan = df.deletions(dups, lambda f: any(t(f) for t in tests))
        self.assertEqual(len(df.rmduplicates(plan, snapshots, delete=True)), 1)
        self.assertEqual(len(self.remaining()), 1)

    def test_rmre_matches_file_names_only(self):
        self.write('photos.bak/a', b'same')
        self.write('photos/a copy', b'same')
        dups, snapshots = self.scan()
        plan = list(df.deletions(dups, df.matching(r'\.bak|copy$')))
        self.assertEqual([os.path.relpath(f, self.root) for f, _ in plan], ['photos/a copy'])

    def test_changed_or_missing_files_are_not_deleted(self):
        for change in ('modify doomed', 'modify keeper', 'remove keeper', 'replace doomed'):
            with self.subTest(change):
                doomed = self.write('trash/a', b'same')
                keeper = self.write('keep/a', b'same')
                dups, snapshots = self.scan()
                plan = list(df.deletions(dups, df.under(os.path.join(self.root, 'trash'))))
                if change == 'modify doomed':
                    self.write('trash/a', b'SAME')
                elif change == 'modify keeper':
                    st = os.stat(keeper)
                    os.utime(keeper, ns=(st.st_atime_ns, st.st_mtime_ns + 1))
                elif change == 'remove keeper':
                    os.unlink(keeper)
                else:
                    os.unlink(doomed)
                    self.write('trash/a', b'same')  # same content, but a different file now
                stats = Counter()
                with self.assertLogs('dupfinder', 'WARNING'):
                    self.assertEqual(df.rmduplicates(plan, snapshots, delete=True, stats=stats), [])
                self.assertTrue(os.path.exists(doomed))
                self.assertEqual(stats['skipped'], 1)
                for f in (doomed, keeper):
                    if os.path.exists(f):
                        os.unlink(f)


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

    def test_delete_options(self):
        trash = os.path.join(self.root, 'trash')
        self.write('trash/b', b'data')
        result = self.cli('--format', 'quiet', '--rmfrom', trash)
        self.assertIn('would delete', result.stdout)
        self.assertIn('add --delete', result.stderr)
        self.assertTrue(os.path.exists(os.path.join(trash, 'b')))
        result = self.cli('--format', 'json', '--rmfrom', trash, '--delete', '--stats')
        json.loads(result.stdout)  # report went to stderr, JSON stays valid
        self.assertIn('deleted:          1 files, 4 B; 0 skipped', result.stderr)
        self.assertFalse(os.path.exists(os.path.join(trash, 'b')))
        self.assertTrue(os.path.exists(os.path.join(self.root, 'a')))
        for args, message in [(['--delete'], '--delete needs'), (['--rmre', '('], '--rmre:')]:
            result = subprocess.run([sys.executable, SCRIPT, '--path', self.root, *args],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn(message, result.stderr)

    def test_parsesize(self):
        for text, value in [('100', 100), ('4k', 4096), ('1.5M', 1536 * 1024), ('2GiB', 2 << 30), ('10MB', 10 << 20)]:
            self.assertEqual(df.parsesize(text), value, text)
        for bad in ('', 'M', 'ten'):
            with self.assertRaises(ValueError):
                df.parsesize(bad)


if __name__ == '__main__':
    unittest.main()
