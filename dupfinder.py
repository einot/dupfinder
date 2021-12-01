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
from hashlib import sha256
from xxhash import xxh3_64
from blake3 import blake3
import sys
import pathlib
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
                            x = hashfunc(d).digest()
                        else:
                            logger.debug(f"starting full scan of {filename}")
                            xx = hashfunc()
                            for i, byte_block in enumerate(iter(lambda: file.read(16*1024), b"")):
                                xx.update(byte_block)
                                logger.debug(f"updating {filename} hash with block {i}")
                            x = xx.digest()
                            logger.debug(f"finished scanning {filename}")
                elif prewash:
                    sr = os.stat(filename)
                    if not stat.S_ISREG(sr.st_mode):    
                        continue
                    x = sr.st_size
                else:
                    raise ValueError('Contradicting scansize and prewash parameters')
            except FileNotFoundError as e:
                logger.info(f"File {filename} disappeared")
            output_q.put((x, filename))


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
    
def filewalker(path: str, exclude: Optional[List[str]] = None) -> Generator[str, None, None]:
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

def dupfinder(path: str, exclude: Optional[List[str]] = []) -> Dict[str, List[str]]:
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
    files = filewalker(path, exclude=exclude)
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
    parser.add_argument('--format', dest='format', choices=['txt', 'json'], action='store',
                        help="Output format. Text is default", default='txt'
                       ) 
    parser.add_argument('--verbose', dest='verbose', action='store_true',
                        help="Verbose logging",
                       ) 
    parser.add_argument('--debug', dest='debug', action='store_true',
                        help="Debug level logging"
                       ) 

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

    dups = dupfinder(args.path, args.exclude)

    if args.format == 'json':
        print(json.dumps(dups, indent=4))
    else:
        for hash, list in dups.items():
            print('┏', list[0])
            for file in list[1:len(list)-1]:
               print('┃', file)
            print('┗', list[-1])
