---
id: add-subtract
title: Add a subtract function to calc
created: 2026-05-03
author: smorand
labels: [feature, calc]
---

# Add a subtract function to calc

## Description

The calc module currently exposes only `add`. We need a symmetric `subtract`
function for our internal arithmetic helpers, callable from any module that
imports `calc`. The function must validate its inputs and raise a clear error
when given non-integer arguments, so misuse is caught early in callers.

## Acceptance Criteria

- AC-1: `calc.subtract(5, 3)` returns the integer `2`.
- AC-2: `calc.subtract(0, 0)` returns the integer `0`.
- AC-3: `calc.subtract` accepts two integer arguments and raises `TypeError`
  with a clear message on non-integer input (e.g., `subtract(1.5, 0)` raises
  with message `"subtract requires integer arguments"`).

## Out of Scope

- Floating-point support; integers only for this ticket.
- A CLI command exposing `subtract`; library function only.

## Examples

```python
>>> from calc import subtract
>>> subtract(5, 3)
2
>>> subtract(0, 0)
0
>>> subtract(1.5, 0)
Traceback (most recent call last):
  ...
TypeError: subtract requires integer arguments
```

## Notes

The existing `add` function in `src/calc.py` is the closest reference for
style and validation patterns; mirror it.
