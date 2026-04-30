"""Custom CSS for the dashboard — modern, dark, professional."""

CUSTOM_CSS = """
<style>
    /* Hide Streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    /* Color palette */
    :root {
        --bg-primary: #0e1117;
        --bg-secondary: #1a1d26;
        --bg-tertiary: #242833;
        --border: #2d3142;
        --text-primary: #ffffff;
        --text-secondary: #8b92a0;
        --text-muted: #5a6172;
        --success: #00d97e;
        --danger: #ff5b5b;
        --warning: #ffa726;
        --info: #5e72e4;
        --accent: #a78bfa;
        --gradient: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    }

    /* Body */
    .stApp {
        background-color: var(--bg-primary);
        color: var(--text-primary);
    }

    /* Main container */
    .main .block-container {
        padding-top: 1rem;
        padding-bottom: 2rem;
        max-width: 1400px;
    }

    /* Sidebar */
    section[data-testid="stSidebar"] {
        background-color: var(--bg-secondary);
        border-right: 1px solid var(--border);
    }

    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3 {
        color: var(--text-primary);
    }

    /* KPI Card */
    .kpi-card {
        background: var(--bg-secondary);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 20px 24px;
        height: 100%;
        transition: transform 0.2s, border-color 0.2s;
    }
    .kpi-card:hover {
        transform: translateY(-2px);
        border-color: var(--accent);
    }
    .kpi-label {
        color: var(--text-secondary);
        font-size: 12px;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 8px;
    }
    .kpi-value {
        color: var(--text-primary);
        font-size: 22px;
        font-weight: 700;
        line-height: 1.2;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .kpi-delta {
        font-size: 13px;
        font-weight: 500;
        margin-top: 6px;
    }
    .kpi-delta.positive { color: var(--success); }
    .kpi-delta.negative { color: var(--danger); }
    .kpi-delta.neutral  { color: var(--text-secondary); }

    /* Section Card */
    .section-card {
        background: var(--bg-secondary);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 20px 24px;
        margin-bottom: 16px;
    }
    .section-title {
        color: var(--text-primary);
        font-size: 16px;
        font-weight: 600;
        margin-bottom: 16px;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .section-subtitle {
        color: var(--text-secondary);
        font-size: 13px;
        font-weight: 400;
    }

    /* Status Badge */
    .badge {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }
    .badge-success { background: rgba(0, 217, 126, 0.15); color: var(--success); }
    .badge-danger  { background: rgba(255, 91, 91, 0.15); color: var(--danger); }
    .badge-warning { background: rgba(255, 167, 38, 0.15); color: var(--warning); }
    .badge-info    { background: rgba(94, 114, 228, 0.15); color: var(--info); }
    .badge-mode-paper { background: rgba(167, 139, 250, 0.15); color: var(--accent); }
    .badge-mode-live  { background: rgba(0, 217, 126, 0.15); color: var(--success); }

    /* Agent Activity Feed */
    .activity-feed { max-height: 480px; overflow-y: auto; padding-right: 8px; }
    .activity-item {
        background: var(--bg-tertiary);
        border-left: 3px solid var(--info);
        border-radius: 6px;
        padding: 10px 14px;
        margin-bottom: 8px;
        font-size: 13px;
    }
    .activity-item.success { border-left-color: var(--success); }
    .activity-item.warning { border-left-color: var(--warning); }
    .activity-item.error   { border-left-color: var(--danger); }
    .activity-meta { color: var(--text-muted); font-size: 11px; margin-bottom: 4px; }
    .activity-agent {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 4px;
        background: var(--bg-secondary);
        color: var(--accent);
        font-size: 10px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-right: 6px;
    }
    .activity-text { color: var(--text-primary); line-height: 1.5; }
    .activity-symbol {
        color: var(--info);
        font-weight: 600;
    }

    /* Header */
    .app-header {
        background: var(--gradient);
        border-radius: 12px;
        padding: 24px 32px;
        margin-bottom: 24px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    .app-header h1 {
        color: white;
        margin: 0;
        font-size: 24px;
        font-weight: 700;
    }
    .app-header p {
        color: rgba(255, 255, 255, 0.85);
        margin: 4px 0 0 0;
        font-size: 14px;
    }

    /* Tables */
    .stDataFrame {
        border: 1px solid var(--border);
        border-radius: 8px;
    }

    /* Divider */
    hr {
        border-color: var(--border) !important;
        margin: 16px 0 !important;
    }

    /* Tabs */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background: transparent;
    }
    .stTabs [data-baseweb="tab"] {
        background: var(--bg-secondary);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 8px 16px;
        color: var(--text-secondary);
    }
    .stTabs [aria-selected="true"] {
        background: var(--bg-tertiary) !important;
        color: var(--text-primary) !important;
        border-color: var(--accent) !important;
    }

    /* Buttons */
    .stButton > button {
        background: var(--bg-tertiary);
        color: var(--text-primary);
        border: 1px solid var(--border);
        border-radius: 8px;
        font-weight: 500;
        transition: all 0.2s;
    }
    .stButton > button:hover {
        background: var(--accent);
        border-color: var(--accent);
        color: white;
    }

    /* Metric */
    [data-testid="stMetricValue"] {
        color: var(--text-primary) !important;
        font-weight: 700;
    }
    [data-testid="stMetricLabel"] {
        color: var(--text-secondary) !important;
    }
</style>
"""
