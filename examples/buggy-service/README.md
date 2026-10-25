# buggy-service

A deliberately broken example repository for KAGOU's bug workflow check (`scripts/autonomy_check.py`).
`average_price([])` raises `ZeroDivisionError`; `test_no_trades` fails until it is fixed. Test command:
`python -m unittest -q`. The check copies this folder to a temporary directory and turns it into a git repository,
so the agents' fix is applied to that copy, never to this folder.
