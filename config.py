 config.py
import os

# Datenbankkonfiguration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'user': os.getenv('DB_USER', 'user'),
    'password': os.getenv('DB_PASSWORD', 'passwort'),
    'db': os.getenv('DB_NAME', 'dbname')
}

# Serverkonfiguration
SERVER_IP = os.getenv('SERVER_IP', '0.0.0.0')
SERVER_PORT = os.getenv('SERVER_PORT', 5000)



APK_BASE_PATH = os.path.join(os.path.dirname(__file__), 'apks')

VERSIONS_FILE = os.path.join(os.path.dirname(__file__), 'apk_versions.json')

CONFIG_FILE_PATH = 'config.xml'  # Aktualisieren Sie dies mit dem korrekten Pfad



