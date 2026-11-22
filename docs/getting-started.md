# Getting started

This guide contains the operational instructions kept out of the short project
README. Run commands from the repository root.

## Requirements

- Python 3.9 or newer.
- Git.
- No network connection or API key for the baseline, normal tests, or scripted
  demonstrations.
- An individual ASU Research Computing Voyager key for live hosted execution.

The frozen baseline has no third-party dependencies. The main agent uses the
packages pinned in [`requirements.txt`](../requirements.txt).

## Create an isolated environment

macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export PYTHONPATH=./src
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:PYTHONPATH = '.\src'
```

If the shell prompt does not show `(.venv)`, activate the environment again
before installing or running the main system.

## Run offline tests

```bash
python -m unittest discover -s baseline/tests -p "test*.py" -q
python -m unittest discover -s tests/agent -p "test*.py" -q
python -m unittest discover -s tests/contracts -p "test*.py" -q
python -m unittest discover -s tests/evaluation -p "test*.py" -q
python -m unittest discover -s tests/integration -p "test*.py" -q
python -m unittest discover -s tests/output -p "test*.py" -q
python -m unittest discover -s tests/safety -p "test*.py" -q
python -m unittest discover -s tests/tools -p "test*.py" -q
```

These suites use scripted or fake hosted responses. They do not read a Voyager
key or make network calls.

## Run offline demonstrations

The smallest scripted runtime demonstration:

```bash
python -m claim_agent.agent.offline_runner --pretty
```

The production-shaped local path uses real local routing, tools, evidence
storage, validation, output packaging, and tracing with a scripted model:

```bash
python -m claim_agent.agent.integrated_runner --pretty
```

To save a canonical JSON Lines trace:

macOS or Linux:

```bash
commit=$(git rev-parse HEAD)
python -m claim_agent.agent.integrated_runner \
  --pretty \
  --trace-directory .local-output/traces \
  --code-commit "$commit"
```

Windows PowerShell:

```powershell
$commit = git rev-parse HEAD
python -m claim_agent.agent.integrated_runner `
    --pretty `
    --trace-directory '.\.local-output\traces' `
    --code-commit $commit
```

## Create and load a Voyager key

1. Sign in to [ASU Research Computing Voyager](https://voyager.rc.asu.edu/).
2. Open **My Profile**, select **Keys**, and choose **Create API key** under
   **AI LLM API keys**.
3. Copy the complete value immediately. Voyager shows the full secret only when
   the key is created or rotated.
4. If the value was not saved, rotate the key or create a replacement. The
   masked value on the key list cannot be recovered.

Load the key only into the current terminal. Do not place it in `.env`, source
files, commands, screenshots, traces, issues, or pull requests.

macOS or Linux:

```bash
printf 'Voyager API key: '
stty -echo
IFS= read -r CLAIM_AGENT_LLM_API_KEY
stty echo
printf '\n'
export CLAIM_AGENT_LLM_API_KEY
```

Windows PowerShell:

```powershell
$secureKey = Read-Host 'Voyager API key' -AsSecureString
$env:CLAIM_AGENT_LLM_API_KEY = [System.Net.NetworkCredential]::new('', $secureKey).Password
```

At the hidden prompt, paste the complete key and press Enter once. The prompt's
colon is punctuation; it is not part of the key.

Check that a plausible value is loaded without printing it.

macOS or Linux:

```bash
if [[ "$CLAIM_AGENT_LLM_API_KEY" == sk_* && ${#CLAIM_AGENT_LLM_API_KEY} -gt 20 ]]; then
  echo 'Voyager key loaded'
else
  echo 'Voyager key is missing or appears incomplete'
fi
```

Windows PowerShell:

```powershell
if (
    -not [string]::IsNullOrWhiteSpace($env:CLAIM_AGENT_LLM_API_KEY) -and
    $env:CLAIM_AGENT_LLM_API_KEY.StartsWith('sk_') -and
    $env:CLAIM_AGENT_LLM_API_KEY.Length -gt 20
) {
    'Voyager key loaded'
} else {
    'Voyager key is missing or appears incomplete'
}
```

The variable disappears when the terminal closes. Each team member should use
their own key.

## Run the hosted demonstration

The hosted runner uses the accepted `beam-1-0` configuration and the same local
safety boundaries as the offline path.

macOS or Linux:

```bash
commit=$(git rev-parse HEAD)
python -m claim_agent.agent.hosted_runner --pretty --code-commit "$commit"
```

Windows PowerShell:

```powershell
$commit = git rev-parse HEAD
python -m claim_agent.agent.hosted_runner `
    --pretty `
    --code-commit $commit
```

By default the command writes a trace under `.local-output/hosted-traces/`. A
timeout, malformed response, provider failure, or insufficient evidence
produces a traced `MANUAL_REVIEW` result. Hosted execution never switches to
the scripted model or a different hosted model.

## Run a reproducible baseline example

macOS or Linux:

```bash
python baseline/run_baseline.py \
  --input baseline/examples/test2_medical_necessity.json \
  --as-of 2026-09-06 \
  --outdir .local-output/baseline-smoke
```

Windows PowerShell:

```powershell
python '.\baseline\run_baseline.py' `
    --input '.\baseline\examples\test2_medical_necessity.json' `
    --as-of '2026-09-06' `
    --outdir '.\.local-output\baseline-smoke'
```

The expected disposition is `APPEAL_WITH_DOCUMENTATION`, the root cause is
`MEDICAL_NECESSITY`, and the illustrative deadline is `2027-01-07`.

## Troubleshooting

- **`ModuleNotFoundError: claim_agent`:** set `PYTHONPATH=./src` or
  `$env:PYTHONPATH = '.\src'` in the current shell.
- **The environment uses the wrong Python:** remove and recreate only `.venv`,
  then activate it before installing dependencies.
- **The pasted key appears blank:** hidden input intentionally shows no
  characters. Paste once and press Enter once.
- **The key variable is null:** assign the result of `Read-Host` as shown above;
  entering a prompt label does not assign a value.
- **A hosted request times out:** the runtime records the failure and sends the
  case to manual review. Do not add an unreviewed fallback or silently increase
  limits.

For frozen experiment commands and result evidence, see
[Evaluation](evaluation.md).
