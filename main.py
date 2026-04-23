import argparse
import json
import sqlite3
from pathlib import Path
from langgraph.graph import START, END, StateGraph
DB = Path("demo.sqlite")
MEM = Path("memory.json")
DOMAINS = ["sales", "inventory", "customers", "finance", "support"]

def init_db(reset=False):
    if reset and DB.exists(): DB.unlink()
    groups = {
        "sales": "orders order_items products channels".split(), "inventory": "warehouses stock suppliers shipments".split(),
        "customers": "customers addresses segments feedback".split(), "finance": "invoices payments expenses budgets".split(),
        "support": "tickets agents kb_articles sla".split(),
    }
    schema = "id INTEGER PRIMARY KEY, name TEXT, status TEXT, amount REAL, total_amount REAL, channel_id INTEGER"
    with sqlite3.connect(DB) as con:
        cur = con.cursor()
        for d, names in groups.items():
            p = "customer" if d == "customers" else d
            for n in names: cur.execute(f"CREATE TABLE IF NOT EXISTS {p}_{n} ({schema})")
        cur.executescript(
            "INSERT OR REPLACE INTO sales_channels (id,name) VALUES (1,'Online'),(2,'Retail');"
            "INSERT OR REPLACE INTO sales_orders (id,name,channel_id,total_amount) VALUES (1,'Alice',1,1250),(2,'Bob',2,85),(3,'Cara',1,1200);"
            "INSERT OR REPLACE INTO support_tickets (id,name,status) VALUES (1,'Login issue','open'),(2,'Bug in checkout','closed'),(3,'Refund question','open');"
            "INSERT OR REPLACE INTO finance_invoices (id,name,amount,status) VALUES (1,'Alice',1250,'paid'),(2,'Bob',85,'unpaid');"
        )

def load_memory():
    if not MEM.exists(): return []
    try:
        x = json.loads(MEM.read_text())
        return x[-5:] if isinstance(x, list) else []
    except Exception:
        return []

def save_memory(q, a):
    h = load_memory(); h.append({"q": q, "a": a}); MEM.write_text(json.dumps(h[-5:], indent=2))

def rule_sql(q, d):
    q = q.lower()
    if d == "sales":
        if "total" in q and "channel" in q:
            return "SELECT c.name AS channel, ROUND(SUM(o.total_amount),2) AS total_sales FROM sales_orders o JOIN sales_channels c ON c.id=o.channel_id GROUP BY c.name ORDER BY total_sales DESC;"
        return "SELECT * FROM sales_orders LIMIT 5;"
    if d == "support":
        if "open" in q and "ticket" in q: return "SELECT status, COUNT(*) AS ticket_count FROM support_tickets GROUP BY status;"
        return "SELECT * FROM support_tickets LIMIT 5;"
    if d == "finance":
        if "unpaid" in q: return "SELECT * FROM finance_invoices WHERE status='unpaid';"
        return "SELECT * FROM finance_invoices LIMIT 5;"
    if d == "inventory": return "SELECT * FROM inventory_stock LIMIT 5;"
    if d == "customers": return "SELECT * FROM customer_customers LIMIT 5;"
    return ""

def select_domain(st):
    q, d = st.get("question", "").lower(), st.get("domain", "")
    if not d:
        for x in DOMAINS:
            if x in q: d = x; break
    if not d: return {**st, "intent": "need_domain", "answer": "Pick domain: sales/inventory/customers/finance/support"}
    return {**st, "domain": d}

def parse_intent(st):
    q = st.get("question", "").lower().strip()
    if q in ["hi", "hello", "hey", "yo"]: return {**st, "intent": "small", "answer": "Hi! Ask me a data question."}
    if "history" in q or "memory" in q:
        h = st.get("history", [])
        if not h: return {**st, "intent": "memory", "answer": "No memory yet."}
        s = "\n".join(f"{i+1}. {x.get('q','')} -> {x.get('a','')[:60]}" for i, x in enumerate(h))
        return {**st, "intent": "memory", "answer": s}
    if "list tables" in q or q == "tables": return {**st, "intent": "tables"}
    if "show table" in q: return {**st, "intent": "show"}
    return {**st, "intent": "query"}

def generate_sql(st):
    i, q, d = st.get("intent"), st.get("question", ""), st.get("domain", "")
    if i in ["small", "memory", "need_domain"]: return st
    if i == "tables":
        p = "customer" if d == "customers" else d
        return {**st, "sql": f"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '{p}%' ORDER BY name;"}
    if i == "show":
        tail = q.lower().split("show table", 1)
        t = tail[1].strip().split(" ")[0] if len(tail) > 1 and tail[1].strip() else ""
        return {**st, "sql": f"SELECT * FROM {t} LIMIT 5;"} if t else {**st, "error": "table name not found", "sql": ""}
    sql = rule_sql(q, d)
    return {**st, "sql": sql} if sql else {**st, "error": "sql generation failed", "sql": ""}

def validate_execute(st):
    sql = st.get("sql", "").strip()
    if not sql: return {**st, "error": st.get("error", "no sql")}
    lo = sql.lower()
    if not lo.startswith("select"): return {**st, "error": "query must start with SELECT"}
    if any(x in lo for x in ["drop ", "delete ", "insert ", "update ", "alter ", "pragma "]): return {**st, "error": "only SELECT allowed"}
    try:
        with sqlite3.connect(DB) as con:
            con.row_factory = sqlite3.Row
            rows = [dict(x) for x in con.execute(sql).fetchall()]
    except Exception as e:
        return {**st, "error": str(e), "retry": st.get("retry", 0) + 1}
    if not rows: return {**st, "rows": [], "answer": "No rows", "error": ""}
    head = ", ".join(rows[0].keys())
    body = [", ".join(str(v) for v in r.values()) for r in rows[:5]]
    return {**st, "rows": rows, "answer": "\n".join([head] + body), "error": ""}

def app_build():
    g = StateGraph(dict)
    g.add_node("select_domain", select_domain)
    g.add_node("parse_intent", parse_intent)
    g.add_node("generate_sql", generate_sql)
    g.add_node("validate_execute", validate_execute)
    g.add_edge(START, "select_domain")
    g.add_edge("select_domain", "parse_intent")
    g.add_conditional_edges("parse_intent", lambda s: "done" if s.get("intent") in ["need_domain", "small", "memory"] else "go", {"done": END, "go": "generate_sql"})
    g.add_conditional_edges("generate_sql", lambda s: "fail" if not s.get("sql") else "go", {"fail": END, "go": "validate_execute"})
    g.add_conditional_edges("validate_execute", lambda s: "retry" if s.get("error") and s.get("retry", 0) < 1 else "done", {"retry": "generate_sql", "done": END})
    return g.compile()
APP = app_build()

def run_one(q, d):
    out = APP.invoke({"question": q, "domain": d, "history": load_memory(), "retry": 0})
    save_memory(q, out.get("answer", out.get("error", "")))
    return out

def run_demo():
    tests = [("sales", "what is total sales by channel"), ("support", "list open tickets"), ("finance", "hello")]
    for i, (d, q) in enumerate(tests, 1):
        print(f"\n--- Example {i} ---")
        print("Domain:", d)
        print("Q:", q)
        o = run_one(q, d)
        print("SQL:", o.get("sql", "(none)"))
        print("Answer:\n" + (o.get("answer") or o.get("error") or "no output"))

def run_chat():
    print("Domains:", ", ".join(DOMAINS))
    try:
        d = input("Pick domain: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\nBye")
        return
    if d not in DOMAINS: d = "sales"
    print("type: quit | history | tables | show table <name> | normal question")
    while True:
        try:
            q = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye")
            break
        if q.lower() in ["quit", "exit"]: break
        if q.lower().startswith("domain "):
            x = q.lower().replace("domain ", "").strip()
            if x in DOMAINS:
                d = x
                print("domain changed to", d)
                continue
        o = run_one(q, d)
        if o.get("sql"): print("SQL:", o.get("sql"))
        print("Agent:", o.get("answer") or o.get("error") or "no output")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--reset-db", action="store_true")
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    init_db(reset=a.reset_db)
    run_demo() if a.demo else run_chat()
if __name__ == "__main__": main()
