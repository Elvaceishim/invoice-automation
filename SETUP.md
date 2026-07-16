# Setup

## 1. Generate the test invoices

```bash
cd data-generator
pip3 install reportlab --break-system-packages
python3 generate_invoices.py --count 30 --seed 42
```

Produces 30 PDF invoices in `data-generator/output/invoices/`, ground truth per invoice in `data-generator/output/ground_truth/`, and a combined `manifest.json`.

## 2. Start the full stack

```bash
export OPENROUTER_API_KEY=sk-or-v1-your-key-here
docker compose up -d --build
```

This builds and starts four containers: Postgres, the extraction service, the validation service, and n8n. Confirm everything's healthy:

```bash
docker compose ps
```

## 3. Verify each service directly before touching n8n

```bash
curl http://localhost:8002/health   # extraction
curl http://localhost:8003/health   # validation
```

## 4. Set up n8n

Visit `http://localhost:5679` (note the port — this stack runs on 5679/5433, not n8n's usual 5678/5432, so it doesn't collide with any other n8n stack you might have running).

Add a **Postgres** credential: host `postgres`, database `n8n`, user `n8n`, password `n8n_local_pw`, port `5432`, SSL off.

Add a **Telegram** credential: message [@BotFather](https://t.me/BotFather), `/newbot`, copy the token. Get your chat ID by messaging your bot anything, then visiting `https://api.telegram.org/bot<TOKEN>/getUpdates` and reading `chat.id` from the response.

Import `workflows/invoice-pipeline.json`. Check every Postgres and Telegram node for a red warning icon (credential didn't carry over from import) and re-link. Set the real chat ID in the "Send Review Alert" node.

**Check every node individually isn't disabled** — right-click each one and confirm there's no "(Deactivated)" label, since this is a real, silent failure mode that cost real debugging time the first time around.

## 5. Test it end to end

```bash
curl -X POST -F "file=@data-generator/output/invoices/invoice_0001.pdf" http://localhost:5679/webhook/invoice-intake
```

Then check the database directly — this is the real source of truth, not the curl response alone:

```bash
docker exec -it invoice-automation-postgres-1 psql -U n8n -d n8n -c "SELECT vendor_name, outcome, flags FROM invoices ORDER BY processed_at DESC LIMIT 5;"
```

Try a scenario that should get flagged (e.g. `invoice_0007.pdf`, an unapproved-vendor case) and confirm you get a Telegram alert.

## 6. Run the full accuracy evaluations

Extraction accuracy against all 30 invoices:
```bash
cd extraction-service
python3 evaluate_extraction.py
```

End-to-end pipeline accuracy (extraction + validation combined), using the real results from the step above:
```bash
cd ../validation-service
python3 evaluate_pipeline.py
```

## Useful commands while debugging

Check the dead-letter table for anything that failed silently:
```bash
docker exec -it invoice-automation-postgres-1 psql -U n8n -d n8n -c "SELECT stage, error_message, created_at FROM invoice_dead_letter ORDER BY created_at DESC LIMIT 5;"
```

Rebuild a single service after changing its code:
```bash
docker compose up -d --build invoice-extraction
docker compose up -d --build invoice-validation
```
