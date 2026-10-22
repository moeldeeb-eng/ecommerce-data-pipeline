"""Prepare local Airflow directories/secrets; preserve existing project settings."""
import base64
import json
import os
import secrets
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def prepare(root=ROOT):
    if not (root/'compose.yaml').is_file() or not (root/'.env').is_file():
        raise FileNotFoundError('Run this inside the existing ecommerce project with compose.yaml and .env')
    for name in ['airflow/dags','airflow/logs','airflow/config']:
        (root/name).mkdir(parents=True,exist_ok=True)
        (root/name).chmod(0o775)
    path=root/'airflow/local.env'
    names={'AF_METADATA_PASSWORD','AF_JWT_SECRET','AF_FERNET_KEY','AF_API_SECRET_KEY','AF_UID'}
    if path.exists():
        existing=dict(line.split('=',1) for line in path.read_text().splitlines() if line and not line.startswith('#') and '=' in line)
        if not names<=existing.keys() or any(not existing[k] for k in names):
            raise ValueError('Existing airflow/local.env is incomplete; refusing to replace existing secrets')
        print('Existing Airflow service secrets preserved.')
    else:
        uid=os.getuid() if hasattr(os,'getuid') else 50000
        values=dict(AF_UID=str(uid if uid>0 else 50000),AF_METADATA_PASSWORD=secrets.token_hex(32),AF_JWT_SECRET=secrets.token_hex(48),
                    AF_FERNET_KEY=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),AF_API_SECRET_KEY=secrets.token_hex(32))
        with path.open('x',encoding='utf-8',newline='\n') as f:
            f.write('# Generated local development credentials. Keep out of Git.\n')
            for key,value in values.items():f.write(f'{key}={value}\n')
        path.chmod(0o600)
        print('Airflow service secrets created.')
    password_path=root/'airflow/config/simple_auth_passwords.json'
    if password_path.exists():
        value=json.loads(password_path.read_text(encoding='utf-8'))
        if not isinstance(value,dict) or not isinstance(value.get('admin'),str) or not value['admin']:
            raise ValueError('Existing admin password file is invalid; refusing overwrite')
    else:
        with password_path.open('x',encoding='utf-8') as f:json.dump({'admin':secrets.token_urlsafe(18)},f,indent=2)
        # SimpleAuthManager opens its password file with write access during startup.
        password_path.chmod(0o660)
    ignore=root/'.gitignore'
    text=ignore.read_text(encoding='utf-8') if ignore.exists() else ''
    lines=['airflow/local.env','airflow/config/','airflow/logs/']
    missing=[line for line in lines if line not in text.splitlines()]
    if missing:
        with ignore.open('a',encoding='utf-8',newline='\n') as f:
            f.write(('\n' if text and not text.endswith('\n') else '')+'\n# Local Airflow state\n'+'\n'.join(missing)+'\n')
    print('Setup ready. Ecommerce .env and compose.yaml were not changed.')
    print('UI username: admin')
    print('UI password is in airflow/config/simple_auth_passwords.json (not printed).')

if __name__=='__main__':prepare()
