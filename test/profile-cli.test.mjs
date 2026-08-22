import assert from 'node:assert/strict';
import test from 'node:test';

import { positionalArgument } from '../src/cli/options.mjs';

test('skips option values when locating profile identify input', () => {
  assert.equal(
    positionalArgument(['--profile-dir', 'profiles', 'sample.jsc', '--json']),
    'sample.jsc',
  );
});
