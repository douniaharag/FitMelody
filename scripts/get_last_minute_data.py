#!/usr/bin/env python3
import os, time, json
from datetime import datetime
import requests
from scripts.oauth2_utils import get_fitbit_client

print("CLIENT_ID =", os.getenv("CLIENT_ID"))
print("CLIENT_SECRET =", "******" if os.getenv("CLIENT_SECRET") else None)
print("REDIRECT_URI =", os.getenv("REDIRECT_URI"))

# === CONFIG ===
CLIENT_ID     = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
REDIRECT_URI  = os.environ.get("REDIRECT_URI")

# === AUTHENT ===
fb = get_fitbit_client(CLIENT_ID, CLIENT_SECRET, REDIRECT_URI)
tok = fb.client.session.token["access_token"]
headers = {"Authorization": f"Bearer {tok}"}

# === DATE DU JOUR ===
date_str = datetime.now().strftime("%Y-%m-%d")
print(f"\n📅 Données du {date_str}\n")

# === MÉTRIQUES INTRADAY À RÉCUPÉRER ===
resources = {
    'Steps':               'activities/steps',
    'Calories':            'activities/calories',
    'Distance':            'activities/distance',
    'Floors':              'activities/floors',
    'Elevation':           'activities/elevation',
    'HeartRate':           'activities/heart',
    'MinutesSedentary':    'activities/minutesSedentary',
    'MinutesLightlyActive':'activities/minutesLightlyActive',
    'MinutesFairlyActive': 'activities/minutesFairlyActive',
    'MinutesVeryActive':   'activities/minutesVeryActive',
}

# === RÉCUP INTRADAY ===
latest_time = None
data_last = {}

for label, path in resources.items():
    print(f"→ {label}...", end=" ")
    url = f"https://api.fitbit.com/1/user/-/{path}/date/{date_str}/1d/1min.json"
    for attempt in range(5):
        resp = requests.get(url, headers=headers)
        if resp.status_code == 429:
            wait = 2**attempt
            print(f"(429, j’attends {wait}s)", end=" ")
            time.sleep(wait)
            continue
        if resp.status_code != 200:
            print(f"(Erreur {resp.status_code})")
            data_last[label] = None
            break

        js  = resp.json()
        key = next((k for k in js if k.endswith("-intraday")), None)
        ds  = js[key]["dataset"] if key else []
        if ds:
            last = ds[-1]
            data_last[label] = last["value"]
            latest_time = last["time"]
        else:
            data_last[label] = None

        print("OK")
        break
    else:
        data_last[label] = None

# === AFFICHAGE INTRADAY ===
print("\n🔔 Résumé dernière minute :")
for k, v in data_last.items():
    print(f"   {k:<20} : {v}")
print(f"🕒 Heure dernière mesure : {latest_time or '--:--'}\n")

# === DONNÉES DE SOMMEIL ===
print("😴 Sommeil :")
sleep_url = f"https://api.fitbit.com/1.2/user/-/sleep/date/{date_str}.json"
sr = requests.get(sleep_url, headers=headers)
if sr.status_code == 200 and sr.json().get("sleep"):
    ms = sr.json()["sleep"][0]
    lv = ms.get("levels", {}).get("summary", {})
    print(f"   Asleep     : {ms.get('minutesAsleep',0)} min")
    print(f"   Efficiency : {ms.get('efficiency',0)}%")
    print(f"   Deep       : {lv.get('deep',{}).get('minutes',0)} min")
    print(f"   REM        : {lv.get('rem',{}).get('minutes',0)} min")
    print(f"   Light      : {lv.get('light',{}).get('minutes',0)} min")
    print(f"   Wake       : {lv.get('wake',{}).get('minutes',0)} min")
else:
    print(f"   (Pas de données, statut {sr.status_code})")

# === FORMAT COMPACT POUR IA ===
fmt = (
    f"date:{date_str} time:{latest_time or '--:--'} "
    + " ".join(f"{k.lower()}:{data_last.get(k,0)}"
               for k in ["HeartRate","Steps","Calories","MinutesSedentary"])
    + f" asleep:{ms.get('minutesAsleep',0)} eff:{ms.get('efficiency',0)}"
    + f" rem:{lv.get('rem',{{}}).get('minutes',0)}"
    + f" deep:{lv.get('deep',{{}}).get('minutes',0)}"
    + f" wake:{lv.get('wake',{{}}).get('minutes',0)}"
)
print("\n🧾 Compact IA :", fmt, "\n")