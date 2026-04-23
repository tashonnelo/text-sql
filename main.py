import argparse
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

try:
    import matplotlib.pyplot as plt
except Exception:
    plt = None


DB = Path("demo.sqlite")
MEM = Path("memory.json")
CHARTS = Path("charts")
CHARTS.mkdir(exist_ok=True)

DOMAINS = {
    "sales": ["sales_orders", "sales_order_items", "sales_products", "sales_channels"],
    "inventory": ["inventory_warehouses", "inventory_stock", "inventory_suppliers", "inventory_shipments"],
    "customers": ["customer_customers", "customer_addresses", "customer_segments", "customer_feedback"],
    "finance": ["finance_invoices", "finance_payments", "finance_expenses", "finance_budgets"],
    "support": ["support_tickets", "support_agents", "support_kb_articles", "support_sla"],
}

TABLE_SCHEMA = "id INTEGER PRIMARY KEY, name TEXT, status TEXT, amount REAL, total_amount REAL, channel_id INTEGER"
SEED_SQL = (
    "INSERT OR REPLACE INTO sales_channels (id,name) VALUES (1,'Online'),(2,'Retail');"
    "INSERT OR REPLACE INTO sales_orders (id,name,channel_id,total_amount) VALUES (1,'Alice',1,1250),(2,'Bob',2,85),(3,'Cara',1,1200);"
    "INSERT OR REPLACE INTO support_tickets (id,name,status) VALUES (1,'Login issue','open'),(2,'Bug in checkout','closed'),(3,'Refund question','open');"
    "INSERT OR REPLACE INTO finance_invoices (id,name,amount,status) VALUES (1,'Alice',1250,'paid'),(2,'Bob',85,'unpaid');"
)

MERMAID = """flowchart TD
    A([Start]) --> B[select_domain]
    B --> C[parse_intent]
    C -->|greeting/memory/no domain| Z([End])
    C -->|query| D[generate_sql]
    D -->|failed| F[return error]
    D -->|ok| E[validate_execute]
    E -->|sql/runtime error + retry| D
    E -->|done| Z
    F --> Z
"""


class S(TypedDict, total=False):
    question: str
    domain: str
    intent: str
    sql: str
    rows: list[dict[str, Any]]
    answer: str
    error: str
    wants_chart: bool
    chart: str
    history: list[dict[str, str]]
    retry: int


def init_db(reset: bool = False) -> None:
    if reset and DB.exists(): DB.unlink()
    all_tables = [t for group in DOMAINS.values() for t in group]
    with sqlite3.connect(DB) as con:
        cur = con.cursor()
        for t in all_tables: cur.execute(f"CREATE TABLE IF NOT EXISTS {t} ({TABLE_SCHEMA})")
        cur.executescript(SEED_SQL)


def load_memory() -> list[dict[str, str]]:
    if not MEM.exists():
        return []
    try:
        data = json.loads(MEM.read_text())
        return data[-5:] if isinstance(data, list) else []
    except Exception:
        return []


def save_memory(q: str, a: str) -> None:
    old = load_memory()
    old.append({"q": q, "a": a})
    MEM.write_text(json.dumps(old[-5:], indent=2))


def clean_sql(x: str) -> str:
    x = x.replace("```sql", "").replace("```", "").strip()
    if x and not x.endswith(";"):
        x += ";"
    return x


def rule_sql(q: str, d: str) -> str:
    q = q.lower()
    if d == "sales":
        if "total" in q and "channel" in q:
            return "SELECT c.name AS channel, ROUND(SUM(o.total_amount),2) AS total_sales FROM sales_orders o JOIN sales_channels c ON c.id=o.channel_id GROUP BY c.name ORDER BY total_sales DESC;"
        return "SELECT * FROM sales_orders LIMIT 5;"
    if d == "support":
        if "open" in q and "ticket" in q:
            return "SELECT status, COUNT(*) AS ticket_count FROM support_tickets GROUP BY status;"
        return "SELECT * FROM support_tickets LIMIT 5;"
    if d == "finance":
        if "unpaid" in q:
            return "SELECT * FROM finance_invoices WHERE status='unpaid';"
        return "SELECT * FROM finance_invoices LIMIT 5;"
    if d == "inventory":
        return "SELECT * FROM inventory_stock LIMIT 5;"
    if d == "customers":
        return "SELECT * FROM customer_customers LIMIT 5;"
    return ""


def llm_sql(q: str, d: str) -> str:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return rule_sql(q, d)
    try:
        from openai import OpenAI

        client = OpenAI(api_key=key)
        txt = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            input=f"Return only one SQLite SELECT query for domain {d} using tables {DOMAINS[d]}. Question: {q}",
        ).output_text
        return clean_sql(txt)
    except Exception:
        return rule_sql(q, d)


def chart(rows: list[dict[str, Any]]) -> str:
    if not plt or not rows:
        return ""
    ks = list(rows[0].keys())
    if len(ks) < 2:
        return ""
    xk, yk = ks[0], ks[1]
    xs, ys = [], []
    for r in rows:
        if isinstance(r.get(yk), (int, float)):
            xs.append(str(r.get(xk)))
            ys.append(float(r.get(yk)))
    if not xs:
        return ""
    p = CHARTS / f"chart_{int(time.time())}.png"
    plt.figure(figsize=(6, 3))
    plt.bar(xs, ys)
    plt.tight_layout()
    plt.savefig(p)
    plt.close()
    return str(p)


def select_domain(st: S) -> S:
    q = st.get("question", "").lower()
    d = st.get("domain", "")
    if not d:
        for x in DOMAINS:
            if x in q:
                d = x
    if not d:
        return {**st, "intent": "need_domain", "answer": "Pick domain first: sales/inventory/customers/finance/support"}
    return {**st, "domain": d}


def parse_intent(st: S) -> S:
    q = st.get("question", "").lower().strip()
    if q in ["hi", "hello", "hey"]:
        return {**st, "intent": "small", "answer": "Hi! Ask me a data question."}
    if "history" in q or "memory" in q:
        h = st.get("history", [])
        if not h:
            return {**st, "intent": "memory", "answer": "No memory yet."}
        s = "\n".join([f"{i+1}. {x['q']} -> {x['a'][:70]}" for i, x in enumerate(h)])
        return {**st, "intent": "memory", "answer": s}
    if "list tables" in q or q == "tables":
        return {**st, "intent": "tables", "wants_chart": False}
    if "show table" in q:
        return {**st, "intent": "show", "wants_chart": False}
    return {**st, "intent": "query", "wants_chart": ("chart" in q or "plot" in q or "graph" in q)}


def generate_sql(st: S) -> S:
    i = st.get("intent")
    q = st.get("question", "")
    d = st.get("domain", "")
    if i in ["small", "memory", "need_domain"]:
        return st
    if i == "tables":
        return {**st, "sql": f"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '{d}%' ORDER BY name;"}
    if i == "show":
        ql = q.lower()
        for t in DOMAINS.get(d, []):
            if t in ql:
                return {**st, "sql": f"SELECT * FROM {t} LIMIT 5;"}
        return {**st, "error": "table name not found", "sql": ""}
    sql = llm_sql(q, d)
    if not sql:
        return {**st, "error": "sql generation failed", "sql": ""}
    return {**st, "sql": sql}


def validate_execute(st: S) -> S:
    sql = st.get("sql", "").strip()
    if not sql:
        return {**st, "error": st.get("error", "no sql")}
    lo = sql.lower()
    if not lo.startswith("select"):
        return {**st, "error": "query must start with SELECT"}
    if any(x in lo for x in ["drop ", "delete ", "insert ", "update ", "alter "]):
        return {**st, "error": "only SELECT allowed"}

    try:
        with sqlite3.connect(DB) as con:
            con.row_factory = sqlite3.Row
            rows = [dict(x) for x in con.execute(sql).fetchall()]
    except Exception as e:
        return {**st, "error": str(e), "retry": st.get("retry", 0) + 1}

    if not rows:
        ans = "No rows"
    else:
        head = ", ".join(rows[0].keys())
        body = [", ".join(str(v) for v in r.values()) for r in rows[:5]]
        ans = "\n".join([head] + body)

    cp = ""
    if st.get("wants_chart"):
        cp = chart(rows)
        if cp:
            ans += f"\nChart: {cp}"

    return {**st, "rows": rows, "answer": ans, "error": "", "chart": cp}


def r1(st: S) -> str:
    return "done" if st.get("intent") in ["need_domain", "small", "memory"] else "go"


def r2(st: S) -> str:
    return "fail" if not st.get("sql") else "go"


def r3(st: S) -> str:
    return "retry" if st.get("error") and st.get("retry", 0) < 1 else "done"


def app_build():
    g = StateGraph(S)
    g.add_node("select_domain", select_domain)
    g.add_node("parse_intent", parse_intent)
    g.add_node("generate_sql", generate_sql)
    g.add_node("validate_execute", validate_execute)

    g.add_edge(START, "select_domain")
    g.add_edge("select_domain", "parse_intent")
    g.add_conditional_edges("parse_intent", r1, {"done": END, "go": "generate_sql"})
    g.add_conditional_edges("generate_sql", r2, {"fail": END, "go": "validate_execute"})
    g.add_conditional_edges("validate_execute", r3, {"retry": "generate_sql", "done": END})
    return g.compile()


def run_one(q: str, d: str) -> S:
    out = app_build().invoke({"question": q, "domain": d, "history": load_memory(), "retry": 0})
    save_memory(q, out.get("answer", out.get("error", "")))
    return out


def run_demo() -> None:
    print("\nMermaid diagram:\n")
    print(MERMAID)

    tests = [
        ("sales", "what is total sales by channel and make a chart"),
        ("support", "list open tickets"),
        ("finance", "hello"),
    ]
    for i, (d, q) in enumerate(tests, 1):
        print(f"\n--- Example {i} ---")
        print("Domain:", d)
        print("Q:", q)
        o = run_one(q, d)
        print("SQL:", o.get("sql", "(none)"))
        print("Answer:\n" + (o.get("answer") or o.get("error") or "no output"))


def run_chat() -> None:
    print("Domains:", ", ".join(DOMAINS.keys()))
    try:
        d = input("Pick domain: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\nBye")
        return
    if d not in DOMAINS:
        d = "sales"
    print("type: quit | history | list tables | show table <name> | normal question")
    while True:
        try:
            q = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye")
            break
        if q.lower() in ["quit", "exit"]:
            break
        if q.lower().startswith("domain "):
            maybe = q.lower().replace("domain ", "").strip()
            if maybe in DOMAINS:
                d = maybe
                print("domain changed to", d)
                continue
        o = run_one(q, d)
        if o.get("sql"):
            print("SQL:", o.get("sql"))
        print("Agent:", o.get("answer") or o.get("error") or "no output")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--reset-db", action="store_true")
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    init_db(reset=a.reset_db)
    if a.demo:
        run_demo()
    else:
        run_chat()


if __name__ == "__main__":
    main()
