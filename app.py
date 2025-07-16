import os
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

print("=== Démarrage de l'application Flask ===")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY")

# Variables d'environnement
CLIENT_ID            = os.environ.get("CLIENT_ID")
CLIENT_SECRET        = os.environ.get("CLIENT_SECRET")
REDIRECT_URI         = os.environ.get("REDIRECT_URI")
AZ_CONN_STR          = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
AUDIO_CONTAINER      = os.environ.get("AUDIO_CONTAINER")
FEEDBACK_CONTAINER   = os.environ.get("FEEDBACK_CONTAINER")
AZURE_MODEL_ENDPOINT = os.environ.get("AZURE_MODEL_ENDPOINT")

# Clients Azure Blob
blob_service_client       = BlobServiceClient.from_connection_string(AZ_CONN_STR)
audio_container_client    = blob_service_client.get_container_client(AUDIO_CONTAINER)
feedback_container_client = blob_service_client.get_container_client(FEEDBACK_CONTAINER)

def get_fb_client():
    token = session.get("fitbit_token") or load_token()
    if not token:
        return None
    return fitbit.Fitbit(
        CLIENT_ID,
        CLIENT_SECRET,
        oauth2=True,
        access_token=token["access_token"],
        refresh_token=token["refresh_token"]
    )

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
    return redirect("/")  # redirige vers la page principale

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
        "date":      today,
        "time":      latest_time,
        "steps":     data.get("Steps", "-"),
        "calories":  data.get("Calories", "-"),
        "bpm":       data.get("HeartRate", "-"),
        "sedentary": data.get("MinutesSedentary", "-"),
        **sleep_summary
    }
    return jsonify(final_data)

@app.route("/generate_music", methods=["POST"])
def generate_music():
    d = request.json or {}
    biometric_input = " ".join(f"{k}:{v}" for k, v in d.items())

    try:
        response = requests.post(
            AZURE_MODEL_ENDPOINT,
            json={"biometric": biometric_input},
            timeout=300
        )
        response.raise_for_status()
        prompt = response.json().get("generated_prompt", "")
        if not prompt:
            return jsonify({"status": "error", "message": "Le modèle n'a pas retourné de prompt"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erreur modèle : {e}"}), 500

    try:
        HF_API = "https://douniaharag-fitmusicgen-api.hf.space/generate"
        music_resp = requests.post(HF_API, json={"prompt": prompt, "duration": 30}, timeout=300)
        music_resp.raise_for_status()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erreur MusicGen HF : {e}"}), 500

    from io import BytesIO
    filename = f"music_{datetime.datetime.now():%Y-%m-%d_%H-%M-%S}.wav"
    blob_client = audio_container_client.get_blob_client(filename)
    blob_client.upload_blob(BytesIO(music_resp.content), overwrite=True)

    return jsonify({
        "status": "success",
        "filename": filename,
        "url": blob_client.url,
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

    if not lines:
        lines.append("input_text,music_prompt,score")
    esc = lambda s: s.replace('"', '""')
    lines.append(f'"{esc(input_text)}","{esc(music_prompt)}",{score}')
    blob_client.upload_blob("\n".join(lines) + "\n", overwrite=True)

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
