'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { evaluate } = require('../src/parser');

test('adds numbers', () => {
  assert.equal(evaluate('2 + 3'), 5);
});

test('multiplies numbers', () => {
  assert.equal(evaluate('3 * 4'), 12);
});

test('respects multiplication precedence over addition', () => {
  assert.equal(evaluate('2 * 3 + 4'), 10);
});

test('respects parentheses', () => {
  assert.equal(evaluate('(2 + 3) * 4'), 20);
});
