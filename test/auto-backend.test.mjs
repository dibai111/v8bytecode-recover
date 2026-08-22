import assert from 'node:assert/strict';
import test from 'node:test';

import { createAutoBackend } from '../src/backends/auto-backend.mjs';

test('uses the profile backend when it succeeds', () => {
  let d8Calls = 0;
  const backend = createAutoBackend(
    {
      disassemble() {
        return { backendId: 'profile', text: 'profile output' };
      },
    },
    {
      available() {
        d8Calls += 1;
        return true;
      },
    },
  );

  assert.deepEqual(backend.disassemble('sample.jsc', {}), {
    backendId: 'profile',
    text: 'profile output',
  });
  assert.equal(d8Calls, 0);
});

test('falls back to matching d8 when profile decoding fails', () => {
  let d8Calls = 0;
  const backend = createAutoBackend(
    {
      disassemble() {
        throw new Error('profile layout mismatch');
      },
    },
    {
      available() {
        return true;
      },
      disassemble() {
        d8Calls += 1;
        return { backendId: 'd8', text: 'd8 output' };
      },
    },
  );

  assert.deepEqual(backend.disassemble('sample.jsc'), {
    backendId: 'd8',
    text: 'd8 output',
  });
  assert.equal(d8Calls, 1);
});

test('does not replace an explicit snapshot with d8 fallback', () => {
  let d8Calls = 0;
  const profileError = new Error('snapshot layout mismatch');
  const backend = createAutoBackend(
    {
      disassemble() {
        throw profileError;
      },
    },
    {
      available() {
        return true;
      },
      disassemble() {
        d8Calls += 1;
        return { backendId: 'd8', text: 'unexpected output' };
      },
    },
  );

  assert.throws(
    () => backend.disassemble('sample.jsc', {}, 'snapshot_blob.bin'),
    profileError,
  );
  assert.equal(d8Calls, 0);
});

test('reports both failures when profile and d8 cannot decode', () => {
  const backend = createAutoBackend(
    {
      disassemble() {
        throw new Error('profile failed');
      },
    },
    {
      available() {
        return true;
      },
      disassemble() {
        throw new Error('d8 failed');
      },
    },
  );

  assert.throws(
    () => backend.disassemble('sample.jsc', { payloadOffset: null }),
    (error) => error instanceof AggregateError
      && /profile failed/.test(error.message)
      && /d8 failed/.test(error.message),
  );
});
