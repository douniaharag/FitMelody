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

print("=== Démarrage de l'application Flask ===")

print("Variables d'environnement chargées :")
print("FLASK_SECRET_KEY =", os.getenv("FLASK_SECRET_KEY"))
print("CLIENT_ID =", os.getenv("CLIENT_ID"))
print("AZURE_STORAGE_CONNECTION_STRING =", os.getenv("AZURE_STORAGE_CONNECTION_STRING")[:10] + "...")
print("AZURE_MODEL_ENDPOINT =", os.getenv("AZURE_MODEL_ENDPOINT"))

app = Flask(__name__)

print("Chargement des variables d'environnement...")
app.secret_key = os.environ.get("FLASK_SECRET_KEY")
print("FLASK_SECRET_KEY chargée:", bool(app.secret_key))

CLIENT_ID     = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
REDIRECT_URI  = os.environ.get("REDIRECT_URI")

print("CLIENT_ID:", CLIENT_ID)
print("CLIENT_SECRET:", "******" if CLIENT_SECRET else None)
print("REDIRECT_URI:", REDIRECT_URI)

AZ_CONN_STR        = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
AUDIO_CONTAINER    = os.environ.get("AUDIO_CONTAINER")
FEEDBACK_CONTAINER = os.environ.get("FEEDBACK_CONTAINER")

print("AZURE_STORAGE_CONNECTION_STRING chargée:", bool(AZ_CONN_STR))
print("AUDIO_CONTAINER:", AUDIO_CONTAINER)
print("FEEDBACK_CONTAINER:", FEEDBACK_CONTAINER)

blob_service_client = BlobServiceClient.from_connection_string(AZ_CONN_STR)

# Suppression de la création de containers car ils existent déjà
print("Utilisation des containers Blob existants...")

audio_container_client    = blob_service_client.get_container_client(AUDIO_CONTAINER)
feedback_container_client = blob_service_client.get_container_client(FEEDBACK_CONTAINER)

AZURE_MODEL_ENDPOINT = os.environ.get("AZURE_MODEL_ENDPOINT")
print("AZURE_MODEL_ENDPOINT:", AZURE_MODEL_ENDPOINT)
if not AZURE_MODEL_ENDPOINT:
    raise ValueError("AZURE_MODEL_ENDPOINT environment variable is not set")

def get_fb_client():
    print("Tentative de récupération du client Fitbit...")
    token = session.get("fitbit_token") or load_token()
    if not token:
        print("Aucun token Fitbit trouvé.")
        return None
    print("Token Fitbit trouvé, création du client Fitbit.")
    return fitbit.Fitbit(
        CLIENT_ID,
        CLIENT_SECRET,
        oauth2=True,
        access_token=token["access_token"],
        refresh_token=token["refresh_token"]
    )

@app.route("/authorize")
def authorize():
    print("Redirection vers l'URL d'autorisation Fitbit.")
    return redirect(build_authorize_url(CLIENT_ID, REDIRECT_URI))

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        print("Aucun code OAuth reçu dans le callback.")
        return "❌ Pas de code OAuth retourné", 400
    print(f"Code OAuth reçu: {code}")
    token = exchange_code_for_token(CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, code)
    session["fitbit_token"] = token
    print("Token Fitbit stocké en session.")
    return "<h3>✅ Autorisation Fitbit OK ! Revenez à l'accueil.</h3>"

@app.route("/")
def index():
    print("Page d'accueil demandée.")
    return render_template("index.html")

@app.route("/biometrics")
def biometrics():
    print("Requête biometrics reçue.")
    fb = get_fb_client()
    if fb is None:
        print("Client Fitbit non disponible, redirection vers /authorize.")
        return redirect("/authorize")
    token = fb.client.session.token
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    resources = {
        "Steps":            "activities/steps",
        "Calories":         "activities/calories",
        "HeartRate":        "activities/heart",
        "MinutesSedentary": "activities/minutesSedentary",
    }

    data = {}
    latest_time = None
    for label, path in resources.items():
        url  = f"https://api.fitbit.com/1/user/-/{path}/date/{today}/1d/1min.json"
        print(f"Récupération données Fitbit: {label} depuis {url}")
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            intraday = next(
                (v for k, v in resp.json().items() if "intraday" in k),
                {}
            ).get("dataset", [])
            if intraday:
                last = intraday[-1]
                data[label]    = last["value"]
                latest_time    = last["time"]

    sleep_url  = f"https://api.fitbit.com/1.2/user/-/sleep/date/{today}.json"
    print(f"Récupération données sommeil depuis {sleep_url}")
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
    print("📥 Données Fitbit envoyées :", final_data)
    return jsonify(final_data)

@app.route("/generate_music", methods=["POST"])
def generate_music():
    print("Requête generate_music reçue.")
    d = request.json or {}
    biometric_input = " ".join(f"{k}:{v}" for k, v in d.items())
    print("Paramètres biométriques reçus :", biometric_input)

    try:
        print(f"Appel au modèle via {AZURE_MODEL_ENDPOINT} ...")
        response = requests.post(
            AZURE_MODEL_ENDPOINT,
            json={"biometric": biometric_input},
            timeout=300
        )
        response.raise_for_status()
        model_result = response.json()
        prompt = model_result.get("generated_prompt", "")
        print("Réponse du modèle reçue :", prompt)
        if not prompt:
            print("Erreur : prompt vide reçu du modèle.")
            return jsonify({"status": "error", "message": "Le modèle n'a pas retourné de prompt"}), 500
    except Exception as e:
        print("Exception lors de l'appel au modèle :", e)
        return jsonify({"status": "error", "message": f"Erreur lors de l'appel au modèle : {str(e)}"}), 500

    return jsonify({
        "status": "success",
        "generated_prompt": prompt,
        "input_biometrics": biometric_input
    })

@app.route("/submit_feedback", methods=["POST"])
def submit_feedback():
    print("Requête submit_feedback reçue.")
    data         = request.json or {}
    input_text   = data.get("input_text", "").strip()
    music_prompt = data.get("music_prompt", "").strip()
    score        = data.get("score", None)

    if not input_text or not music_prompt or score is None:
        print("Erreur : champs manquants dans le feedback.")
        return jsonify({"status": "error", "message": "Champs manquants"})

    blob_client = feedback_container_client.get_blob_client("user_feedback.csv")

    try:
        existing = blob_client.download_blob().readall().decode("utf-8")
        lines    = existing.splitlines()
    except ResourceNotFoundError:
        lines = []

    rows = []
    if not lines:
        rows.append("input_text,music_prompt,score")

    esc_input  = input_text.replace('"', '""')
    esc_prompt = music_prompt.replace('"', '""')
    rows.append(f'"{esc_input}","{esc_prompt}",{score}')

    content = "\n".join(lines + rows) + "\n"
    blob_client.upload_blob(content, overwrite=True)
    print("Feedback enregistré avec succès.")

    return jsonify({"status": "success", "message": "Feedback enregistré ✅"})

@app.route("/heart_history")
def heart_history():
    print("Requête heart_history reçue.")
    return last_60min_values("activities/heart")

@app.route("/steps_history")
def steps_history():
    print("Requête steps_history reçue.")
    return last_60min_values("activities/steps")

@app.route("/calories_history")
def calories_history():
    print("Requête calories_history reçue.")
    return last_60min_values("activities/calories")

@app.route("/sedentary_history")
def sedentary_history():
    print("Requête sedentary_history reçue.")
    return last_60min_values("activities/minutesSedentary")

def last_60min_values(path):
    print(f"Récupération des 60 dernières minutes pour {path}.")
    fb = get_fb_client()
    if fb is None:
        print("Client Fitbit non disponible.")
        return jsonify([])
    token   = fb.client.session.token
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    today   = datetime.datetime.now().strftime("%Y-%m-%d")
    url     = f"https://api.fitbit.com/1/user/-/{path}/date/{today}/1d/1min.json"
    resp    = requests.get(url, headers=headers)
    if resp.status_code != 200:
        print(f"Erreur lors de la récupération des données Fitbit ({resp.status_code}).")
        return jsonify([])

    dataset = next(
        (v for k, v in resp.json().items() if "intraday" in k),
        {}
    ).get("dataset", [])
    print(f"{len(dataset)} valeurs récupérées.")
    return jsonify(dataset[-60:] if len(dataset) >= 60 else dataset)

if __name__ == "__main__":
    print("=== Lancement du serveur local Flask sur port 5000 ===")
    app.run(host="127.0.0.1", port=5000, debug=False)
