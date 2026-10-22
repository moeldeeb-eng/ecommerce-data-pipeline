"""Compose wrapper so Windows users do not repeat overlay/environment arguments."""
import argparse
import shutil
import subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
COMPONENTS=['airflow-api-server','airflow-scheduler','airflow-dag-processor']
ACTIONS={
    'build':['build','airflow-init'],
    'init':['run','--rm','airflow-init'],
    'up':['up','-d','--wait','--wait-timeout','300',*COMPONENTS],
    'ps':['ps'],
    'logs':['logs','--tail','100',*COMPONENTS],
    'import-errors':['exec','airflow-scheduler','airflow','dags','list-import-errors'],
    'stop':['stop',*COMPONENTS,'airflow-db'],
}


def command(action,root=ROOT):
    for relative in ['.env','compose.yaml','compose.airflow.yaml','airflow/local.env','airflow/config/simple_auth_passwords.json']:
        if not (root/relative).is_file():raise FileNotFoundError(f'Missing {relative}; run prepare_airflow.py first')
    return ['docker','compose','--env-file','.env','--env-file','airflow/local.env',
            '-f','compose.yaml','-f','compose.airflow.yaml',*ACTIONS[action]]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=ACTIONS)
    args=p.parse_args()
    if not shutil.which('docker'):raise FileNotFoundError('Docker CLI is not available')
    args_list=command(args.action)
    if args.action in {'build','init','up'}:
        prefix=['docker','compose','--env-file','.env','--env-file','airflow/local.env','-f','compose.yaml','-f','compose.airflow.yaml']
        result=subprocess.run([*prefix,'config','-q'],cwd=ROOT)
        if result.returncode:raise SystemExit(result.returncode)
    result=subprocess.run(args_list,cwd=ROOT)
    raise SystemExit(result.returncode)

if __name__=='__main__':main()
