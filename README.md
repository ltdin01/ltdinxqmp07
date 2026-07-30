# 🚀 Laptop Deals Data Pipeline (`pipeline/scripts`)

Comprehensive guide and technical reference for the **Laptop Deals Pipeline** — owning automated catalog ingestion, parallel scraping (Amazon & Lenovo PSREF), hardware specification inventory construction, centralized CPU/dGPU/iGPU normalization, Amazon Twister CTO option extraction, catalog deduplication, and web app data sync.

---

## 🏗️ Architecture & Pipeline Overview

The pipeline operates in 5 distinct phases:

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                           1. FETCH & SCRAPE                             │
│  - Amazon Parallel Scraper (fresh_parallel_scraper.py)                 │
│    └─ Runs 6 concurrent brand workers with exact brand filter IDs       │
│  - Lenovo PSREF & DLP Scrapers                                          │
│  - Hardware Inventory Builder (build_hardware_inventory.py)             │
│    └─ Builds CPU, dGPU, and iGPU inventories from Wikipedia & PSREF     │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    2. DEDUPLICATE & SPEC NORMALIZE                      │
│  - Centralized Hardware Normalization (normalize_hardware.py)           │
│    └─ Normalizes Intel, AMD, Snapdragon CPUs, iGPUs, & RTX/GTX dGPUs   │
│    └─ Enriches cores, threads, clock speeds, and specific iGPU models   │
│    └─ Formats dGPUs with full VRAM (e.g. RTX 4050 6 GB GDDR6)           │
│    └─ Batch normalizes apps/web/data.json and apps/web/cto_configs/*.json│
│  - Per-Brand Subseries Priority Deduplication (Specific > Other)       │
│  - Spec Normalization & Exact Model SKU Extraction                     │
│  - Amazon Twister CTO Variant Extractor (03_parse_normalize_specs.py)  │
│    └─ Extracts RAM, SSD, Display, Colour, CPU choices                 │
│  - JS Junk Filtering (click-metrics, acrlink, popover removal)         │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         3. INTEGRATE & COMPILE                          │
│  - Catalog Integrator (04_integrate_to_site.py)                         │
│    └─ Merges into pipeline/data/amazon-catalog.json                    │
│    └─ Updates apps/web/data.json (55 categories, 435+ products)         │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         4. WEB APP SEARCH & FILTERS                     │
│  - Interleaved Generation Filter Ordering (SearchResultsClient.tsx)     │
│    └─ Groups contemporary Intel, AMD, & Snapdragon CPUs side-by-side   │
│  - Dynamic Model Badge & Configurable CTO Options                       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 📁 Directory Structure (`pipeline/scripts`)

```text
pipeline/scripts/
├── amazon/                         # 🛒 Amazon Scraper & Normalization Suite
│   ├── fresh_parallel_scraper.py   # Multi-threaded parallel brand worker & PDP fetcher
│   ├── 03_parse_normalize_specs.py # Spec normalizer & Twister CTO option extractor
│   ├── 04_integrate_to_site.py     # Site catalog compiler & web/data.json integrator
│   ├── common.py                   # Brand filters (p_123:*), subseries priority maps, & IO
│   ├── 01_fetch_amazon_asins.py    # Legacy single-threaded ASIN scraper
│   ├── 02_fetch_amazon_pdps.py     # Legacy single-threaded PDP fetcher
│   └── dedupe_catalog.py           # Standalone catalog deduplicator
│
├── build_hardware_inventory.py     # 🔬 Hardware Inventory Builder (CPU, dGPU, iGPU)
├── laptopdeals/
│   ├── normalize_hardware.py       # ⚡ Centralized CPU/GPU Normalization Engine
│   ├── catalog.py                  # 📦 Main Lenovo Catalog Ingestion CLI
│   ├── psref.py                    # 📑 Lenovo PSREF Excel Specification Matcher
│   ├── cto.py                      # ⚙️ Lenovo Custom Build (CTO) Option Generator
│   ├── prices.py                   # 📈 Price History Tracking & Statistics Math
│   ├── archive.py                  # 🗃️ Out-of-Stock & Product Archiving Tool
│   ├── maintenance.py              # 🧹 Catalog Validation & Hygiene Helpers
│   └── merge_json.py               # 🔀 JSON Feeds Merger
```

---

## 🔬 Hardware Normalization & Inventory Suite

### 1. `build_hardware_inventory.py`
* **Purpose**: Fetches and compiles authoritative mobile CPU, discrete GPU, and integrated GPU (iGPU) specification databases.
* **Outputs**:
  * `data/cpu_inventory.json` (3,250+ mobile processor models)
  * `data/gpu_inventory.json` (105+ dGPU models, GeForce 16+ & Ada/Blackwell Workstation)
  * `data/igpu_inventory.json` (160+ iGPU models across Intel, AMD, Qualcomm)
* **Usage**:
  ```bash
  python3 -m pipeline.scripts.build_hardware_inventory
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
  # Incremental mode
  python3 -m pipeline.scripts.laptopdeals.normalize_hardware

  # Full re-normalization mode
  python3 -m pipeline.scripts.laptopdeals.normalize_hardware --all
  ```

---

## ⚡ Full Execution Walkthrough (End-to-End Refresh)

To perform a complete pipeline refresh from scratch:

```bash
# Step 1: Build hardware specification inventories
python3 -m pipeline.scripts.build_hardware_inventory

# Step 2: Scrape Amazon brand catalogs in parallel
PYTHONPATH=. python3 -m pipeline.scripts.amazon.fresh_parallel_scraper --fresh

# Step 3: Normalize specifications & extract Twister CTO options
PYTHONPATH=. python3 -m pipeline.scripts.amazon.03_parse_normalize_specs --brands lenovo asus hp acer msi dell

# Step 4: Run centralized hardware normalization engine on catalog and CTO options
python3 -m pipeline.scripts.laptopdeals.normalize_hardware --all

# Step 5: Verify production web build
pnpm --filter web build
```
