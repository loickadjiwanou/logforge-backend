import socket
import json
import time

# Configuration
# =============
# Remplacez cette clé par une vraie API Key de projet LogForge
API_KEY = ""  # <--- METTRE VOTRE CLÉ ICI
SERVER_IP = "[IP_ADDRESS]"
SERVER_PORT = 12201

def send_gelf_udp(payload):
    """Envoie un dictionnaire Python en GELF UDP."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        message = json.dumps(payload).encode('utf-8')
        sock.sendto(message, (SERVER_IP, SERVER_PORT))
        print(f"Message envoyé à {SERVER_IP}:{SERVER_PORT}")
        print(f"Payload: {json.dumps(payload, indent=2)}")
    except Exception as e:
        print(f"Erreur lors de l'envoi : {e}")
    finally:
        sock.close()

if __name__ == "__main__":
    print("--- Test GELF UDP pour LogForge ---")
    
    # 1. Test Minimal (Message court uniquement)
    print("\nEnvoi d'un log minimal...")
    test_min = {
        "version": "1.1",
        "host": "test-client-udp",
        "short_message": "Test GELF UDP Minimal",
        "level": "info",
        "_api_key": API_KEY
    }
    send_gelf_udp(test_min)
    
    time.sleep(1) # Petite pause entre les envois
    
    # 2. Test Complet (Message long + Metadata)
    print("\nEnvoi d'un log complet avec Stack Trace et Metadata...")
    test_full = {
        "version": "1.1",
        "host": "test-client-full",
        "short_message": "Erreur de connexion base de données",
        "full_message": "Traceback (most recent call last):\n  File \"app.py\", line 42, in connect\n    db.connect()\nConnectionError: Failed to connect to DB at 10.0.0.5",
        "level": "error",
        "_api_key": API_KEY,
        "_environment": "development",
        "_channel": "backend-api",
        "_user_id": 12345,
        "_request_time": 0.452,
        "_tags": ["database", "critical"]
    }
    send_gelf_udp(test_full)

    # 3. Test Native Syslog Level (Integer 4 - Warning)
    print("\nEnvoi d'un log avec niveau natif GELF (Entier 4 - Warning)...")
    test_native = {
        "version": "1.1",
        "host": "test-client-native",
        "short_message": "Log envoyé via Docker/Syslog (level: 4)",
        "level": 4, # Désormais supporté pour la compatibilité native
        "_api_key": API_KEY
    }
    send_gelf_udp(test_native)

    print("\n--- Terminée ---")
    print("Vérifiez votre tableau de bord LogForge pour voir les nouveaux logs.")
    print("Note: Le log avec level: 4 (entier) devrait désormais apparaître comme WARNING.")
