"""Execute one child, recording wall, waited-tree CPU and max RSS with wait4."""
import json
import os
import sys
import time

result_path = sys.argv[1]
command = sys.argv[2:]
started = time.perf_counter()
pid = os.fork()
if pid == 0:
    os.execvp(command[0], command)
_, status, usage = os.wait4(pid, 0)
result = {'wall_seconds': time.perf_counter() - started,
          'cpu_user_seconds': usage.ru_utime, 'cpu_system_seconds': usage.ru_stime,
          'max_rss_kib': usage.ru_maxrss, 'exit_code': os.waitstatus_to_exitcode(status)}
with open(result_path, 'w', encoding='utf-8') as stream:
    json.dump(result, stream)
os._exit(result['exit_code'] if result['exit_code'] >= 0 else 128 - result['exit_code'])
