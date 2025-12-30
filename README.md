# Virtus AI 🏃‍♂️🤖
**Performance Intelligence & Coaching System**

Virtus AI is an end-to-end endurance training intelligence platform that combines **objective training load analytics** with an **LLM-powered virtual coach**. It ingests real athlete data (Strava), models fatigue/fitness, and provides actionable coaching insights, session recommendations, and long-term planning via both dashboards and chat.

---

## 🚀 Features

### 1. Training Load Intelligence
- CTL / ATL / TSB modeling (EWMA-based)
- Daily, weekly, and rolling volume analytics
- Fatigue & readiness indicators
- Automatic trend detection (load spikes, volatility, recovery windows)

### 2. Virtus Coach (LLM-Powered)
- Snapshot coaching insights (state-based)
- Natural language chat with intent routing
- Tool-based reasoning (no hallucinated metrics)
- Deterministic + LLM hybrid architecture

### 3. Coach Capabilities (Tier 1 – MVP)
- **Recommend next session**
- **Explain current training state**
- **Adjust training load from feedback**
- **Plan race build (5km → 100 miles)**
- **Plan full season**

### 4. Real-Time Dashboard (Streamlit)
- Interactive charts (Altair)
- KPI cards (readiness, recovery, load)
- Coach panel + chat interface
- Strava sync status

---

## 🧠 Architecture Overview

```

Strava → Ingestion → SQLite
↓
Training Load Engine
↓
Athlete State Builder
↓
┌───────────────┐
│ Virtus Coach  │
│  (LLM + Tools)│
└───────────────┘
↓
FastAPI Backend (JSON)
↓
Streamlit UI (Chat + Viz)

```

---

## 🛠️ Tech Stack

**Backend**
- Python 3.12+
- FastAPI
- SQLAlchemy (SQLite)
- Loguru
- OpenAI / LangChain
- Pydantic v2

**Frontend**
- Streamlit
- Altair
- Pandas

**Infra**
- Render (deployment)
- Strava API
- uvicorn

---

## 📂 Project Structure

```

app/
├── api/
│   ├── state.py        # Training load + coach snapshot
│   ├── coach.py        # Chat endpoint
│
├── coach/
│   ├── agent.py        # LLM coach (snapshot)
│   ├── service.py     # Agent execution
│   ├── tools/         # Tool functions (next session, plan race, etc.)
│   ├── chat_utils/
│   │   ├── intent_router.py
│   │   └── dispatcher.py
│
├── state/
│   ├── db.py
│   └── state_builder.py
│
ui/
└── app.py              # Streamlit UI

````

---

## 🧩 Coach Chat – Intent Routing

User messages are routed to deterministic tools:

| Intent | Example |
|------|--------|
| NEXT_SESSION | “Recommend today’s workout” |
| EXPLAIN_STATE | “Why do I feel tired?” |
| ADJUST_LOAD | “Yesterday felt too hard” |
| PLAN_RACE | “Build me to a marathon” |
| PLAN_SEASON | “Plan my year” |

LLM is used for:
- Intent classification
- Natural language generation
- Planning narratives

Metrics always come from code.

---

## ▶️ Running Locally

### 1. Backend
```bash
export OPENAI_API_KEY=sk-...
uvicorn app.main:app --reload
````

### 2. UI

```bash
streamlit run ui/app.py
```

---

## ☁️ Deployment (Render)

**Start Command**

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

**Important**

* Entry point is `app/main.py`
* Not `main.py` at root

---

## 🔐 Environment Variables

```
OPENAI_API_KEY=...
STRAVA_CLIENT_ID=...
STRAVA_CLIENT_SECRET=...
DATABASE_URL=sqlite:///./virtus.db
```

---

## 📈 Roadmap

### Tier 2

* Training plan calendar export
* Adaptive mesocycles
* Race-specific pacing models
* Injury risk modeling

### Tier 3

* Multimodal inputs (HRV, sleep)
* Athlete memory & preferences
* Coach personas
* Team / coach dashboards

---

## 🧠 Philosophy

> **“Metrics are computed. Advice is reasoned. Coaching is earned.”**

Virtus AI never guesses fitness.
It measures → reasons → explains → adapts.

---

## 📄 License

Private / Proprietary

---

## 👋 Author
Built by **Aptus Initiatives**