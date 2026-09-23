'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { formatBlock } = require('../src/format');

test('removes common leading whitespace', () => {
  const input = '    line one\n    line two';
  const result = formatBlock(input);
  assert.equal(result.trim(), 'line one\nline two');
});
