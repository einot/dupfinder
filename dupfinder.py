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

from typing import List, Set, Dict, Tuple, Optional, Generator

import os
from hashlib import sha256
import pathlib

# one disk block
SCANSIZE = 4096

def hasher(*files, scansize, hashfunc) -> Generator[Tuple[str, str], None, None]:
    """Calculates hashes for files

    A generator function to calculate hashes for a list of files.

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
        with open(filename, 'rb') as file:
            if scansize:
                d = file.read(scansize)
                x = hashfunc(d).hexdigest()
            else:
                xx = hashfunc()
                for byte_block in iter(lambda: file.read(4096),b""):
                    xx.update(byte_block)
                x = xx.hexdigest()
        yield x, filename

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
      Filenames as string
    """

    for dirpath, dirnames, filenames in os.walk(path):
        if exclude and any(e in dirpath for e in exclude):
            continue
        for f in filenames:
            fpath = os.path.join(dirpath, f)
            if not os.path.isfile(fpath):
                continue
            yield fpath

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
    
    for hash, filename in hasher(*files, scansize=scansize, hashfunc=hashfunc):
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

    # First pass
    files = filewalker(path, exclude=exclude)
    dups = duplicates(*files, scansize=SCANSIZE, hashfunc=sha256)

    # Second pass
    files = (file for files in dups.values() for file in files)
    dups = duplicates(*files, scansize=100*SCANSIZE, hashfunc=sha256)

    # Third pass
    files = (file for files in dups.values() for file in files)
    dups = duplicates(*files, scansize=0, hashfunc=sha256)

    return dups

if __name__ == '__main__':
    import json
    import argparse

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
    args = parser.parse_args()

    dups = dupfinder(args.path, args.exclude)

    if args.format == 'json':
        print(json.dumps(dups, indent=4))
    else:
        for hash, list in dups.items():
            print('┏', list[0])
            for file in list[1:len(list)-1]:
               print('┃', file)
            print('┗', list[-1])
