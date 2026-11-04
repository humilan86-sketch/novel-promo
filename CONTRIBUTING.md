# Contributing

Thanks for taking a look! Issues and PRs are welcome.

## Development setup

```bash
cd novel-auto
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # optional: only needed for real script rewriting
./.venv/bin/python -m novelauto doctor
```

You can run the whole pipeline without an API key (offline rewriting fallback) and without footage:

```bash
./.venv/bin/python -m novelauto assets synth --count 2 --seconds 60
./.venv/bin/python -m novelauto prepare -i samples/sample_story_en.txt --minutes 3 --language en
./.venv/bin/python -m novelauto render --job-id <job_id> --hook 1
```

## Before opening a PR

- `python -m compileall -q novel-auto/novelauto` passes
- `cd novel-auto && python -m novelauto --help` works
- No secrets, personal paths (`/Users/...`) or generated media committed
- Keep pipeline stages independent: a stage takes explicit inputs and returns explicit outputs so it can be re-run alone

## Scope

This project generates short-form video from text. Keep contributions content-neutral: no bundled novel text, voices or b-roll. Bring-your-own assets.
