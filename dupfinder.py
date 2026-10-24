#! /usr/bin/env python3

"""
Find duplicate files in a directory

Copyright (c) 2021 Eino Tuominen

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

__author__ = "Eino Tuominen <eino@utu.fi>"
__copyright__ = "Copyright (C) 2021 Eino Tuominen"
__license__ = "MIT License"
__version__ = "1.5"

from typing import List, Dict, Tuple, Optional, Generator, Union, Callable
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import logging
import os
import stat
import sys
from fnmatch import fnmatch
from hashlib import sha256
import pathlib

# Hash functions by name. blake3 is several times faster than sha256 but
# needs the blake3 package (pip install blake3), so it is optional.
HASHES: Dict[str, Callable] = {'sha256': sha256}
try:
    from blake3 import blake3
    HASHES['blake3'] = blake3
except ImportError:
    pass
DEFAULT_HASH = 'blake3' if 'blake3' in HASHES else 'sha256'

# Size groups compared at the same time by default. More than the number of
# physical cores slows blake3 down, and parallel reads of different files
# thrash spinning disks, where --jobs 1 is better.
DEFAULT_JOBS = min(4, os.cpu_count() or 1)

# one disk block; the size of the first chunk and of the tail block
BLOCKSIZE = 4096

# chunks double in size from one block up to this
MAXCHUNK = 8 * 1024 * 1024

logger = logging.getLogger('dupfinder')

def warn(error: OSError) -> None:
    """Reports a file that could not be processed and carries on"""

    logger.warning("%s", error)

def readrange(filename: str, offset: int, length: int, stats: Counter) -> Optional[bytes]:
    """Reads a byte range of a file

    Reads unbuffered, so that no more than the requested range is read.
    A file that cannot be read, or that is shorter than expected because
    it changed during the scan, is reported on stderr.

    Args:
      filename:
        File path
      offset:
        Where to start reading
      length:
        Number of bytes to read
      stats:
        Counter for bytes read and read calls

    Returns:
      The bytes, or None if the range could not be read.
    """

    parts = []
    remaining = length
    try:
        with open(filename, 'rb', buffering=0) as file:
            file.seek(offset)
            while remaining:
                data = file.read(remaining)
                if not data:
                    break
                parts.append(data)
                remaining -= len(data)
    except OSError as e:
        warn(e)
        return None
    stats['reads'] += 1
    stats['bytes_read'] += length - remaining
    if remaining:
        logger.warning("%s: changed during scan, skipped", filename)
        return None
    return b''.join(parts)

def filewalker(path: Union[str, os.PathLike], exclude: Optional[List[str]] = None
              ) -> Generator[Tuple[str, os.stat_result], None, None]:
    """Walk through a filesystem

    A generator to walk through the filesystem under a path. Supports
    excluding directories whose name matches one of the exclusion
    patterns. Excluded directories are not descended into. Only regular
    files are yielded; symbolic links are not followed.

    Args:
      path:
        Pathname to walk through
      exclude:
        List of directory names or shell-style patterns (e.g. '.git', '*.bak')

    Yields:
      A tuple which is a pair (filename, stat result).
    """

    for dirpath, dirnames, filenames in os.walk(path, onerror=warn):
        if exclude:
            dirnames[:] = [d for d in dirnames
                           if not any(fnmatch(d, e) for e in exclude)]
        for f in filenames:
            fpath = os.path.join(dirpath, f)
            try:
                st = os.lstat(fpath)
            except OSError as e:
                warn(e)
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            yield fpath, st

def lockstep(files: List[str], size: int, stats: Counter,
             hashfunc: Callable = sha256) -> Dict[str, List[str]]:
    """Returns duplicates among files of equal size

    Reads the files in step, chunk by chunk, keeping a running hash for
    each file, and splits the group whenever the files' hashes so far
    differ. A file is dropped as soon as no other file matches it, so
    files that differ are read only up to the chunk where they differ,
    and identical files are read exactly once.

    The last block is read first, because many file formats keep an index
    at the end that differs whenever the contents differ. It is held in
    memory and fed to the hash when the sequential reads reach it, so no
    byte is read twice. Chunks start at one block and double up to
    MAXCHUNK, so most differing files are dropped cheaply while identical
    files are streamed in large reads.

    Args:
      files:
        A list of files of equal size
      size:
        Size of the files in bytes
      stats:
        Counter for bytes read and read calls
      hashfunc:
        Hash constructor, such as hashlib.sha256 or blake3.blake3. Its
        objects need update(), copy(), digest() and hexdigest().

    Returns:
      A dict containing duplicate hex digests of the whole files. Format
      {hashdigest1: [filepath1, filepath2],
       ...
      }
    """

    # Tail block
    tailstart = max(0, size - BLOCKSIZE)
    tails: Dict[str, bytes] = {}
    buckets: Dict[bytes, List[str]] = {}
    for filename in files:
        data = readrange(filename, tailstart, size - tailstart, stats)
        if data is not None:
            tails[filename] = data
            buckets.setdefault(data, []).append(filename)
    groups = [group for group in buckets.values() if len(group) > 1]

    # Sequential chunks up to the tail block
    hashes = {filename: hashfunc() for group in groups for filename in group}
    offset, chunk = 0, BLOCKSIZE
    while offset < tailstart and groups:
        length = min(chunk, tailstart - offset)
        refined = []
        for group in groups:
            buckets = {}
            for filename in group:
                data = readrange(filename, offset, length, stats)
                if data is None:
                    continue
                hashes[filename].update(data)
                buckets.setdefault(hashes[filename].copy().digest(), []).append(filename)
            refined += [g for g in buckets.values() if len(g) > 1]
        groups = refined
        offset += length
        chunk = min(2 * chunk, MAXCHUNK)

    # Feed the held tail blocks to complete the full-file hashes
    duplicates: Dict[str, List[str]] = {}
    for group in groups:
        for filename in group:
            hashes[filename].update(tails[filename])
            duplicates.setdefault(hashes[filename].hexdigest(), []).append(filename)
    logger.debug("size %d: %d files, %d duplicates", size, len(files),
                 sum(map(len, duplicates.values())))
    return duplicates

def dupfinder(path: Union[str, os.PathLike], exclude: Optional[List[str]] = None,
              stats: Optional[Counter] = None, minsize: int = 0,
              hashfunc: Optional[Callable] = None, jobs: int = 1) -> Dict[str, List[str]]:
    """Returns duplicate files

    Walks through a file path and finds duplicates by comparing hash digests of the files.
    Only files of equal size can be duplicates, so files are first grouped by size and
    files with a unique size are never read. Each size group is then read in step, see
    lockstep(). No byte of any file is read more than once.

    With jobs > 1, that many size groups are compared at the same time in threads;
    reading and hashing release the GIL, so they run in parallel. The groups with
    the most bytes go first, and are also listed first in the result.

    Empty files and files smaller than minsize are ignored, and hard links to
    the same file are counted once, as they do not take up extra space.

    Args:
      path:
        Path name to scan
      exclude:
        list of directory names or patterns to exclude
      stats:
        Optional Counter that receives the number of files seen, candidate
        files and bytes, read calls, bytes read, and duplicate files and bytes
      minsize:
        Ignore files smaller than this many bytes
      hashfunc:
        Hash constructor; defaults to HASHES[DEFAULT_HASH], i.e. blake3 when
        installed, else sha256
      jobs:
        Number of size groups to compare at the same time
    Returns:
      A dict of duplicates. The keys are hex digests of the files, matching
      sha256sum or b3sum output. Format:
      {hashdigest1: [filepath1, filepath2],
       ...
      }
    """

    if stats is None:
        stats = Counter()
    if hashfunc is None:
        hashfunc = HASHES[DEFAULT_HASH]

    sizes: Dict[int, List[str]] = {}
    inodes = set()
    for filename, st in filewalker(path, exclude=exclude):
        stats['files'] += 1
        if st.st_size < max(minsize, 1) or (st.st_dev, st.st_ino) in inodes:
            continue
        inodes.add((st.st_dev, st.st_ino))
        sizes.setdefault(st.st_size, []).append(filename)

    # Biggest groups first, so that parallel jobs finish close together
    groups = sorted(((size, files) for size, files in sizes.items() if len(files) > 1),
                    key=lambda group: group[0] * len(group[1]), reverse=True)
    logger.info("%d files seen, %d to compare in %d size groups", stats['files'],
                sum(len(files) for _, files in groups), len(groups))

    def compare(group: Tuple[int, List[str]]) -> Tuple[int, List[str], Counter, Dict[str, List[str]]]:
        # each group counts into its own Counter, so threads share no state
        size, files = group
        groupstats: Counter = Counter()
        return size, files, groupstats, lockstep(files, size, groupstats, hashfunc)

    dups: Dict[str, List[str]] = {}
    pool = ThreadPoolExecutor(max_workers=jobs) if jobs > 1 else None
    try:
        for size, files, groupstats, found in (pool.map if pool else map)(compare, groups):
            stats.update(groupstats)
            stats['candidates'] += len(files)
            stats['candidate_bytes'] += size * len(files)
            for digest, group in found.items():
                stats['duplicates'] += len(group)
                stats['duplicate_bytes'] += size * len(group)
                dups[digest] = group
    finally:
        if pool:
            # on Ctrl-C or an error, don't wait for the queued groups
            pool.shutdown(cancel_futures=True)

    return dups

def humansize(n: int) -> str:
    """Formats a byte count, e.g. 1.5 MiB"""

    if n < 1024:
        return f"{n} B"
    for unit in ('KiB', 'MiB', 'GiB', 'TiB'):
        n /= 1024
        if n < 1024 or unit == 'TiB':
            return f"{n:.1f} {unit}"

def parsesize(text: str) -> int:
    """Parses a byte count with an optional K, M, G or T suffix, e.g. 10M"""

    units = {'': 1, 'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3, 'T': 1024 ** 4}
    number = text.strip().upper().removesuffix('IB').removesuffix('B')
    unit = number[-1:] if number[-1:] in units else ''
    try:
        return int(float(number[:len(number) - len(unit)]) * units[unit])
    except ValueError:
        raise ValueError(f"invalid size: {text}") from None

if __name__ == '__main__':
    import json
    import argparse

    def size(text: str) -> int:
        try:
            return parsesize(text)
        except ValueError as e:
            raise argparse.ArgumentTypeError(str(e))

    parser = argparse.ArgumentParser(description='Find duplicate files within a path')
    parser.add_argument('--path', dest='path', type=pathlib.Path,
                        action='store', help="Process path", default='.'
                       )
    parser.add_argument('--exclude', dest='exclude', action='store', nargs='*',
                        help="Exclude directories with matching names (wildcards allowed)",
                        default=[]
                       )
    parser.add_argument('--format', dest='format', choices=['txt', 'json', 'quiet'], action='store',
                        help="Output format. Text is default, quiet prints nothing", default='txt'
                       )
    parser.add_argument('--minsize', dest='minsize', type=size, action='store',
                        help="Ignore files smaller than this, e.g. 4096, 100K or 1.5M", default=0
                       )
    parser.add_argument('--jobs', dest='jobs', type=int, action='store', default=DEFAULT_JOBS,
                        help="Size groups to compare in parallel. Default min(4, CPUs); "
                             "use 1 for spinning disks and network shares"
                       )
    parser.add_argument('--hash', dest='hash', choices=['blake3', 'sha256'], action='store',
                        help="Hash function. Default blake3 if installed, else sha256",
                        default=DEFAULT_HASH
                       )
    parser.add_argument('--stats', dest='stats', action='store_true',
                        help="Print read statistics to stderr"
                       )
    parser.add_argument('--verbose', dest='verbose', action='store_true',
                        help="Verbose logging"
                       )
    parser.add_argument('--debug', dest='debug', action='store_true',
                        help="Debug level logging"
                       )
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    if args.hash not in HASHES:
        parser.error(f"{args.hash} is not installed; pip install {args.hash}")

    level = logging.DEBUG if args.debug else logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(format='dupfinder: %(message)s', level=level)

    stats: Counter = Counter()
    dups = dupfinder(args.path, args.exclude, stats, minsize=args.minsize,
                     hashfunc=HASHES[args.hash], jobs=args.jobs)

    if args.format == 'json':
        print(json.dumps(dups, indent=4))
    elif args.format == 'txt':
        for files in dups.values():
            print('┏', files[0])
            for file in files[1:-1]:
                print('┃', file)
            print('┗', files[-1])

    if args.stats:
        share = stats['bytes_read'] / stats['candidate_bytes'] if stats['candidate_bytes'] else 0
        print(f"files seen:       {stats['files']}\n"
              f"candidates:       {stats['candidates']} files, "
              f"{humansize(stats['candidate_bytes'])} (same size as another file)\n"
              f"duplicates:       {stats['duplicates']} files in {len(dups)} groups, "
              f"{humansize(stats['duplicate_bytes'])}\n"
              f"bytes read:       {humansize(stats['bytes_read'])} "
              f"({share:.1%} of candidate bytes) in {stats['reads']} reads",
              file=sys.stderr)
