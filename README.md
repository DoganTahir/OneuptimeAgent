# OneUptime AI Agent Observability POC

Python 3.11+ proof of concept for sending AI Agent, LLM, and tool-call telemetry to **OneUptime 14.x** via OpenTelemetry (OTLP/HTTP).

This repository currently contains **project infrastructure only**. Real agent logic, LLM calls, and OneUptime OTEL wiring will be added in later phases.

## Project layout

```
.
├── config/           # Future configuration files
├── src/              # Application source
│   └── main.py       # Entry point
├── tests/            # Unit / smoke tests
├── .env.example      # Environment variable template
├── .gitignore
├── requirements.txt  # Python dependencies
└── README.md
```

## Prerequisites

- Python **3.11+**
- A virtual environment (recommended)

## Setup

```bash
# Clone / enter the project
cd OneuptimeAgent

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy environment template and fill in values when ready
cp .env.example .env
```

## Environment variables

| Variable | Purpose |
|---|---|
| `ONEUPTIME_OTLP_ENDPOINT` | OneUptime OTLP HTTP endpoint |
| `ONEUPTIME_INGESTION_TOKEN` | OneUptime telemetry ingestion token |
| `OTEL_SERVICE_NAME` | Service name reported in OpenTelemetry spans |
| `LLM_BASE_URL` | LLM API base URL (future use) |
| `LLM_API_KEY` | LLM API key (future use) |
| `LLM_MODEL` | Model identifier (future use) |

## Run

```bash
# From the project root, with the virtualenv activated
python -m src.main
```

Expected output:

```
... [INFO] oneuptime-ai-agent-poc: OneUptime AI Agent Observability POC started
... [INFO] oneuptime-ai-agent-poc: Infrastructure ready — agent, LLM, and OTEL wiring come next
```

## Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

## Dependencies (current)

- `opentelemetry-api` / `opentelemetry-sdk` — OpenTelemetry SDK
- `opentelemetry-exporter-otlp-proto-http` — OTLP over HTTP exporter
- `python-dotenv` — `.env` loading (for upcoming phases)

## Next steps (not implemented yet)

1. Initialize OpenTelemetry TracerProvider and export to OneUptime.
2. Add an LLM client and instrument model calls as spans.
3. Build a minimal AI agent with tool calls and nested spans.
4. Validate Agent Observability dashboards in OneUptime 14.x.
