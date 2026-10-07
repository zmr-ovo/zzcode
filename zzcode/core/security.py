"""文件工具和执行副本共用的已知凭证路径规则。"""
PRIVATE_PATH_NAMES = {'.env', '.aws', '.ssh', '.netrc', '.npmrc', '.pypirc', 'id_rsa', 'id_ed25519'}


def private_name(name):
    return name in PRIVATE_PATH_NAMES or name.startswith('.env.')
