#!/usr/bin/env python3
"""Dependency-free PDF page count (no pypdf/poppler needed) for the 2-page render gate.
Counts `/Type /Page` objects, falling back to the Pages-tree `/Count`. Prints the integer count."""
import re
import sys


def page_count(path):
    data = open(path, "rb").read()
    # One `/Type /Page` per page; the negative lookahead excludes the `/Type /Pages` tree node.
    pages = len(re.findall(rb"/Type\s*/Page(?![s])", data))
    if pages:
        return pages
    counts = re.findall(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", data)
    return max((int(x) for x in counts), default=0)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: pagecount.py <file.pdf>")
    print(page_count(sys.argv[1]))
