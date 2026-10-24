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
__version__ = "1.1"

from typing import List, Dict, Tuple, Optional, Generator, Union

import os
import stat
import sys
from fnmatch import fnmatch
from hashlib import sha256
import pathlib

# one disk block
SCANSIZE = 4096

# read buffer for hashing whole files
CHUNKSIZE = 1024 * 1024

def warn(error: OSError) -> None:
    """Reports a file that could not be processed and carries on"""

    print(f"dupfinder: {error}", file=sys.stderr)

def hasher(*files, scansize, hashfunc) -> Generator[Tuple[str, str], None, None]:
    """Calculates hashes for files

    A generator function to calculate hashes for a list of files.
    Files that cannot be read are reported on stderr and skipped.

    Args:
      files:
        List of file paths
      scansize:
        Read scansize bytes from the beginning of the file.
        If scansize = 0, process the whole file.
      hashfunc:
        Hash function to use for calculation

    Yields:
      A tuple which is a pair (hash, filename).
    """

    for filename in files:
        try:
            with open(filename, 'rb') as file:
                if scansize:
                    d = file.read(scansize)
                    x = hashfunc(d).hexdigest()
                else:
                    xx = hashfunc()
                    for byte_block in iter(lambda: file.read(CHUNKSIZE), b""):
                        xx.update(byte_block)
                    x = xx.hexdigest()
        except OSError as e:
            warn(e)
            continue
        yield x, filename

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

def duplicates(*files, scansize, hashfunc) -> Dict[str, List[str]]:
    """Returns a duplicate files

    Returns a dict that contains filenames that have identical
    hash digests.

    Args:
      files:
        A list of files to compare
      scansize:
        Read scansize bytes from the beginning of the file.
        If scansize = 0, process the whole file.
      hashfunc:
        Hash function to use for calculation

    Returns:
      A dict containing duplicate hash digests. Format
      {hashdigest1: [filepath1, filepath2],
       ...
      }
    """

    candidates: Dict[str, str] = {}
    duplicates: Dict[str, List[str]] = {}

    for digest, filename in hasher(*files, scansize=scansize, hashfunc=hashfunc):
        if digest in duplicates:
            duplicates[digest].append(filename)
        elif digest in candidates:
            duplicates[digest] = [candidates[digest], filename]
        else:
            candidates[digest] = filename
    return duplicates

def dupfinder(path: Union[str, os.PathLike], exclude: Optional[List[str]] = None
             ) -> Dict[str, List[str]]:
    """Returns duplicate files

    Walks through a file path and finds duplicates by comparing hash digests of the files.
    Only files of equal size can be duplicates, so files are first grouped by size and
    files with a unique size are never read. Within a size group, the first pass scans
    a little from the beginning of the file, the second pass scans a bigger part of the
    file for the colliding files, and the last pass calculates full sha256 sums. A pass
    that would read the whole file anyway is skipped. Returns found duplicates.

    Empty files are ignored, and hard links to the same file are counted once,
    as they do not take up extra space.

    Args:
      path:
        Path name to scan
      exclude:
        list of directory names or patterns to exclude
    Returns:
      A dict of duplicates. Format:
      {hashdigest1: [filepath1, filepath2],
       ...
      }
    """

    sizes: Dict[int, List[str]] = {}
    inodes = set()
    for filename, st in filewalker(path, exclude=exclude):
        if st.st_size == 0 or (st.st_dev, st.st_ino) in inodes:
            continue
        inodes.add((st.st_dev, st.st_ino))
        sizes.setdefault(st.st_size, []).append(filename)

    dups: Dict[str, List[str]] = {}
    for size, files in sizes.items():
        if len(files) < 2:
            continue

        # Partial passes, refining the groups of colliding files
        groups = [files]
        for scansize in (SCANSIZE, 100*SCANSIZE):
            if scansize >= size:
                break
            groups = [refined for group in groups
                      for refined in duplicates(*group, scansize=scansize,
                                                hashfunc=sha256).values()]

        # Full pass
        for group in groups:
            dups.update(duplicates(*group, scansize=0, hashfunc=sha256))

    return dups

if __name__ == '__main__':
    import json
    import argparse

    parser = argparse.ArgumentParser(description='Find duplicate files within a path')
    parser.add_argument('--path', dest='path', type=pathlib.Path,
                        action='store', help="Process path", default='.'
                       )
    parser.add_argument('--exclude', dest='exclude', action='store', nargs='*',
                        help="Exclude directories with matching names (wildcards allowed)",
                        default=[]
                       )
    parser.add_argument('--format', dest='format', choices=['txt', 'json'], action='store',
                        help="Output format. Text is default", default='txt'
                       )
    args = parser.parse_args()

    dups = dupfinder(args.path, args.exclude)

    if args.format == 'json':
        print(json.dumps(dups, indent=4))
    else:
        for files in dups.values():
            print('┏', files[0])
            for file in files[1:-1]:
                print('┃', file)
            print('┗', files[-1])
