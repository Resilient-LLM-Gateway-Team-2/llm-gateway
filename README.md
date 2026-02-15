**Resilient LLM Gateway (Team 2)**
A high-performance, resilient middleware that abstracts multiple LLM providers (OpenAI, Anthropic, Mock) into a single, unified REST API.

**🌟 Key Features**

Unified Interface: A single /chat endpoint to rule them all.
Automatic Failover: Smart routing that switches to a backup provider if the primary fails.
Semantic Caching: Redis-backed caching that recognizes similar prompts to save costs and reduce latency.
Cost & Token Tracking: Real-time logging of usage and spend per API key in PostgreSQL.
Rate Limiting: Protect your upstream providers with custom per-user limits.

**🏗️ Architecture**

The gateway acts as a resilient proxy layer between your application and the LLM providers.

**🛠️ Tech Stack**

Framework: FastAPI
Database: PostgreSQL (Usage Logs)
Cache: Redis (Semantic & Exact Match)
Async Client: HTTPX
Containerization: Docker & Docker Compose

**🚀 Quick Start (Sprint 0)**
Currently, we are in Sprint 0. Our foundation is being laid.

1. Clone the repo:
Bash
**git clone https://github.com/Resilient-LLM-Gateway-Team-2/llm-gateway.git**

2. View Documentation: Check out our GitHub Wiki for the full project description and roadmap.
