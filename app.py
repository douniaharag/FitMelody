import os
import sys
import csv
import datetime
import requests
from flask import Flask, render_template, jsonify, redirect, request, session
import fitbit
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceNotFoundError
from scripts.oauth2_utils import (
    build_authorize_url,
    exchange_code_for_token,
    load_token
)

# ─── 1. Création de l'app et configuration secrète ──────────────────────────
app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET_KEY"]

# ─── 2. Identifiants Fitbit et redirect URI ─────────────────────────────────
CLIENT_ID     = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
REDIRECT_URI  = os.environ["REDIRECT_URI"]

# ─── 3. Configuration Azure Blob Storage ────────────────────────────────────
AZ_CONN_STR        = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
AUDIO_CONTAINER    = os.environ["AUDIO_CONTAINER"]
FEEDBACK_CONTAINER = os.environ["FEEDBACK_CONTAINER"]

blob_service_client = BlobServiceClient.from_connection_string(AZ_CONN_STR)

# Création des clients de conteneur
audio_container_client    = blob_service_client.get_container_client(AUDIO_CONTAINER)
feedback_container_client = blob_service_client.get_container_client(FEEDBACK_CONTAINER)

# ─── 4. Fonctions d’aide Fitbit ─────────────────────────────────────────────
def get_fb_client():
    token = session.get("fitbit_token") or load_token()
    if not token:
        print("⚠️ Aucun token Fitbit, redirection vers /authorize")
        return None
    return fitbit.Fitbit(
        CLIENT_ID,
        CLIENT_SECRET,
        oauth2=True,
        access_token=token["access_token"],
        refresh_token=token["refresh_token"]
    )

# ─── 5. Routes de l'application ─────────────────────────────────────────────

@app.route("/authorize")
def authorize():
    return redirect(build_authorize_url(CLIENT_ID, REDIRECT_URI))

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "❌ Pas de code OAuth retourné", 400
    token = exchange_code_for_token(CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, code)
    session["fitbit_token"] = token
    return "<h3>✅ Autorisation Fitbit OK ! Revenez à l'accueil.</h3>"

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/biometrics")
def biometrics():
    fb = get_fb_client()
    if fb is None:
        return redirect("/authorize")
    token = fb.client.session.token
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    resources = {
        "Steps": "activities/steps",
        "Calories": "activities/calories",
        "HeartRate": "activities/heart",
        "MinutesSedentary": "activities/minutesSedentary",
    }

    data = {}
    latest_time = None
    for label, path in resources.items():
        url = f"https://api.fitbit.com/1/user/-/{path}/date/{today}/1d/1min.json"
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            intraday = next((v for k, v in resp.json().items() if "intraday" in k), {}).get("dataset", [])
            if intraday:
                last = intraday[-1]
                data[label] = last["value"]
                latest_time = last["time"]

    # Données de sommeil
    sleep_url = f"https://api.fitbit.com/1.2/user/-/sleep/date/{today}.json"
    sleep_resp = requests.get(sleep_url, headers=headers)
    sleep_summary = {}
    if sleep_resp.status_code == 200 and sleep_resp.json().get("sleep"):
        ms = sleep_resp.json()["sleep"][0]
        summary = ms["levels"]["summary"]
        sleep_summary = {
            "asleep": ms.get("minutesAsleep", 0),
            "eff":    ms.get("efficiency", 0),
            "rem":    summary.get("rem", {}).get("minutes", 0),
            "deep":   summary.get("deep", {}).get("minutes", 0),
            "wake":   summary.get("wake", {}).get("minutes", 0),
        }

    final_data = {
        "date": today,
        "time": latest_time,
        "steps": data.get("Steps", "-"),
        "calories": data.get("Calories", "-"),
        "bpm": data.get("HeartRate", "-"),
        "sedentary": data.get("MinutesSedentary", "-"),
        **sleep_summary
    }

    return jsonify(final_data)

@app.route("/generate_music", methods=["POST"])
def generate_music():
    d = request.json or {}
    biometric_input = " ".join(f"{k}:{v}" for k, v in d.items())

    # 1️⃣ Appel modèle Azure ML pour générer le prompt
    try:
        ml_endpoint = os.environ["AZUREML_ENDPOINT"]
        ml_key = os.environ["AZUREML_KEY"]
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ml_key}"
        }
        payload = {"biometric": biometric_input}
        ml_resp = requests.post(ml_endpoint, json=payload, headers=headers, timeout=300)
        ml_resp.raise_for_status()
        result = ml_resp.json()
        prompt = result["generated_prompt"]
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erreur Azure ML : {str(e)}"})

    # 2️⃣ Appel Hugging Face pour générer la musique
    try:
        HF_API = "https://douniaharag-fitmusicgen-api.hf.space/generate"
        music_payload = {"prompt": prompt, "duration": 30}
        music_resp = requests.post(HF_API, json=music_payload, timeout=300)
        music_resp.raise_for_status()
        audio_data = music_resp.content
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erreur Hugging Face : {str(e)}"})

    # 3️⃣ Sauvegarde dans Azure Blob Storage
    filename = f"music_{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.wav"
    try:
        blob_client = audio_container_client.get_blob_client(filename)
        blob_client.upload_blob(audio_data, overwrite=True)
        audio_url = blob_client.url
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erreur upload audio : {str(e)}"})

    return jsonify({
        "status": "success",
        "filename": filename,
        "url": audio_url,
        "prompt": prompt,
        "input_text": biometric_input
    })

@app.route("/submit_feedback", methods=["POST"])
def submit_feedback():
    data = request.json or {}
    input_text = data.get("input_text", "").strip()
    music_prompt = data.get("music_prompt", "").strip()
    score = data.get("score", None)

    if not input_text or not music_prompt or score is None:
        return jsonify({"status": "error", "message": "Champs manquants"})

    blob_client = feedback_container_client.get_blob_client("user_feedback.csv")
    try:
        existing = blob_client.download_blob().readall().decode("utf-8")
        lines = existing.splitlines()
    except ResourceNotFoundError:
        lines = []

    rows = []
    if not lines:
        rows.append("input_text,music_prompt,score")

    esc_input = input_text.replace('"', '""')
    esc_prompt = music_prompt.replace('"', '""')
    rows.append(f'"{esc_input}","{esc_prompt}",{score}')

    content = "\n".join(lines + rows) + "\n"
    blob_client.upload_blob(content, overwrite=True)

    return jsonify({"status": "success", "message": "Feedback enregistré ✅"})

@app.route("/heart_history")
def heart_history():
    return last_60min_values("activities/heart")

@app.route("/steps_history")
def steps_history():
    return last_60min_values("activities/steps")

@app.route("/calories_history")
def calories_history():
    return last_60min_values("activities/calories")

@app.route("/sedentary_history")
def sedentary_history():
    return last_60min_values("activities/minutesSedentary")

def last_60min_values(path):
    fb = get_fb_client()
    if fb is None:
        return jsonify([])
    token = fb.client.session.token
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    url = f"https://api.fitbit.com/1/user/-/{path}/date/{today}/1d/1min.json"
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        return jsonify([])
    dataset = next((v for k, v in resp.json().items() if "intraday" in k), {}).get("dataset", [])
    return jsonify(dataset[-60:] if len(dataset) >= 60 else dataset)

# ─── 6. Lancement local ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("✅ Démarrage local Flask sur http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
