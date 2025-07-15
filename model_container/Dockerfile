# Utilise une image officielle Python avec support CUDA (si GPU nécessaire)
FROM python:3.10-slim

# Installer les dépendances système
RUN apt-get update && apt-get install -y \
    git \
    && rm -rf /var/lib/apt/lists/*

# Définir le répertoire de travail
WORKDIR /app

# Copier les fichiers requirements.txt si tu en as, sinon installer directement
# COPY requirements.txt .
# RUN pip install --no-cache-dir -r requirements.txt

# Installer les librairies Python nécessaires
RUN pip install --no-cache-dir \
    flask \
    torch \
    transformers \
    datasets \
    peft \
    azure-storage-blob \
    azure-identity \
    pandas \
    sentencepiece


# Copier tout le code source dans le container
COPY . .

# Exposer le port Flask
EXPOSE 5000

# Variables d'environnement (optionnel si tu préfères passer à l'exécution)
# ENV AZURE_CONNECTION_STRING="..."

# Commande pour démarrer l'application Flask
CMD ["python", "scoring_service.py"]
