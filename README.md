# dupfinder
Find duplicate files under a directory path

    $ ./dupfinder --help
    usage: dupfinder [-h] [--path PATH] [--exclude [EXCLUDE ...]]
                     [--format {txt,json,quiet}] [--minsize MINSIZE] [--stats]
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
      --stats               Print read statistics to stderr
      --verbose             Verbose logging
      --debug               Debug level logging

## Testing

    $ python3 -m unittest
