"""工具与评分容器共用的基础隔离配置。"""
import re

IMAGE_ID = re.compile(r'^sha256:[0-9a-f]{64}$')
SECRET_ENV = re.compile(r'(?:API.?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH)', re.I)
DOCKER_ENV = {'HOME': '/tmp/home', 'PYTHONDONTWRITEBYTECODE': '1',
              'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTHONPATH': '/workspace'}


def isolation_arguments(name, limits, *, user, cwd='/workspace', env=None):
    values = DOCKER_ENV | dict(env or {})
    args = ['create', '--name', name, '--network', 'none', '--init', '--cpus', str(limits.cpus),
            '--memory', f'{limits.memory_mb}m', '--memory-swap', f'{limits.memory_mb}m',
            '--pids-limit', str(limits.pids_limit), '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--user', user,
            '--tmpfs', f'/tmp:rw,noexec,nosuid,nodev,size={limits.tmpfs_mb}m,mode=1777', '--workdir', cwd]
    for key, value in values.items():
        args += ['--env', f'{key}={value}']
    return args


def isolation_violations(inspection, *, limits, mounts):
    host, config = inspection.get('HostConfig', {}), inspection.get('Config', {})
    checks = {'network=none': host.get('NetworkMode') == 'none',
              'read-only rootfs': host.get('ReadonlyRootfs') is True,
              'memory limit': host.get('Memory') == limits.memory_mb * 1024 * 1024,
              'memory swap disabled': host.get('MemorySwap') == limits.memory_mb * 1024 * 1024,
              'CPU limit': host.get('NanoCpus') == int(limits.cpus * 1_000_000_000),
              'PID limit': host.get('PidsLimit') == limits.pids_limit,
              'all capabilities dropped': 'ALL' in (host.get('CapDrop') or []),
              'no-new-privileges': 'no-new-privileges:true' in (host.get('SecurityOpt') or []),
              'non-root user': str(config.get('User') or '').partition(':')[0] not in {'', '0', 'root'},
              'limited /tmp tmpfs': '/tmp' in (host.get('Tmpfs') or {})}
    observed = {row.get('Destination'): row for row in inspection.get('Mounts', [])}
    for target, (source, writable) in mounts.items():
        row = observed.get(target, {})
        checks[f'mount {target}'] = row.get('Source') == str(source) and row.get('RW') is writable
    checks['no unexpected bind mounts'] = all(row.get('Type') not in {'bind', 'volume'} or row.get('Destination') in mounts for row in observed.values())
    return [name for name, passed in checks.items() if not passed]
