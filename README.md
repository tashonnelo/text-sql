# Very Basic Text-to-SQL (Single File)

Everything is in one file: `main.py`.

What it shows:
- LangGraph workflow
- Nodes: `parse_intent`, `generate_sql`, `validate_execute`
- Domain first (5 domains, 20 tables total)
- Small talk (`hi`, `hello`)
- Memory of last 5 chats
- List tables and show table data
- Chart output for numeric results
- Mermaid flow string printed in demo mode

## Install

```bash
pip install langgraph matplotlib openai
```

## Run demo

```bash
python main.py --reset-db --demo
```

## Run chat

```bash
python main.py --reset-db
```

## Optional LLM mode

If you set an API key, SQL generation will try real LLM first and fallback to rule-based SQL.

```bash
export OPENAI_API_KEY=your_key
export OPENAI_MODEL=gpt-4o-mini
```

## Example prompts

- hello
- list tables
- show table sales_orders
- what is total sales by channel and make a chart
- history
- domain support
