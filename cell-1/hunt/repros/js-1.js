import test from 'node:test';
import assert from 'node:assert/strict';
import { runningMax } from './mathx.js';

test('running max handles all-negative input', () => {
  assert.deepEqual(runningMax([-5, -3]), [-5, -3]);
});
