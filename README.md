# dupfinder
Find duplicate files under a directory path

    $ ./dupfinder --help
    usage: dupfinder [-h] [--path PATH] [--exclude [EXCLUDE ...]]
                     [--format {txt,json,quiet}] [--minsize MINSIZE]
                     [--jobs JOBS] [--hash {blake3,sha256}] [--stats]
                     [--verbose] [--debug]
    
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
      --stats               Print read statistics to stderr
      --verbose             Verbose logging
      --debug               Debug level logging

Needs Python 3.9 or newer and nothing else. For faster hashing, install
[blake3](https://pypi.org/project/blake3/); it is then used by default:

    $ pip install blake3

## Testing

    $ python3 -m unittest
