import json
import shutil
import tempfile
import unittest
from pathlib import Path

try:
    from .profiles import load_profiles, read_profile_data, validate_profile_data
    from .tooling.generate_profiles import VERSIONS
except ImportError:  # unittest discover with cached_data as the top-level path.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from cached_data.profiles import (  # type: ignore[no-redef]
        load_profiles,
        read_profile_data,
        validate_profile_data,
    )
    from cached_data.tooling.generate_profiles import VERSIONS  # type: ignore[no-redef]


class ProfilePackTests(unittest.TestCase):
    def test_bundled_catalog_matches_generator_versions(self):
        bundled = Path(__file__).with_name('profiles')
        index = json.loads((bundled / 'index.json').read_text(encoding='utf-8'))

        self.assertEqual(tuple(index['versions']), VERSIONS)
        self.assertIn('14.9.480207', VERSIONS)
        self.assertIn('15.3.79', VERSIONS)

    def test_bundled_catalog_has_no_orphan_profile_files(self):
        bundled = Path(__file__).with_name('profiles')
        index = json.loads((bundled / 'index.json').read_text(encoding='utf-8'))
        profile_files = {
            path.stem
            for path in bundled.glob('*.json')
            if path.name != 'index.json'
        }

        self.assertEqual(profile_files, set(index['versions']))

    def test_loads_an_external_exact_profile_pack(self):
        bundled = Path(__file__).with_name('profiles')
        with tempfile.TemporaryDirectory(prefix='v8bytecode-profile-test-') as root:
            directory = Path(root)
            index = json.loads((bundled / 'index.json').read_text(encoding='utf-8'))
            index['versions'] = ['9.4.146.24']
            (directory / 'index.json').write_text(json.dumps(index), encoding='utf-8')
            shutil.copyfile(bundled / '9.4.146.24.json', directory / '9.4.146.24.json')

            profiles = load_profiles(directory)

            self.assertEqual(profiles.directory, directory.resolve())
            self.assertEqual(profiles.by_version('9.4.146.24').version_hash, 0xcd991825)

    def test_rejects_a_profile_pack_with_missing_files(self):
        bundled = Path(__file__).with_name('profiles')
        with tempfile.TemporaryDirectory(prefix='v8bytecode-invalid-profile-test-') as root:
            directory = Path(root)
            index = json.loads((bundled / 'index.json').read_text(encoding='utf-8'))
            index['versions'] = ['9.4.146.24']
            (directory / 'index.json').write_text(json.dumps(index), encoding='utf-8')

            with self.assertRaisesRegex(ValueError, r'unable to read profile 9\.4\.146\.24'):
                load_profiles(directory)

    def test_rejects_an_unresolved_operand_type(self):
        index, items = read_profile_data()
        items[0]["bytecodes"][0]["operands"] = ["UnresolvedOperand"]

        errors = validate_profile_data(index, items)

        self.assertTrue(
            any("unsupported operand types: UnresolvedOperand" in error for error in errors)
        )


if __name__ == '__main__':
    unittest.main()
