"""写操作审批策略；Gateway 在执行前显式调用。"""
import json
import sys


def approve_operation(mode, read_only, name, arguments):
    if read_only or mode == 'never':
        return False
    if mode == 'auto':
        return True
    print(f'approve {name} {json.dumps(arguments, ensure_ascii=True)}? [y/N] ',
          file=sys.stderr, end='', flush=True)
    try:
        answer = input()
    except EOFError:
        return False
    return answer.strip().lower() in {'y', 'yes'}
