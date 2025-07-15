import os
import json
import urllib.parse
import requests
from typing import Optional, Dict

# Chemin où stocker/charger le token OAuth Fitbit
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "fitbit_token.json")

def build_authorize_url(client_id: str, redirect_uri: str) -> str:
    """
    Construit l'URL vers laquelle rediriger l'utilisateur pour autorisation Fitbit.
    """
    ru = urllib.parse.quote(redirect_uri, safe='')
    return (
        "https://www.fitbit.com/oauth2/authorize"
        f"?response_type=code"
        f"&client_id={client_id}"
        f"&redirect_uri={ru}"
        "&scope=activity%20heartrate%20sleep%20nutrition%20weight"
        "&expires_in=604800"
    )

def exchange_code_for_token(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str
) -> Dict:
    """
    Échange le code OAuth obtenu contre un access & refresh token.
    """
    token_url = "https://api.fitbit.com/oauth2/token"
    data = {
        "client_id":    client_id,
        "grant_type":   "authorization_code",
        "redirect_uri": redirect_uri,
        "code":         code
    }
    resp = requests.post(
        token_url,
        data=data,
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    resp.raise_for_status()
    return resp.json()

def load_token() -> Optional[Dict]:
    """
    Charge un token Fitbit depuis le disque si présent,
    ou renvoie None.
    """
    if not os.path.isfile(TOKEN_PATH):
        return None
    with open(TOKEN_PATH, "r", encoding="utf-8") as f:
        return json.load(f)
