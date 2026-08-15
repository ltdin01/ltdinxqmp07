# 🚀 Laptop Deals Data Pipeline (`pipeline/scripts`)

Comprehensive guide and technical reference for the **Laptop Deals Pipeline** — owning automated catalog ingestion, Lenovo PSREF & DLP scraping, hardware specification inventory construction, centralized CPU/dGPU/iGPU normalization, and web app data sync. (Amazon parallel scraping, Twister CTO option extraction, and catalog deduplication are tracked on the un-merged `amazon-integration` branch and are **not** part of `main`.)

---

## 🏗️ Architecture & Pipeline Overview

The pipeline operates in 5 distinct phases:

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                           1. FETCH & SCRAPE                             │
│  - Lenovo PSREF & DLP Scrapers                                          │
│  - Hardware Inventory Builder (build_hardware_inventory.py)             │
│    └─ Consolidates already-fetched local disk files into master         │
│       CPU/dGPU/iGPU DBs (data/*_inventory.json, data/inventory/)        │
│  - Wikipedia Scrapers: build_intel/amd/nvidia_inventory.py              │
│    └─ Run with --scrape; PSREF is NOT involved                          │
│  - Amazon Scraper Suite (fresh_parallel_scraper.py, 03_* etc.)          │
│    └─ ⚠ un-merged amazon-integration branch only, not on main           │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    2. DEDUPLICATE & SPEC NORMALIZE                      │
│  - Centralized Hardware Normalization (normalize_hardware.py)           │
│    └─ Normalizes Intel, AMD, Snapdragon CPUs, iGPUs, & RTX/GTX          │
│       dGPUs; enriches cores, threads, clock speeds, iGPU models         │
│    └─ Formats dGPUs with full VRAM (e.g. RTX 4050 6 GB GDDR6)           │
│    └─ Batch normalizes apps/web/data.json and cto_configs/*.json        │
│  - Per-Brand Subseries Priority Deduplication (Specific > Other)        │
│  - Spec Normalization & Exact Model SKU Extraction                      │
│  - Amazon Twister CTO Variant Extractor (03_parse_normalize_specs.py)   │
│    └─ Extracts RAM, SSD, Display, Colour, CPU choices                   │
│  - JS Junk Filtering (click-metrics, acrlink, popover removal)          │
│    └─ ⚠ above 3 Amazon lines: un-merged amazon-integration branch only  │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         3. INTEGRATE & COMPILE                          │
│  - Catalog Integrator (04_integrate_to_site.py)                         │
│    └─ ⚠ un-merged amazon-integration branch only, not on main           │
│    └─ Merges into pipeline/data/amazon-catalog.json                     │
│    └─ Updates apps/web/data.json (8 categories, 442 products)           │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         4. WEB APP SEARCH & FILTERS                     │
│  - Interleaved Generation Filter Ordering (SearchResultsClient.tsx)     │
│    └─ Groups contemporary Intel, AMD, & Snapdragon CPUs side-by-side    │
│  - Dynamic Model Badge & Configurable CTO Options                       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 📁 Directory Structure (`pipeline/scripts`)

```text
pipeline/scripts/
├── build_amd_inventory.py          # 🔬 AMD Mobile CPU & iGPU Inventory Builder (Wikipedia)
├── build_hardware_inventory.py     # 🔬 Hardware Inventory Builder (CPU, dGPU, iGPU) — consolidates local disk files
├── build_intel_inventory.py        # 🔬 Intel Mobile CPU & iGPU Inventory Builder (Wikipedia)
├── build_nvidia_inventory.py       # 🔬 NVIDIA Mobile GPU Inventory Builder (Wikipedia)
├── catalog.py                      # 📦 Main Catalog Ingestion CLI (--provider lenovo | refurb)
├── clean_hardware_inventory.py     # 🧹 Hardware Inventory Sanitizer & Post-Processor
├── cto.py                          # ⚙️ Lenovo Custom Build (CTO) Option Generator
├── maintenance.py                  # 🧹 Catalog Validation & Hygiene Helpers
├── merge_json.py                   # 🔀 JSON Feeds Merger (git-merge helper)
├── normalize.py                    # ⚡ Thin CLI wrapper over laptopdeals.normalize_hardware
├── prices.py                       # 📈 Price History Tracking & Statistics Math (lenovo-current, refurb-current)
├── psref.py                        # 📑 Lenovo PSREF MTM Spec Pool Matcher & LoadSpecData Engine
├── archive.py                      # 🗃️ Out-of-Stock & Product Archiving Tool
├── quality.py                      # 🛡️ Data Quality Gate & Conflict Auditor
├── verify_all.sh                   # 🧪 Complete Pipeline Test & Workflow Verification Suite
├── laptopdeals/
│   ├── normalize_hardware.py       # ⚡ Centralized CPU/GPU Normalization Engine
│   ├── archive.py                  # 🗃️ Archiving helpers
│   ├── catalog.py                  # 📦 Catalog ingestion & provider dispatch helpers
│   ├── cto.py                      # ⚙️ CTO option generation helpers
│   ├── datafile.py                 # 💾 Deal data file loading/parsing (load_data, write_data)
│   ├── enrich_lenovo.py            # 📑 PSREF Datasheet Generation & Spec Enrichment Ladder
│   ├── history.py                  # 📈 Price history loading & change-point tracking
│   ├── http.py                     # 🌐 Lazy curl-cffi loader for HTTP sessions
│   ├── ids.py                      # 🏷️ Internal model code / ID helpers
│   ├── inventory.py                # 🔬 Hardware inventory lookups
│   ├── jsonio.py                   # 📄 JSON read/write helpers
│   ├── maintenance.py              # 🧹 Catalog hygiene helpers
│   ├── manufacturer.py             # Ladder-based Spec Resolution & Origin Tracker
│   ├── merge_json.py               # 🔀 JSON deep-merge helpers
│   ├── paths.py                    # 🛤️ Repo-root path resolution
│   ├── pdp_fetcher.py              # 📥 PDP (product detail page) fetch helpers
│   ├── pricing.py                  # 💰 Pricing statistics & live update helpers
│   ├── psref.py                    # 📑 PSREF spec matching & Compare/LoadSpecData client
│   ├── quality.py                  # 🛡️ Quality checks & spec conflict analyzer
│   ├── router.py                   # 🌐 Internal route helpers
│   ├── specs.py                    # 🔧 Spec normalization helpers
│   ├── timeutil.py                 # ⏱️ Time formatting utilities
│   ├── providers/
│   │   ├── __init__.py             # Provider registry & lazy dispatcher
│   │   ├── base.py                 # BaseProvider contract (scrape_catalog, format_catalog)
│   │   └── refurb.py               # Lenovo Outlet Certified Refurbished Provider
│   └── sources/
│       ├── bitbns.py               # 🔗 BitBns price source adapter
│       ├── lenovo.py               # 🔗 Lenovo India retail source adapter
│       └── lenovo_outlet.py        # 🔗 Lenovo India Outlet scraper (batch API + inventory)
```

---

## 🔬 Hardware Normalization & Inventory Suite

### 1. `build_hardware_inventory.py`
* **Purpose**: Consolidates already-fetched local disk inventories (`data/intel_cpu_inventory.json`, `data/amd_cpu_inventory.json`, `data/nvidia_gpu_inventory.json`, and every JSON under `data/inventory/`) into master CPU, discrete GPU, and integrated GPU (iGPU) specification databases. It does **not** scrape Wikipedia itself.
* **Outputs**:
  * `data/cpu_inventory.json` (838 mobile processor models)
  * `data/gpu_inventory.json` (320 dGPU models, GeForce 16+ & Ada/Blackwell Workstation)
  * `data/igpu_inventory.json` (currently 0 iGPU models — empty iGPU inventory)
* **Usage**:
  ```bash
  # Consolidate local disk inventories into master files
  python3 -m pipeline.scripts.build_hardware_inventory

  # Run Wikipedia scrapers first (via build_intel/amd/nvidia_inventory.py), then consolidate
  python3 -m pipeline.scripts.build_hardware_inventory --scrape
  ```

---

### 2. `normalize_hardware.py`
* **Purpose**: Centralized normalization engine for processing catalog items and CTO configuration files against inventory databases.
* **Key Features**:
  * **CPU Normalization**: Standardizes Intel (`Intel Core Ultra 7 155H`, `Core Series 1 / Series 2`, `Core 14th/13th/12th Gen`), AMD (`AMD Ryzen 7 8845HS`, `Ryzen AI 300/400 Series`, `Ryzen 200/100 Series`), and Qualcomm Snapdragon.
  * **iGPU & Core/Thread Enrichment**: Automatically populates specific iGPU names (`Radeon 740M`, `Radeon 780M`, `Intel Arc 140V`), iGPU series, cores, and threads.
  * **dGPU Model & VRAM Formatting**: Strips `GeForce` and `PRO` branding, formatting dGPUs consistently with full VRAM and memory type (e.g. `RTX 4050 6 GB GDDR6`, `RTX 5000 Blackwell 24 GB GDDR7`).
  * **Manufacturer TGP Preservation**: Preserves exact manufacturer TGP values without alteration.
  * **Batch CTO Config Normalization**: Automatically normalizes all JSON files in `apps/web/cto_configs/`.
* **Usage**:
  ```bash
  python3 -m pipeline.scripts.laptopdeals.normalize_hardware

  # Skip CTO config normalization (the only functional flag; --all is parsed but unused)
  python3 -m pipeline.scripts.laptopdeals.normalize_hardware --no-cto
  ```

---

## ⚡ Full Execution Walkthrough (End-to-End Refresh)

To perform a complete pipeline refresh from scratch:

```bash
# Step 1: Build hardware specification inventories
python3 -m pipeline.scripts.build_hardware_inventory

# Step 2: Run centralized hardware normalization engine on catalog and CTO options
python3 -m pipeline.scripts.laptopdeals.normalize_hardware

# Step 3: Verify production web build
pnpm --filter web build
```

---

## 🏷️ Certified Refurbished Pipeline (`refurb`)

The `refurb` provider indexes official Lenovo India Outlet clearance products, combines live inventory checks, and enriches listings with full PSREF technical specifications.

### 1. Ingestion & Enrichment Flow
1. **Scrape**: `python pipeline/scripts/catalog.py --provider refurb scrape` queries Lenovo Outlet APIs to fetch active refurbished laptop listings into `data/refurb-catalog.json`.
2. **PSREF Resolution**:
   - Matches the 4-character Machine Type (e.g. `82JK`, `82JU`, `20Y0`, `83HL`, `83JC`).
   - If not in the local index, queries `https://psref.lenovo.com/api/search/DefinitionFilterAndSearch/Suggest` (with fallback to general search for newer/withdrawn series).
   - Generates/loads datasheets via `https://psref.lenovo.com/api/product/Compare/LoadSpecData?ProductKey={ProductKey}` to extract 100% full structured platform defaults (`ports`, `dimensions`, `audio`, `memory_slots`, `storage_slots`).
3. **Format**: `python pipeline/scripts/catalog.py --provider refurb format` applies the 4-tier manufacturer specification resolution ladder (`manufacturer.py`), normalizes model titles (e.g. `Lenovo LOQ Gen 9 (15, AMD)`), and writes `apps/web/data-refurb.json`.
4. **Live Stock & Price Sync**: `python pipeline/scripts/prices.py refurb-current` performs batch queries against Lenovo Outlet inventory endpoints to keep availability and change-point pricing synchronized.

### 2. Automated Workflow
The pipeline runs on a scheduled GitHub Actions workflow:
- **`.github/workflows/refurb-catalog.yml`**: Runs hourly at minute 0 to scrape new outlet listings, re-format specs, check real-time stock, and commit updates to `apps/web/data-refurb.json`.

