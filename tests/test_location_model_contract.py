"""Offline upgrade contract checks; these do not execute PostgreSQL."""
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class LocationModelContractTests(unittest.TestCase):
    def test_reporting_uses_store_price_and_keeps_nullable_rating(self):
        text=(ROOT/'sql/011_location_reporting.sql').read_text()
        self.assertIn('simulation_anchor_price AS catalog_price',text)
        self.assertIn('observed_rating AS rating',text)
        self.assertNotIn('coalesce(observed_rating',text.lower())
    def test_incremental_customers_receive_location_keys(self):
        text=(ROOT/'sql/006_build_warehouse.sql').read_text()
        self.assertLess(text.index('INSERT INTO warehouse.dim_location'),text.index('INSERT INTO warehouse.dim_customer'))
        self.assertIn('JOIN warehouse.dim_location l ON l.city=c.city AND l.country=c.country',text)
    def test_upgrade_has_no_destructive_statements(self):
        text=(ROOT/'sql/011_location_reporting.sql').read_text().upper()
        self.assertNotIn('DROP ',text)
        self.assertNotIn('TRUNCATE ',text)
        self.assertIn('ALTER COLUMN LOCATION_KEY SET NOT NULL',text)
if __name__=='__main__':unittest.main()
