# LLM Gateway - Project Analysis

## 1. DASHBOARD UI ELEMENTS

### Header Section
- **Logo**: "GATEWAY" with animated pulse effect and rotating diamond icon
- **Status Badge**: "Live Mode" with animated pulse dot
- **Health Check Indicators** (3 dots that update every 5 seconds):
  - `#api-health` - API status
  - `#db-health` - Database (PostgreSQL) status
  - `#cache-health` - Redis cache status
- **API Key Input**: Password field for authentication (`#api-key-input`, default: "test_key")

### Main Chat Section
- **Messages Area** (`#messages`): Displays conversation history
  - User messages: Cyan gradient boxes, right-aligned
  - Bot messages: Dark with magenta border, left-aligned
  - Error messages: Red-tinted with danger colors
- **Input Section**:
  - Chat input field (`#chat-input`)
  - Send button (`#send-btn`)

### Sidebar Panels

#### Panel 1: Metrics (`📊 Metrics`)
**Grid Layout (2x2)**:
- `#request-count`: Total number of requests sent
- `#avg-latency`: Average latency in milliseconds
- `#success-rate`: Success rate percentage (successCount / requestCount * 100)
- `#tokens-used`: Cumulative tokens used across all requests

#### Panel 2: Cost Estimate (`💰 Cost Estimate`)
Shows cost breakdown for the **last request only**:
- `#openai-cost`: Estimated cost if OpenAI was used ($X.XXXXXX)
- `#gemini-cost`: Estimated cost if Gemini was used ($X.XXXXXX)
- `#provider-used-cost`: Actual cost for the provider that responded ($X.XXXXXX)
- Label: "Last Request"

#### Panel 3: Route Path (`🛣️ Route Path`)
`#route-container`: Shows the journey of the request through providers
- **Display format per step**:
  1. Provider name (e.g., "1. OPENAI")
  2. Resolved model (e.g., "Model: gpt-4")
  3. Status badge: ✓ "success" (green) or ✗ "failed" (red)
- Default text if no routing info: "Send a message to see routing info"

### JavaScript State Tracking
```javascript
let requestCount = 0;          // Total requests
let totalLatency = 0;          // Sum of all latencies
let successCount = 0;          // Successful requests
let totalTokens = 0;           // Cumulative tokens
```

### Key JavaScript Functions
1. `updateHealth()` - Fetches `/health/detailed` every 5 seconds, updates health dots
2. `updateMetrics(latency)` - Updates request count, avg latency, success rate, token count
3. `updateCosts(costEstimate)` - Populates cost panel with cost_estimate data
4. `updateRoute(routePath)` - Renders Route Path panel with RouteStep objects
5. `sendMessage()` - Main function that sends POST to `/chat` and processes response

---

## 2. API ENDPOINTS & RESPONSE FORMATS

### GET `/` - Serve Dashboard
**Response**: HTML file (dashboard.html)
**Purpose**: Loads the UI

### GET `/health` - Basic Health Check
**Response**:
```json
{"status": "ok"}
```

### GET `/health/detailed` - Detailed Health Check
**Response**:
```json
{
  "api": "ok",        // Always "ok" if endpoint is reachable
  "postgres": "ok",   // "ok" or "error"
  "redis": "ok"       // "ok" or "error"
}
```
**Implementation**:
- Postgres: Executes `SELECT 1` query
- Redis: Calls `redis_client.ping()`
- Updates health dots in header every 5 seconds

### POST `/chat` - Main Chat Endpoint
**Request Headers**:
```
X-API-Key: <api_key_from_api_keys_table>
Content-Type: application/json
```

**Request Body** (ChatRequest):
```json
{
  "model": "gpt-4",
  "messages": [
    {"role": "user", "content": "string"}
  ],
  "temperature": 0.7,
  "max_tokens": 256
}
```

**Response** (ChatResponse):
```json
{
  "content": "response text",
  "provider": "openai",
  "model": "gpt-4",
  "usage": {
    "prompt_tokens": 15,
    "completion_tokens": 42,
    "total_tokens": 57
  },
  "route_path": [
    {
      "provider": "openai",
      "requested_model": "gpt-4",
      "resolved_model": "gpt-4",
      "status": "success",
      "source": "router"
    }
  ],
  "cost_estimate": {
    "openai_usd": 0.000345,
    "gemini_usd": 0.000213,
    "provider_used_usd": 0.000345,
    "currency": "USD",
    "pricing_basis": "estimated_from_tokens"
  }
}
```

**Error Response** (HTTP 502):
```json
{"detail": "All LLM providers failed"}
```

**Error Response** (HTTP 403):
```json
{"detail": "Missing X-API-Key header"}
```

**Processing Flow**:
1. ✓ Validate X-API-Key header (verify_api_key) → HTTPException 403 if missing/invalid
2. ✓ Validate ChatRequest via Pydantic
3. ✓ Check two-layer cache:
   - Layer 1 (exact_key): Full request state hash (model + temperature + max_tokens + all messages)
   - Layer 2 (prompt_key): Last user message + model
   - If cache hit: Return cached response with `route_path = [{"provider": "redis_cache", "source": "cache", "status": "success"}]`
4. ✓ Call `route_request(body)` which tries providers in order
5. ✓ Build `cost_estimate` from response usage tokens
6. ✓ Log request to PostgreSQL (endpoint, provider, model, status_code, latency_ms, api_key_id)
7. ✓ Return ChatResponse with route_path and cost_estimate
8. ✓ Cache successful response under both keys

**Authentication**:
- Requires X-API-Key header
- Validates against `api_keys` table (fields: id, key, owner, created_at)
- Returns 403 if missing or invalid

---

## 3. ROUTING LOGIC & DECISION SYSTEM

### Provider Chain
```
Primary:   OpenAI
  ↓ (on failure)
Fallback:  Gemini
  ↓ (on failure)
Fallback²: MockProvider
  ↓ (on failure)
✗ Raise ProviderError
```

### Routing Decision Algorithm (`_select_provider_order()`)
Determines whether to prefer OpenAI or Gemini based on:

**1. Model Name Analysis**
- If model starts with "gemini" → `["gemini", "openai"]`
- If model starts with "gpt" or "openai" → `["openai", "gemini"]`

**2. User Intent Keywords** (overrides #1)
- Looks for keywords in the last user message (case-insensitive)
- Gemini-preferred keywords:
  ```python
  {"summarize", "summary", "rewrite", "rephrase", "translate", 
   "paraphrase", "shorten", "tone", "grammar"}
  ```
- If user says "use gemini" (without saying "gpt" or "openai") → `["gemini", "openai"]`

**3. Default**
- `["openai", "gemini"]` (OpenAI first)

### Provider Adapters

#### OpenAI Adapter (`call_openai()`)
- Uses `openai.OpenAI()` client
- Calls `client.chat.completions.create()` with:
  - model: requested model
  - messages: normalized to `{"role": role, "content": content}`
  - temperature, max_tokens: from request
- Returns ChatResponse with actual usage from API
- Retry logic: **3 attempts with exponential backoff (1s → 2s → 4s)**

#### Gemini Adapter (`call_gemini()`)
- Uses `google.generativeai` client
- Maps requested model to Gemini model (defaults to "gemini-1.5-flash")
- Combines messages into single prompt (Gemini uses simpler interface)
- Calls `model.generate_content()` with generation_config (temperature, max_output_tokens)
- Returns ChatResponse with:
  - Actual usage from `response.usage_metadata` if available
  - Falls back to token estimation if usage metadata missing
- Retry logic: **3 attempts with exponential backoff (1s → 2s → 4s)**

#### MockProvider (`MockProvider.call()`)
- Always returns a simulated response
- Content: "This is a simulated response from the MockProvider. The real providers (OpenAI/Gemini) were unavailable or out of quota."
- Estimates tokens based on text length (~4 chars/token)
- provider="mock", model="mock-model"

### Route Tracing
Each attempt appends a `RouteStep` to `route_path`:
```python
RouteStep(
  provider="openai",          # Which provider was tried
  requested_model="gpt-4",    # What user asked for
  resolved_model="gpt-4",     # What provider actually uses
  status="success" | "failed", # Whether it worked
  source="router"             # Where this came from
)
```

**For Cache Hits**, route_path is manually constructed:
```python
route_path = [{
  "provider": "redis_cache",
  "requested_model": body.model,
  "resolved_model": response.model,
  "status": "success",
  "source": "cache"
}]
```

**Information Available in Routing**:
- ✓ Which provider was attempted
- ✓ What model was requested vs. resolved
- ✓ Whether each step succeeded or failed
- ✓ Cache hits are tracked separately
- ✗ **MISSING**: Failure reasons (exact error message)
- ✗ **MISSING**: Latency per provider
- ✗ **MISSING**: Timestamp when each step occurred
- ✗ **MISSING**: Token usage per provider attempt
- ✗ **MISSING**: Fallback reasons (why did Primary fail?)

---

## 4. DATA STRUCTURES (Pydantic Models)

### ChatRequest
```python
class ChatRequest(BaseModel):
    model: str                    # e.g., "gpt-4", "gemini-pro"
    messages: List[Message]       # At least 1 message
    temperature: float = 0.7      # 0.0 - 2.0
    max_tokens: int = 256         # 1 - 4096
```

### Message
```python
class Message(BaseModel):
    role: str                     # "system" | "user" | "assistant"
    content: str                  # The message text
```

### ChatResponse
```python
class ChatResponse(BaseModel):
    content: str                  # LLM's response text
    provider: str                 # "openai", "gemini", "mock", "redis_cache"
    model: str                    # Actual model used
    usage: UsageStats            # Token counts
    route_path: List[RouteStep] = []  # Steps through providers
    cost_estimate: Optional[CostEstimate] = None
```

### UsageStats
```python
class UsageStats(BaseModel):
    prompt_tokens: int = 0       # Tokens in request
    completion_tokens: int = 0   # Tokens in response
    total_tokens: int = 0        # Sum
```

### RouteStep
```python
class RouteStep(BaseModel):
    provider: str                # Provider name
    requested_model: str         # Model client requested
    resolved_model: Optional[str] = None  # Model actually used
    status: str                  # "success" or "failed"
    source: str = "router"       # "router" or "cache"
```

### CostEstimate
```python
class CostEstimate(BaseModel):
    openai_usd: float = 0.0      # Cost if OpenAI priced it
    gemini_usd: float = 0.0      # Cost if Gemini priced it
    provider_used_usd: float = 0.0  # Actual provider's cost
    currency: str = "USD"
    pricing_basis: str = "estimated_from_tokens"
```

---

## 5. CACHING SYSTEM

### Two-Layer Cache Strategy

**Layer 1: Exact Match Cache** (`exact_key`)
- Cache key = `chat:<api_key_id>:<sha256_hash>`
- Hash of: `{model, temperature, max_tokens, full_messages_array}`
- Purpose: Identical requests return cached response
- Hit scenario: User repeats exact same conversation

**Layer 2: Prompt-Level Cache** (`prompt_key`)
- Cache key = `prompt:<api_key_id>:<sha256_hash>`
- Hash of: `<requested_model>:<last_user_message>`
- Purpose: Same question asked differently still hits cache
- Hit scenario: User asks same question with same model

**Implementation**:
- Uses `app.cache` module (likely Redis)
- Functions: `get_cached_response(key)`, `set_cached_response(key, response_dict)`
- Cached response is stored as full `ChatResponse.model_dump()`
- On cache hit, constructs route_path with `source="cache"`

### Missing Cache Features
- ✗ No cache statistics exposed (hit/miss rates)
- ✗ No cache invalidation strategy visible
- ✗ No cache TTL or size limits defined
- ✗ No endpoint to clear cache or view cache stats

---

## 6. DATABASE LAYER

### ORM Models (SQLAlchemy)

#### ApiKey Table
```python
class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Integer (PK)
    key: String(255) - unique
    owner: String(255)
    created_at: DateTime (auto: now)
```

#### RequestLog Table
```python
class RequestLog(Base):
    __tablename__ = "requests"
    id: Integer (PK)
    api_key_id: Integer (FK → api_keys.id)
    endpoint: String(255)    # "/chat"
    provider: String(255)    # "openai", "gemini", "mock", "redis_cache"
    model: String(255)       # Requested model
    status_code: Integer     # HTTP status
    latency_ms: Integer      # Response time
    created_at: DateTime (auto: now)
```

**What's Logged**:
- ✓ Each request endpoint, provider used, model, status_code, latency
- ✓ Associated with api_key_id for per-user tracking
- ✗ **MISSING**: Cost tracking per request
- ✗ **MISSING**: Route taken (only final provider logged)
- ✗ **MISSING**: Failure details (which previous providers were tried)
- ✗ **MISSING**: Token usage per request
- ✗ **MISSING**: Number of retries attempted

---

## 7. COST ESTIMATION SYSTEM

### Pricing Configuration (Environment Variables)
```python
OPENAI_PROMPT_USD_PER_1K = 0.005      # Input tokens
OPENAI_COMPLETION_USD_PER_1K = 0.015  # Output tokens
GEMINI_PROMPT_USD_PER_1K = 0.00125    # Input tokens
GEMINI_COMPLETION_USD_PER_1K = 0.00375 # Output tokens
```

### Cost Calculation Algorithm
```python
def _cost_from_tokens(prompt_tokens, completion_tokens, prompt_rate, completion_rate):
    prompt_cost = (prompt_tokens / 1000) * prompt_rate
    completion_cost = (completion_tokens / 1000) * completion_rate
    return round(prompt_cost + completion_cost, 8)
```

### CostEstimate Generation
1. Extracts prompt_tokens and completion_tokens from response.usage
2. Calculates costs for both OpenAI and Gemini pricing
3. Determines provider_used_usd based on response.provider:
   - If "openai" in provider → use OpenAI cost
   - If "gemini" in provider → use Gemini cost
   - If "cache" or "redis" in provider → use min(openai_cost, gemini_cost)
   - Otherwise → use min(openai_cost, gemini_cost)

**Current Limitations**:
- ✗ Only "last request" cost shown in UI (not cumulative)
- ✗ No cost tracking over time
- ✗ No cost breakdown by provider/time period
- ✗ No cost alerts or budget warnings
- ✗ Costs not stored in database for analysis

---

## 8. FEATURES IMPLEMENTED vs. MISSING

### ✅ IMPLEMENTED FEATURES

| Feature | Location | Status |
|---------|----------|--------|
| **Chat UI** | dashboard.html | Full chat interface with messages |
| **Health Checks** | `/health`, `/health/detailed` | API, PostgreSQL, Redis monitored |
| **API Authentication** | `/chat` + verify_api_key() | X-API-Key validation |
| **Provider Failover** | router.py | OpenAI → Gemini → Mock |
| **Retry Logic** | providers.py | 3 attempts, exponential backoff |
| **Request Caching** | main.py | 2-layer cache (exact + prompt) |
| **Cost Estimation** | main.py | Per-request cost calculated |
| **Route Tracking** | route_path field | Shows provider journey |
| **Smart Routing** | _select_provider_order() | Model name + intent keywords |
| **Request Logging** | RequestLog table | Endpoint, provider, latency logged |
| **Metrics Display** | dashboard.html | Request count, latency, success rate |
| **Database Integration** | main.py, RequestLog | PostgreSQL ORM with Alembic |

### ❌ MISSING / LIMITED FEATURES

| Feature | Impact | Complexity |
|---------|--------|-----------|
| **Request History API** | Can't query past requests from UI | Medium |
| **Provider Statistics** | No visibility into provider performance | Medium |
| **Cost Trending** | Only shows last request, not aggregate | Medium |
| **Detailed Route Reasons** | Doesn't show WHY a provider failed | Medium |
| **Per-Provider Latency** | Total latency only, not per-step | Medium |
| **Cache Statistics** | No hit/miss rate visibility | Low |
| **Request Search/Filter** | Can't filter logs by date, model, etc. | High |
| **Cost Breakdown Reports** | No cost analysis by provider/time | High |
| **Token Usage History** | Not tracked per request in logs | Low |
| **Fallback Failure Details** | Route shows "failed" but no error message | Medium |
| **Provider Availability Dashboard** | No real-time provider status | High |
| **Cost Alerts/Budget** | No spending limits or warnings | High |

---

## 9. WHERE CHANGES NEED TO BE MADE

### For Route Visualization (Enhanced)
**Files to modify**:
1. `app/router.py`:
   - Add error messages to RouteStep
   - Add timestamp and latency per step
   - Track which retry attempt succeeded

2. `app/schemas.py`:
   - Extend RouteStep to include:
     - `error_message: Optional[str]`
     - `latency_ms: Optional[int]`
     - `attempt_number: Optional[int]`
     - `timestamp: Optional[datetime]`

3. `app/dashboard.html`:
   - Enhance route panel to show failure details
   - Add timeline visualization with latencies

### For Cost Tracking & Trending
**Files to modify**:
1. `app/main.py`:
   - Create new DB model: `CostLog` (date, provider, cost, tokens)
   - Store cost breakdown after each request

2. Add new API endpoints:
   - `GET /cost/summary` - Total costs by provider
   - `GET /cost/history?days=7` - Time-series cost data
   - `GET /cost/breakdown?provider=openai` - Detailed breakdown

3. `app/dashboard.html`:
   - Add cost trending chart
   - Show cumulative costs vs. last request
   - Add cost breakdown by provider

### For Provider Details
**Files to add**:
1. `app/providers_stats.py` (new file):
   - Track per-provider success rates, avg latencies
   - Maintain rolling stats window

2. Add new API endpoints:
   - `GET /providers/stats` - Success rates, latencies, uptime
   - `GET /providers/{name}/health` - Real-time availability

3. `app/dashboard.html`:
   - Add provider status panel
   - Show real-time stats per provider

### For Request History
**Files to modify**:
1. `app/main.py`:
   - Add new endpoints:
     - `GET /requests` - Paginated request history
     - `GET /requests/{id}` - Single request details
     - `GET /requests/search?model=gpt-4&days=7` - Filtered search

2. `app/dashboard.html`:
   - Add request history sidebar
   - Show past requests with timestamps

---

## 10. KEY OBSERVATIONS

### Current Architecture Strengths
- ✅ Clean separation: providers.py (adapters), router.py (logic), main.py (API)
- ✅ Robust error handling with retry logic and fallbacks
- ✅ Two-layer caching strategy for performance
- ✅ API key authentication enforced
- ✅ Real-time health monitoring
- ✅ Cost estimation built-in from the start

### Current Limitations
- ⚠️ Dashboard shows real-time data only (no persistence of metrics)
- ⚠️ Route tracing shows final path but not detailed attempt history
- ⚠️ Cost tracking lacks trending and aggregation
- ⚠️ No visibility into why providers fail or retry behavior
- ⚠️ RequestLog table doesn't capture full routing journey
- ⚠️ Cache system lacks observability (no hit/miss stats)

### Recommended Next Steps
1. **Short-term**: Enhance route_path with failure reasons and latencies
2. **Medium-term**: Add historical cost tracking and analytics
3. **Long-term**: Build provider performance dashboard and cost optimization tools

