# Laptop Deals Ingestion & Update Pipeline (`laptopdeals-scripts`)

Standalone repository owning all Lenovo catalog scraping, price tracking, PSREF spec parsing, and CTO configuration enrichment for the Laptop Deals website.

## Architecture

This repository runs independently on its own GitHub Actions schedule (or manually via `workflow_dispatch`). Upon completing data extraction and formatting, it commits updated files directly back to the main website repository (`laptopdeals`) using cross-repo Git authentication.

### Data Flow

```text
[Scheduled GitHub Action]
       │
       ▼
[Unified Single-Pass PDP Fetcher] ──▶ Extracts price, specs, status, and CTO links in 1 request
       │
       ▼
[Hash Change Router] ───────────────▶ If specs changed: triggers PSREF / CTO re-enrichment
       │                            ▶ If price only: updates price history & bypasses PSREF
       ▼
[Cross-Repo Sync Engine] ──────────▶ Pushes apps/web/data.json & price_history/ to website repo
```

## Quick Start / Deployment Guide

This repository is designed to work **out-of-the-box** after setting up environment secrets:

### 1. Configure GitHub Repository Secrets

In your GitHub repository settings (**Settings -> Secrets and variables -> Actions -> Secrets**), add the following secrets:

* `TARGET_REPO_PAT`: GitHub Personal Access Token (with `repo` scope or `Contents: Read and write` permission on the target website repository).
* `TARGET_REPO_OWNER` *(optional)*: GitHub username or organization owning the website repo (defaults to `kryptobolt07`).
* `TARGET_REPO_NAME` *(optional)*: Name of the target website repository (defaults to `laptopdeals`).
* `TARGET_REPO_BRANCH` *(optional)*: Target branch to push changes to (defaults to `main`).
* `GIT_COMMIT_AUTHOR_EMAIL` *(optional)*: Verified GitHub email associated with your account or bot user (e.g. `your-name@users.noreply.github.com`).
* `GIT_COMMIT_AUTHOR_NAME` *(optional)*: Name to display on automated commits (defaults to `github-actions[bot]`).

### 3. Running Locally

```bash
# Install dependencies
python -m pip install -r requirements.txt

# Run price refresh
python catalog.py scrape --only-new

# Run Lenovo current price update
python prices.py lenovo-current
```

## Repository Structure

```text
├── .github/workflows/         # Scheduled GitHub Actions workflows
│   ├── catalog-hygiene.yml    # Daily catalog ingestion and cleanup
│   ├── data-refresh.yml       # Frequent price/availability refresh
│   └── operations.yml         # Manual targeted SKU management
├── config/
│   └── .env.example           # Configuration template
├── src/                       # Pipeline core modules
│   ├── pdp_fetcher.py         # Unified single-pass PDP fetcher & cache
│   ├── router.py              # Hash change router (price vs. spec)
│   ├── publisher/             # Cross-repo GitHub sync & push engine
│   ├── scrapers/              # Lenovo DLP & PDP scrapers
│   ├── psref/                 # PSREF Excel workbook matcher
│   ├── history/               # Price history & statistics math
│   └── utils/                 # Path & JSON IO helpers
└── requirements.txt
```
