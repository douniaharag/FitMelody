import os
import logging
from flask import Flask, request, jsonify
from azure.storage.blob import BlobServiceClient
from transformers import T5Tokenizer, T5ForConditionalGeneration
from peft import PeftModel
import torch

app = Flask(__name__)

# Configurer le logging sur stdout avec niveau INFO
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# === Variables Azure Blob Storage ===
# On essaie d'abord AZURE_CONNECTION_STRING, sinon AZURE_STORAGE_CONNECTION_STRING
conn_str = os.environ.get("AZURE_CONNECTION_STRING") \
           or os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
if not conn_str:
    logging.error("AZURE_CONNECTION_STRING and AZURE_STORAGE_CONNECTION_STRING are not set")
    raise ValueError("Il faut définir AZURE_CONNECTION_STRING ou AZURE_STORAGE_CONNECTION_STRING")

CONTAINER_NAME = "model"
LOCAL_LORA_DIR = "/tmp/lora_model"
LOCAL_T5_DIR = "/tmp/t5-base-finetuned"

logging.info("Using Azure connection string for Blob Storage")

# Création des dossiers locaux si nécessaire
os.makedirs(LOCAL_LORA_DIR, exist_ok=True)
os.makedirs(LOCAL_T5_DIR, exist_ok=True)

def download_folder_from_blob(container_client, blob_folder, local_folder):
    os.makedirs(local_folder, exist_ok=True)
    blobs = container_client.list_blobs(name_starts_with=f"{blob_folder}/")
    for blob in blobs:
        filename = os.path.basename(blob.name)
        if not filename:
            continue
        blob_client = container_client.get_blob_client(blob.name)
        dest_path = os.path.join(local_folder, filename)
        with open(dest_path, "wb") as f:
            f.write(blob_client.download_blob().readall())
        logging.info(f"🟢 {blob_folder} > {filename} téléchargé dans {dest_path}")

def download_latest_lora(container_client):
    latest_blob = container_client.get_blob_client("latest.txt")
    lora_folder = latest_blob.download_blob().readall().decode().strip()
    logging.info(f"📥 Dernière version LoRA : {lora_folder}")
    download_folder_from_blob(container_client, lora_folder, LOCAL_LORA_DIR)
    return lora_folder

def download_t5_base(container_client):
    logging.info("📥 Téléchargement du modèle T5-base-finetuned...")
    download_folder_from_blob(container_client, "t5-base-finetuned", LOCAL_T5_DIR)

# Connexion au container blob
blob_service_client = BlobServiceClient.from_connection_string(conn_str)
container_client = blob_service_client.get_container_client(CONTAINER_NAME)

# Télécharger les modèles au démarrage
logging.info("🟢 Téléchargement du dernier adaptateur LoRA...")
lora_folder = download_latest_lora(container_client)

logging.info("🟢 Téléchargement du modèle T5 base...")
download_t5_base(container_client)

# Charger tokenizer + modèles
logging.info("🟢 Chargement du tokenizer et des modèles...")
tokenizer = T5Tokenizer.from_pretrained(LOCAL_T5_DIR)
model = T5ForConditionalGeneration.from_pretrained(LOCAL_T5_DIR)
model = PeftModel.from_pretrained(model, LOCAL_LORA_DIR)
model.eval()

@app.route("/score", methods=["POST"])
def score():
    data = request.get_json() or {}
    biometric = data.get("biometric", "")
    prompt = f"Generate music style: {biometric}"

    logging.info(f"Received scoring request with biometric: {biometric}")

    input_ids = tokenizer(
        prompt,
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=64
    ).input_ids

    with torch.no_grad():
        output = model.generate(input_ids, max_new_tokens=16)
    text = tokenizer.decode(output[0], skip_special_tokens=True)

    logging.info(f"Generated prompt: {text}")
    return jsonify({"generated_prompt": text})

if __name__ == "__main__":
    logging.info("Starting Flask app")
    app.run(host="0.0.0.0", port=5000)
