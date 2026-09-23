'use strict';

// Evaluates simple arithmetic expressions like "2 + 3 * 4", supporting
// +, -, *, / and parentheses with standard operator precedence.

function tokenize(expression) {
  const tokens = expression.match(/\d+(\.\d+)?|[+\-*/()]/g);
  if (!tokens) {
    throw new Error(`no tokens found in expression: ${expression}`);
  }
  return tokens;
}

function toRPN(tokens) {
  const precedence = { '+': 1, '-': 1, '*': 2, '/': 2 };
  const output = [];
  const operators = [];

  for (const token of tokens) {
    if (/^\d/.test(token)) {
      output.push(Number(token));
    } else if (token === '(') {
      operators.push(token);
    } else if (token === ')') {
      while (operators.length && operators[operators.length - 1] !== '(') {
        output.push(operators.pop());
      }
      operators.pop();
    } else {
      operators.push(token);
    }
  }

  while (operators.length) {
    output.push(operators.pop());
  }

  return output;
}

function evalRPN(rpn) {
  const stack = [];
  for (const token of rpn) {
    if (typeof token === 'number') {
      stack.push(token);
      continue;
    }
    const b = stack.pop();
    const a = stack.pop();
    switch (token) {
      case '+': stack.push(a + b); break;
      case '-': stack.push(a - b); break;
      case '*': stack.push(a * b); break;
      case '/': stack.push(a / b); break;
      default: throw new Error(`unknown operator: ${token}`);
    }
  }
  return stack[0];
}

function evaluate(expression) {
  return evalRPN(toRPN(tokenize(expression)));
}

module.exports = { evaluate, tokenize, toRPN };
