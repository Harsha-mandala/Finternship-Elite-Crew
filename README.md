# 🏨 Hotel Aditya Grand — AI Smart Order Assistant

> **AI-powered demand forecasting & inventory orchestrator for a high-volume restaurant in Kandukur, Andhra Pradesh.**
> Eliminates daily inventory guesswork by learning from past sales, weather, and local festivals to recommend next-day purchase quantities.

[![Live App](https://img.shields.io/badge/Live%20App-GitHub%20Pages-blue?style=flat-square&logo=github)](https://harsha-mandala.github.io/Finternship-Elite-Crew/)
[![Backend](https://img.shields.io/badge/Backend-Render-46E3B7?style=flat-square&logo=render)](https://finternship-elite-crew.onrender.com)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)

---

## 📌 Problem Statement

Hotel Aditya Grand, a high-volume restaurant in Kandukur, was losing **₹4,000–₹6,000 per week** due to over-ordering and ingredient spoilage. Every morning, the owner made stock purchase calls based purely on memory — no data, no system. This project replaces that guesswork with an AI-powered daily recommendation engine.

---

## 🚀 Live Demo

| Resource | Link |
|---|---|
| 🌐 Web App | https://harsha-mandala.github.io/Finternship-Elite-Crew/ |
| ⚙️ API Docs | https://finternship-elite-crew.onrender.com/docs |
| 📦 GitHub Repo | https://github.com/Harsha-mandala/Finternship-Elite-Crew |

---

## ✨ Key Features

- **📄 PDF OCR Ingestion** — Upload daily billing PDFs; Google Gemini AI extracts item names, quantities, and revenue automatically
- **🤖 LightGBM Forecasting** — Per-item next-day quantity predictions using lag features, rolling statistics, and trend slopes
- **🌦️ Weather Enrichment** — Real-time OpenWeatherMap integration adjusts predictions (rain → more hot soups; heat → more beverages)
- **🎉 Festival Calendar** — Regional festival demand multipliers for Bonalu, Ugadi, Diwali, and 20+ local events
- **📊 Analytics Dashboard** — Revenue trends, category breakdowns, and item-level deep dives with Chart.js
- **🔁 Merchant Override** — Owner can manually override any AI recommendation before finalising the order
- **📱 Mobile-First PWA** — Installable, responsive single-page app that works on any device

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Frontend (GitHub Pages)                │
│         Vanilla JS SPA · Chart.js · Mobile-first        │
└────────────────────┬────────────────────────────────────┘
                     │  REST API calls
┌────────────────────▼────────────────────────────────────┐
│                 Backend (Render · FastAPI)               │
│                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  Gemini OCR │  │  ML Engine   │  │  Recommender  │  │
│  │  PDF Parser │  │  LightGBM    │  │  Fusion Layer │  │
│  └─────────────┘  └──────────────┘  └───────────────┘  │
│                                                         │
│  ┌─────────────┐  ┌──────────────┐                      │
│  │   Weather   │  │   Festival   │                      │
│  │  API Client │  │   Calendar   │                      │
│  └─────────────┘  └──────────────┘                      │
└────────────────────┬────────────────────────────────────┘
                     │  psycopg2 · Transaction Pooler
┌────────────────────▼────────────────────────────────────┐
│            Database (Supabase · PostgreSQL)              │
│        daily_sales · recommendations · menu_items       │
└─────────────────────────────────────────────────────────┘
```

---

## 🧠 Recommendation Formula

The final order quantity for each item is calculated as:

```
R_final = P_ML × F_Weather × F_Festival × F_Trend
```

| Factor | Source | Example |
|---|---|---|
| `P_ML` | LightGBM prediction | 52 units of Sp. Chicken Biryani |
| `F_Weather` | OpenWeatherMap API | 1.15× on rainy days |
| `F_Festival` | Regional calendar | 1.30× during Bonalu |
| `F_Trend` | 7-day slope | 0.95× if declining trend |

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | Vanilla JavaScript, HTML5, CSS3, Chart.js |
| **Backend** | Python 3.11, FastAPI, Uvicorn |
| **ML Model** | LightGBM Regressor, scikit-learn, pandas |
| **OCR Engine** | Google Gemini API (5-model fallback chain) |
| **Database** | PostgreSQL (Supabase), SQLite (local dev) |
| **External APIs** | OpenWeatherMap, Google Gemini |
| **Deployment** | Render (backend), GitHub Pages (frontend) |

---

## 📁 Project Structure

```
Finternship/
├── backend/
│   ├── main.py                 # FastAPI app, all API routes
│   ├── database.py             # DB connection, SQL translator, schema init
│   ├── requirements.txt
│   └── engine/
│       ├── recommender.py      # Recommendation fusion engine
│       ├── ml_engine.py        # LightGBM train/predict
│       ├── feature_builder.py  # Feature engineering pipeline
│       ├── gemini_ocr.py       # PDF OCR with Gemini fallback chain
│       ├── weather_service.py  # OpenWeatherMap integration
│       └── festival_service.py # Regional festival calendar
├── frontend/
│   ├── index.html
│   ├── css/
│   └── js/
│       ├── app.js              # SPA router, health check, keepalive
│       ├── api.js              # All API calls to backend
│       ├── dashboard.js        # Dashboard screen
│       ├── recommendations.js  # Orders/predictions screen
│       ├── log-sales.js        # PDF upload & sales logging
│       ├── trends.js           # Analytics & charts
│       └── settings.js         # API keys, model retraining
└── docs/                       # GitHub Pages deploy source
```

---

## ⚙️ Local Setup

### Prerequisites
- Python 3.11+
- A Supabase project (or use local SQLite — auto-detected)
- Google Gemini API key
- OpenWeatherMap API key

### Backend

```bash
cd backend
pip install -r requirements.txt

# Set environment variables
export GEMINI_API_KEY=your_key_here
export OPENWEATHER_API_KEY=your_key_here
export DATABASE_URL=postgresql://...   # optional; uses SQLite if not set

uvicorn main:app --reload --port 8000
```

### Frontend

```bash
# No build step needed — pure HTML/JS/CSS
cd frontend
python -m http.server 3000
# Then open http://localhost:3000
```

Update `frontend/js/config.js` to point to your local backend:

```js
window.BACKEND_URL = 'http://localhost:8000';
```

---

## 🔑 Environment Variables

| Variable | Description | Required |
|---|---|---|
| `GEMINI_API_KEY` | Google Gemini API key for PDF OCR | Yes |
| `OPENWEATHER_API_KEY` | OpenWeatherMap API key | Yes |
| `DATABASE_URL` | PostgreSQL connection string (Supabase) | No (falls back to SQLite) |

---

## 📊 ML Model Details

| Property | Value |
|---|---|
| Algorithm | LightGBM Regressor |
| Training frequency | On-demand (triggered from Settings page) |
| Features | Lag-1, Lag-7, 7-day median, 14-day mean, DOW, month, weekend flag, trend slope |
| Target | `qty_sold` per item per day |
| Cold-start fallback | Rule-based baseline with DOW + weather + festival multipliers |

---

## 🤖 Gemini OCR Fallback Chain

When a model hits its rate limit (HTTP 429), the system automatically falls back in order:

```
1. gemini-3.5-flash        ← Highest priority
2. gemini-3.0-flash
3. gemini-3.1-flash-lite
4. gemini-2.5-flash
5. gemini-2.5-flash-lite   ← Final fallback
```

---

## 👥 Team

**Finternship Elite Crew** — Built during the OkCredit Finternship Program, 2026

---

## 📄 License

MIT License
