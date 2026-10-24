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
__version__ = "1.0"

from typing import List, Set, Dict, Tuple, Optional, Generator, Callable

import os
import re
from hashlib import sha256
from xxhash import xxh3_64
from blake3 import blake3
import sys
import pathlib
from pathlib import Path
import stat

import multiprocessing as mp
from multiprocessing.pool import ThreadPool
from multiprocessing.queues import Queue

# one disk block
SCANSIZE = 409600

def hasher(*files: str, scansize: int, hashfunc: Callable, prewash: bool = False) -> Generator[Tuple[str, str], None, None]:
    """Calculates hashes for files

    A generator function to calculate hashes for a list of files.

    Args:
      files:
        List of file paths
      scansize:
        Read scansize bytes from the beginning of the file.
        use sys.maxsize for full file
      hashfunc:
        Hash function to use for calculation

    Yields:
      A tuple which is a pair (hash, filename).
    """

    threadcount = mp.cpu_count()

    def queuer(input_q, files):
        for filename in files:
            input_q.put(filename)
        for _ in range(threadcount):    # Once generator is consumed, send end-signal
            input_q.put(None)

    # threadpool worker
    def worker(input_q, output_q):
        while True:
            filename = input_q.get()
            if filename is None:
                output_q.put(None)
                break
            try:
                if scansize:
                    with open(filename, 'rb') as file:
                        if scansize < 10 * 1024 ** 2: # 1 megabyte
                            logger.debug(f"fast scanning {scansize} bytes of {filename}")
                            d = file.read(scansize)
                            hashvalue = hashfunc(d).hexdigest()
                        else:
                            logger.debug(f"starting full scan of {filename}")
                            xx = hashfunc()
                            for i, byte_block in enumerate(iter(lambda: file.read(16*1024), b"")):
                                xx.update(byte_block)
                                logger.debug(f"updating {filename} hash with block {i}")
                            hashvalue = xx.hexdigest()
                            logger.debug(f"finished scanning {filename}")
                elif prewash:
                    sr = os.stat(filename)
                    if not stat.S_ISREG(sr.st_mode):    
                        continue
                    hashvalue = sr.st_size
                else:
                    raise ValueError('Contradicting scansize and prewash parameters')
            except FileNotFoundError as e:
                logger.info(f"File {filename} disappeared")
            output_q.put((hashvalue, filename))


    input_q: Queue = mp.Queue(maxsize=threadcount)
    output_q: Queue = mp.Queue(maxsize=threadcount)

    genpool = ThreadPool(1, initializer=queuer, initargs=(input_q, files))
    tpool = ThreadPool(threadcount, initializer=worker, initargs=(input_q, output_q))

    workers = threadcount
    while workers:
        result = output_q.get()
        if result is None:
            workers -= 1
        else:
            yield result
    
def filewalker(path: str, exclude: Optional[List[str]] = None, minsize: int = 0) -> Generator[str, None, None]:
    """Walk through a filesystem

    A generator to walk through the filesystem under a path. Supports
    excluding directories matching the exclusion strings.

    Args:
      path:
        Pathname to walk through
      exclude:
        List of exclusion strings

    Yields:
      Filenames
    """

    for dirpath, dirnames, filenames in os.walk(path, topdown=False):
        if exclude and any(e in dirpath for e in exclude):
            continue
        for f in filenames:
            fpath = os.path.join(dirpath, f)
            if os.path.getsize(fpath) >= minsize:
                yield fpath

def duplicates(*files: str, scansize: int, hashfunc: Callable, prewash: bool = False) -> Dict[str, List[str]]:
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
    
    for hash, filename in hasher(*files, scansize=scansize, hashfunc=hashfunc, prewash=prewash):
        if hash in duplicates:
            duplicates[hash].append(filename)
        elif hash in candidates: 
            duplicates[hash] = [candidates[hash], filename]
        else:
            candidates[hash] = filename
    return duplicates

def dupfinder(path: str, exclude: Optional[List[str]] = [], minsize: int = 0) -> Dict[str, List[str]]:
    """Returns duplicate files

    Walks through a file path and finds duplicates by comparing hash digests of the files.
    Do three loops. On first pass it scans a little from the beginning of the file, on the 
    second pass scan a bigger part of the file for the colliding files. On the third pass 
    it calculates full sha256 sums for the files. Returns found duplicates.

    Args:
      path:
        Path name to scan
      exclude: 
        list of exclusion strings
    Returns:
      A dict of duplicates. Format:
      {hashdigest1: [filepath1, filepath2],
       ...
      }
    """ 

    # First pass, dedup based on filenames
    # prewash also filters out other than regular files
    noop = lambda *x : None
    files = filewalker(path, exclude=exclude, minsize=minsize)
    dups = duplicates(*files, scansize=0, prewash=True, hashfunc=noop)

    # Second pass, short hashes
    logger.info(f"commencing fast scan of {len(dups.values())} files")
    files = (file for files in dups.values() for file in files)
    dups = duplicates(*files, scansize=SCANSIZE, hashfunc=blake3)

    # Final pass, full files
    logger.info(f"commencing full scan of {len(dups.values())} files")
    files = (file for files in dups.values() for file in files)
    dups = duplicates(*files, scansize=sys.maxsize, hashfunc=blake3)

    return dups

def rmduplicatesfrom(path: str, dups: Dict[str, List[str]]):
    rmfrom_path = Path(path).resolve()

    for hash, filelist in dups.items():
        candidates_for_deletion = set()
        other_copies = set()

        for filename in filelist:
            file_path = Path(filename).resolve()
            if rmfrom_path in file_path.parents:
                candidates_for_deletion.add(file_path)
            else:
                other_copies.add(file_path)
            
        if candidates_for_deletion:
            if not other_copies:
                candidates_for_deletion.pop()
            for file in candidates_for_deletion:
                logger.info(f"deleting {file}")
                file.unlink()

def rmduplicatesre(expression: str, dups: Dict[str, List[str]]):
    pattern = re.compile(expression)
    
    for hash, filelist in dups.items():
        candidates_for_deletion = set()
        other_copies = set()

        for filename in filelist:
            file_path = Path(filename).resolve()
            if pattern.search(file_path.name):
                candidates_for_deletion.add(file_path)
            else:
                other_copies.add(file_path)
            
        if candidates_for_deletion:
            if not other_copies:
                candidates_for_deletion.pop()
            for file in candidates_for_deletion:
                logger.info(f"deleting {file}")
                file.unlink()

if __name__ == '__main__':
    import json
    import argparse
    import logging

    parser = argparse.ArgumentParser(description='Find duplicate files within a path')
    parser.add_argument('--path', dest='path', type=pathlib.Path,
                        action='store', help="Process path", default='.'
                       )
    parser.add_argument('--exclude', dest='exclude', action='store', nargs='*',
                        help="Exclude matching directories", default=[]
                       )
    parser.add_argument('--rmfrom', dest='rmfrom', type=pathlib.Path, action='store',
                        help="Remove duplicates under path", default=None
                       )
    parser.add_argument('--rmre', dest='rmre', type=str, action='store',
                        help="Remove duplicates matching the regular expression", default=None
                       )
    parser.add_argument('--format', dest='format', choices=['txt', 'json', 'quiet'], action='store',
                        help="Output format. Text is default", default='txt'
                       ) 
    parser.add_argument('--verbose', dest='verbose', action='store_true',
                        help="Verbose logging",
                       ) 
    parser.add_argument('--debug', dest='debug', action='store_true',
                        help="Debug level logging"
                       )
    parser.add_argument('--minsize', dest='minsize', type=int, action='store',
                        help="Minimum file size to consider", default=0)

    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger()

    if args.debug:
        logger.setLevel(logging.DEBUG)
    elif args.verbose:
        logger.setLevel(logging.INFO)
        logger.debug('jeejee')
    else:
        logger.setLevel(logging.ERROR)

    dups = dupfinder(args.path, args.exclude, minsize=args.minsize)

    if args.format == 'json':
        print(json.dumps(dups, indent=4))
    elif args.format == 'quiet':
        pass
    else:
        for hash, filelist in dups.items():
            print('┏', filelist[0])
            for file in filelist[1:len(filelist)-1]:
               print('┃', file)
            print('┗', filelist[-1])

    if args.rmfrom:
        rmduplicatesfrom(args.rmfrom, dups)

    if args.rmre:
        rmduplicatesre(args.rmre, dups)
