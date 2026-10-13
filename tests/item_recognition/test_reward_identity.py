"""Canonical identity regression checks without pixels or device interaction."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/item_recognition'))
from reward_identity import canonical_groups


def record(rid, **values):
    return dict(id=rid, record_type='item', display_name='Verified Material',
                game_id='material-1', element='Heart', rarity='4', category='material',
                **values)


class CanonicalIdentityTests(unittest.TestCase):
    def test_api_alias_membership_preserves_hidden_calibration_id(self):
        canonical = record('canonical', canonical_id='canonical', aliases=['historical'])
        mapping, groups = canonical_groups([canonical], preferred=['historical'])
        self.assertEqual(mapping, {'canonical': 'canonical', 'historical': 'canonical'})
        self.assertEqual(groups['canonical']['member_ids'], ['canonical', 'historical'])
        self.assertEqual(groups['canonical']['evidence'][0]['kind'], 'library_canonical_identity')

    def test_loaded_library_alias_variants_share_one_scored_identity(self):
        canonical = record('canonical', canonical_id='canonical', aliases=['alias'])
        alias = record('alias', canonical_id='canonical', aliases=['alias'])
        mapping, groups = canonical_groups([canonical, alias], preferred=['alias'])
        self.assertEqual(mapping, {'canonical': 'canonical', 'alias': 'canonical'})
        self.assertEqual(groups['canonical']['member_ids'], ['alias', 'canonical'])

    def test_singleton_api_metadata_does_not_override_calibration_preference(self):
        records = [record(rid, canonical_id=rid, aliases=[]) for rid in ('a', 'b')]
        mapping, groups = canonical_groups(records, preferred=['b'])
        self.assertEqual(mapping, {'a': 'b', 'b': 'b'})
        self.assertEqual(len(groups), 1)

    def test_conflicting_loaded_library_alias_is_not_merged(self):
        canonical = record('canonical', canonical_id='canonical', aliases=['alias'])
        alias = record('alias')
        alias['element'] = 'Body'
        mapping, groups = canonical_groups([canonical, alias])
        self.assertEqual(mapping, {'canonical': 'canonical', 'alias': 'alias'})
        self.assertEqual(groups['canonical']['member_ids'], ['canonical'])

    def test_missing_canonical_record_cannot_be_a_scored_output(self):
        mapping, groups = canonical_groups([record('alias', canonical_id='absent', aliases=['alias'])])
        self.assertEqual(mapping, {'alias': 'alias'})
        self.assertEqual(groups['alias']['member_ids'], ['alias'])

    def source_card_pair(self):
        canonical = record('canonical', assets=[{'source_image_url': 'https://example.test/missions/card-0949.webp'}])
        canonical.update(display_name='Limit Break Crystal "Body Crystal"', game_id='949',
                         element='Body', rarity='4', category='item')
        alias = record('alias', assets=[{'source_image_url': 'https://example.test/lb/card-0949.webp'}])
        alias.update(display_name='Body Crystal', game_id=None, element='Body', rarity=None, category='crystal')
        reviewed = {'canonical_item_id': 'canonical', 'member_ids': ['canonical', 'alias'],
                    'allow_alternate_names': True, 'source_card_id': '949',
                    'evidence': [{'kind': 'independently_reviewed_source_card', 'screenshot': 'labeled-result.png'}]}
        return canonical, alias, reviewed

    def test_reviewed_alternate_names_require_explicit_shared_source_card(self):
        canonical, alias, reviewed = self.source_card_pair()
        mapping, groups = canonical_groups([canonical, alias], [reviewed])
        self.assertEqual(mapping, {'canonical': 'canonical', 'alias': 'canonical'})
        self.assertEqual(groups['canonical']['evidence'][0]['source_card_id'], '949')
        for missing in ('source_card_id', 'allow_alternate_names', 'evidence'):
            with self.subTest(missing=missing):
                incomplete = dict(reviewed)
                incomplete.pop(missing)
                mapping, _ = canonical_groups([canonical, alias], [incomplete])
                self.assertEqual(mapping['alias'], 'alias')

    def test_reviewed_source_card_never_waives_known_metadata_conflicts(self):
        for field, value in (('record_type', 'character'), ('game_id', '950'),
                             ('rarity', '5'), ('element', 'Heart'), ('category', 'scroll')):
            with self.subTest(field=field):
                canonical, alias, reviewed = self.source_card_pair()
                canonical['category'] = 'crystal'
                alias[field] = value
                mapping, _ = canonical_groups([canonical, alias], [reviewed])
                self.assertEqual(mapping['alias'], 'alias')

    def test_reviewed_card_ignores_identical_pixels_without_source_identity(self):
        for assets in ([], [{'source_image_url': 'https://example.test/art/same.png'}],
                       [{'source_image_url': 'https://example.test/lb/card-0950.webp'}]):
            with self.subTest(assets=assets):
                canonical, alias, reviewed = self.source_card_pair()
                alias['assets'] = assets
                mapping, _ = canonical_groups([canonical, alias], [reviewed])
                self.assertEqual(mapping['alias'], 'alias')


if __name__ == '__main__':
    unittest.main()
