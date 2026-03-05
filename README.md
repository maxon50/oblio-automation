# Automatizare Oblio: apasa "Emite factura" pentru incasarile Stripe de ieri

Acest proiect foloseste browser automation (Playwright), nu API Stripe.
Scriptul:
1. se logheaza in Oblio,
2. intra in `Rapoarte > Stripe`,
3. cauta incasarile din ziua anterioara,
4. apasa `Emite factura` doar pe randurile fara factura deja emisa.

## Fisiere

- `main.py`
- `requirements.txt`
- `.env.example`

## 1) Pregatire Droplet (Ubuntu)

Conecteaza-te pe server:

```bash
ssh root@IP_DROPLET
```

Instaleaza pachete de baza:

```bash
apt update
apt install -y python3 python3-venv python3-pip git
```

## 2) Copiaza proiectul pe server

```bash
mkdir -p /opt/oblio-emitere
cd /opt/oblio-emitere
# daca folosesti git:
# git clone <repo_url> .
```

## 3) Creeaza mediu Python + dependinte

```bash
cd /opt/oblio-emitere
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
python -m playwright install-deps chromium
```

## 4) Configureaza variabilele

```bash
cp .env.example .env
nano .env
```

Completeaza:
- `OBLIO_EMAIL`
- `OBLIO_PASSWORD`
- optional restul (defaulturile sunt ok in general)

Variabile utile pentru stabilitate:
- `LOGIN_RETRIES` (default `3`)
- `EMIT_RETRIES` (default `2`)
- `RUN_RETRIES` (default `2`)
- `RETRY_DELAY_SECONDS` (default `5`)

Alerta webhook (optional):
- `ALERT_WEBHOOK_URL` = URL webhook (Slack/Discord/Make/Zapier)
- `ALERT_ON_SUCCESS` = `1` trimite si rezumat OK, `0` doar la fail

## 5) Test manual

Test fara emitere efectiva:

```bash
cd /opt/oblio-emitere
source .venv/bin/activate
python main.py --dry-run
```

Test cu emitere:

```bash
python main.py
```

Test pentru data fixa:

```bash
python main.py --date 2026-03-04
```

La finalul rularii, daca `ALERT_WEBHOOK_URL` e setat:
- trimite mesaj `OK` cu sumarul;
- trimite mesaj `FAIL` daca toate retry-urile au esuat.

## 6) Programare zilnica (cron)

Deschide crontab:

```bash
crontab -e
```

Exemplu: ruleaza zilnic la 07:05 (ora serverului):

```cron
5 7 * * * cd /opt/oblio-emitere && /opt/oblio-emitere/.venv/bin/python /opt/oblio-emitere/main.py >> /opt/oblio-emitere/cron.log 2>&1
```

## 7) Verificare rulare automata

```bash
tail -f /opt/oblio-emitere/cron.log
```

Vezi si ultimele intrari cron:

```bash
grep CRON /var/log/syslog | tail -n 50
```

## 8) Ce faci daca nu apasa corect butonul

1. Ruleaza cu browser vizibil:
```bash
HEADLESS=0 python main.py --slow-ms 300
```
2. Daca da eroare, verifica screenshot-urile generate:
- `error-timeout.png`
- `error-generic.png`

## Observatii

- Varianta asta depinde de UI-ul Oblio; daca se schimba butoanele/selectori, scriptul trebuie ajustat.
- Daca ai 2FA/captcha la login, automatizarea poate necesita pas suplimentar.
- Pentru stabilitate, recomandarile sunt: server timezone `Europe/Bucharest`, plus rulare `--dry-run` in prima zi.
- Daca vrei doar alerta la esec, seteaza `ALERT_ON_SUCCESS=0`.
