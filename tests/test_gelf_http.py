import requests
import json
import time

# Configuration
# =============
# Remplacez cette clé par une vraie API Key de projet LogForge
API_KEY = ""  # <--- METTRE VOTRE CLÉ ICI
SERVER_URL = "${BACKEND_URL}/api/logs/gelf"

def send_gelf_http(payload):
    """Envoie un dictionnaire Python en GELF via HTTP POST."""
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": API_KEY
    }
    try:
        response = requests.post(SERVER_URL, json=payload, headers=headers)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
    except Exception as e:
        print(f"Erreur lors de l'envoi HTTP : {e}")

if __name__ == "__main__":
    print("--- Test GELF HTTP pour LogForge ---")
    
    # 1. Test Minimal (Message court uniquement)
    print("\nEnvoi d'un log minimal via HTTP...")
    test_min = {
        "version": "1.1",
        "host": "test-client-http",
        "short_message": "Test GELF HTTP Minimal",
        "level": "info"
    }
    send_gelf_http(test_min)
    
    time.sleep(1)
    
    # 2. Test Complet (Message long + Metadata)
    print("\nEnvoi d'un log complet via HTTP...")
    test_full = {
        "version": "1.1",
        "host": "test-client-http-full",
        "short_message": "Alerte de sécurité détectée (HTTP)",
        "full_message": "Tentatives de connexion multiples échouées pour l'utilisateur 'admin' depuis l'IP 192.168.1.50",
        "level": "critical",
        "_environment": "production",
        "_channel": "security",
        "_source": "auth-service",
        "_attempt_count": 5
    }
    send_gelf_http(test_full)

    # 3. Test Native Syslog Level (Integer 3 - Error)
    print("\nEnvoi d'un log avec niveau natif GELF (Entier 3 - Error)...")
    test_native = {
        "version": "1.1",
        "host": "test-client-http-native",
        "short_message": "Erreur système via integer level (HTTP)",
        "level": 3
    }
    send_gelf_http(test_native)

    print("\n--- Terminée ---")
    print("Vérifiez votre tableau de bord LogForge pour voir les nouveaux logs.")
