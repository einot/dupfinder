# dupfinder
Find duplicate files under a directory path

    $ ./dupfinder --help
    usage: dupfinder [-h] [--path PATH] [--exclude [EXCLUDE ...]]
                     [--format {txt,json,quiet}] [--minsize MINSIZE]
                     [--jobs JOBS] [--hash {blake3,sha256}] [--rmfrom RMFROM]
                     [--rmre RMRE] [--delete] [--stats] [--verbose] [--debug]
    
    Find duplicate files within a path
    
    options:
      -h, --help            show this help message and exit
      --path PATH           Process path
      --exclude [EXCLUDE ...]
                            Exclude directories with matching names (wildcards
                            allowed)
      --format {txt,json,quiet}
                            Output format. Text is default, quiet prints nothing
      --minsize MINSIZE     Ignore files smaller than this, e.g. 4096, 100K or
                            1.5M
      --jobs JOBS           Size groups to compare in parallel. Default min(4,
                            CPUs); use 1 for spinning disks and network shares
      --hash {blake3,sha256}
                            Hash function. Default blake3 if installed, else
                            sha256
      --rmfrom RMFROM       Select duplicates under this path for deletion
      --rmre RMRE           Select duplicates whose file name matches this regular
                            expression for deletion
      --delete              Really delete the selected duplicates. Without it,
                            only list them. One copy of each file is always kept
      --stats               Print read statistics to stderr
      --verbose             Verbose logging
      --debug               Debug level logging

Needs Python 3.9 or newer and nothing else. For faster hashing, install
[blake3](https://pypi.org/project/blake3/); it is then used by default:

    $ pip install blake3

## Deleting duplicates

`--rmfrom PATH` selects the duplicates under PATH, and `--rmre REGEX` those whose
file name matches. Without `--delete`, dupfinder only lists what it would delete:

    $ ./dupfinder --path ~/Pictures --format quiet --rmfrom ~/Pictures/backup
    would delete /Users/me/Pictures/backup/a.jpg (kept /Users/me/Pictures/a.jpg)
    $ ./dupfinder --path ~/Pictures --format quiet --rmfrom ~/Pictures/backup --delete

One copy of every file is always kept, even when all copies are selected; then it
keeps the one nearest the root, and of those the first in sort order. Just before
deleting, dupfinder checks that neither the file nor the kept copy has changed
since the scan, and skips the file if either has.

## Testing

    $ python3 -m unittest
