import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/generate'))
import generate_store_data as gen
from validate_store_data import validate, amount

class DataContractTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.root=Path(self.tmp.name)
  self.catalog=ROOT/'data/reference/catalog_final/simulation_products.csv'
 def tearDown(self):self.tmp.cleanup()
 def make(self,run):
  args=SimpleNamespace(run_id=run,catalog=str(self.catalog),customers=20,orders=50,items=120,seed=42)
  with patch.object(gen,'ROOT',self.root),patch('sys.stdout',new=io.StringIO()):gen.generate(args)
  return self.root/'data/raw/synthetic_runs'/run
 def test_generated_relationships_and_amounts(self):
  result=validate(self.make('valid'))
  self.assertEqual(result['status'],'passed')
  self.assertEqual(result['order_items'],120)
  self.assertTrue(result['foreign_keys_valid'])
  self.assertTrue(result['amounts_reconciled'])
 def test_seed_reproduces_file_hashes(self):
  a=self.make('one');b=self.make('two')
  self.assertEqual(json.loads((a/'manifest.json').read_text())['files'],json.loads((b/'manifest.json').read_text())['files'])
 def test_corrupted_bytes_refused(self):
  folder=self.make('corrupt')
  with (folder/'orders.csv').open('ab') as f:f.write(b'corruption')
  with self.assertRaisesRegex(ValueError,'integrity'):validate(folder)
 def test_unknown_customer_refused_after_rehash(self):
  folder=self.make('foreignkey');p=folder/'orders.csv'
  with p.open(newline='') as f:reader=csv.DictReader(f);fields=reader.fieldnames;rows=list(reader)
  rows[0]['customer_id']='C999999999'
  with p.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
  mp=folder/'manifest.json';m=json.loads(mp.read_text());m['files']['orders.csv']={'sha256':gen.hashfile(p),'bytes':p.stat().st_size};mp.write_text(json.dumps(m))
  with self.assertRaisesRegex(ValueError,'Unknown customer'):validate(folder)
 def test_invalid_money_refused(self):
  for value in ['NaN','Infinity','-1','0.001']:
   with self.subTest(value=value),self.assertRaises(ValueError):amount(value)
 def test_duplicate_catalog_refused(self):
  with self.catalog.open(encoding='utf-8-sig',newline='') as f:r=csv.DictReader(f);fields=r.fieldnames;row=next(r)
  p=self.root/'duplicate.csv'
  with p.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows([row,row])
  with self.assertRaisesRegex(ValueError,'unique'):gen.load_catalog(p)

if __name__=='__main__':unittest.main()
