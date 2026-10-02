#!/usr/bin/env python3
"""List cultivar definitions and resource links in an APSIMX JSON document."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def walk(node, path=''):
    if isinstance(node, dict):
        name = node.get('Name') or node.get('$type', '').split(',')[0].split('.')[-1]
        current = f'{path}/{name}' if name else path
        yield current, node
        for value in node.values():
            yield from walk(value, current)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apsimx', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    data = json.loads(args.apsimx.read_text(encoding='utf-8-sig'))
    records = []
    for path, node in walk(data):
        node_type = str(node.get('$type') or '')
        commands = node.get('Command') or node.get('Commands')
        resources = {key: value for key, value in node.items()
                     if 'resource' in key.casefold() and value not in (None, '', [], {})}
        if ('Cultivar' in node_type or commands or resources or
                'cultivar' in str(node.get('Name') or '').casefold()):
            records.append({
                'path': path,
                'name': node.get('Name'),
                'type': node_type,
                'commands': commands,
                'resources': resources,
            })
    text = json.dumps(records, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n', encoding='utf-8')
    else:
        print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
