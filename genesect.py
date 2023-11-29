from flask import Flask, jsonify, render_template, request, Response, abort, send_file
from flask_httpauth import HTTPBasicAuth
from werkzeug.utils import secure_filename
import pymysql
import xml.etree.ElementTree as ET
from pymysqlpool.pool import Pool
from config import DB_CONFIG, SERVER_IP, SERVER_PORT, APK_BASE_PATH, CONFIG_FILE_PATH, VERSIONS_FILE
import logging
import os
import json
import re
from datetime import datetime
from flask_cors import CORS
import tempfile
from apkutils import APK


app = Flask(__name__)
CORS(app)
auth = HTTPBasicAuth()
# Konfiguration des Loggers
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
@app.route('/')


@app.route('/')
@auth.login_required
def index():
    # Load the XML values into a dictionary
    config_values = load_config_values()
    # Check if config_values is None and handle it appropriately
    if config_values is None:
        config_values = {}  # or handle the error in a way that makes sense for your application
    return render_template('index.html', config_values=config_values)


users = {
    "test": "hallo123"
}

@auth.verify_password
def verify_password(username, password):
    if username in users and users[username] == password:
        return username

pool = Pool(**DB_CONFIG)
pool.init()
def create_tables():
    create_device_table_sql = """
    CREATE TABLE IF NOT EXISTS device (
        id INT AUTO_INCREMENT PRIMARY KEY,
        mac VARCHAR(255) NOT NULL,
        origin VARCHAR(255),
        UNIQUE(mac)
    );
    """

    create_status_table_sql = """
    CREATE TABLE IF NOT EXISTS status (
        id INT AUTO_INCREMENT PRIMARY KEY,
        anfrage_mac VARCHAR(255) NOT NULL,
        status VARCHAR(50),
        new_mac VARCHAR(255),
        last_check TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE(anfrage_mac)
    );
    """

    try:
        connection = pool.get_conn()
        with connection.cursor() as cursor:
            cursor.execute(create_device_table_sql)
            cursor.execute(create_status_table_sql)
        connection.commit()
    except pymysql.MySQLError as e:
        print(f"Failed to create tables: {e}")
        raise 
    finally:
        pool.release(connection)



@app.route('/devices/all', methods=['GET'])
@auth.login_required  
def get_all_devices():
    try:        
        connection = pool.get_conn()  
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM device")
            devices = cursor.fetchall()
        return jsonify({'devices': devices})
        return jsonify({'devices': devices})
    except pymysql.MySQLError as e:
        print(f"Fehler beim Abrufen aller Geräte aus der Datenbank: {e}")
        return jsonify({'error': 'Fehler beim Abrufen aller Geräte aus der Datenbank'}), 500
    finally:
        pool.release(connection)

@app.route('/status/all', methods=['GET'])
def get_all_status():
    try:
        status_list = query_database("SELECT * FROM status")
        return jsonify(status_list)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def query_database(query, args=None, fetchone=True):
    try:
        connection = pool.get_conn()
        with connection.cursor() as cursor:
            cursor.execute(query, args)
            return cursor.fetchone() if fetchone else cursor.fetchall()
    except pymysql.MySQLError as e:
        print(f"Database error: {e}")
        abort(500)
    finally:
        pool.release(connection)


@app.route('/devices/waiting', methods=['GET'])
@auth.login_required
def get_waiting_devices():
    # Ich gehe davon aus, dass die korrekte Spalte für den Status 'status' heißt
    # und dass der Status der wartenden Geräte in der 'status' Tabelle gespeichert ist
    waiting_devices = query_database("SELECT * FROM status WHERE status = 'waiting'", fetchone=False)
    
    if waiting_devices:
        # Konvertieren der Ergebnisse in ein geeignetes Format, falls erforderlich
        return jsonify({'waiting_devices': waiting_devices})
    else:
        return jsonify({'message': 'No devices are waiting'}), 200

def format_mac(mac):
    return ':'.join(mac[i:i+2] for i in range(0, len(mac), 2))


def add_device_to_db(mac, origin):
    # Format the MAC address
    mac = format_mac(mac)    # Get a connection from the pool
    connection = pool.get_conn()
    try:
        with connection.cursor() as cursor:
            # Check if the MAC address already exists
            cursor.execute("SELECT id FROM device WHERE mac = %s", (mac,))
            existing_device = cursor.fetchone()

            if existing_device:
                # If the MAC address already exists, do not add it again
                return False, "Device with MAC {} already exists in the database.".format(mac)

            # If the MAC address does not exist, add the new device
            insert_query = "INSERT INTO device (mac, origin) VALUES (%s, %s)"
            cursor.execute(insert_query, (mac, origin))
            connection.commit()  # Commit the transaction
            return True, "Device with MAC {} added successfully.".format(mac)
    except pymysql.MySQLError as e:
        # If an error occurs, rollback the transaction
        connection.rollback()
        return False, "Failed to add device. Error: {}".format(e)
    finally:
        # Release the connection back to the pool
        pool.release(connection)

@app.route('/devices', methods=['POST'])
@auth.login_required
def add_device():
    data = request.json
    mac = data.get('mac', '')
    origin = data.get('origin', '')

    # Validierung der MAC-Adresse
    if not re.match("[0-9a-fA-F]{12}", mac):
        return jsonify({'error': 'Ungültige MAC-Adresse.'}), 400

    # Validierung der Herkunft (z. B. darf nicht leer sein)
    if not origin:
        return jsonify({'error': 'Herkunft ist erforderlich.'}), 400

    # Das Gerät zur Datenbank hinzufügen
    success, message = add_device_to_db(mac, origin)
    if success:
        return jsonify({'message': message}), 200
    else:
        return jsonify({'error': message}), 500 


from flask import jsonify, abort

@app.route('/assign/<waiting_mac>/<origin>', methods=['POST'])
@auth.login_required
def assign_device(waiting_mac, origin):
    origin = origin.strip()  # Leerzeichen entfernen
    print(f"Assigning device with waiting MAC {waiting_mac} to origin '{origin}'")

    # Abfrage der MAC-Adresse für den angegebenen Ursprung
    query_result = query_database("SELECT mac FROM device WHERE origin = %s", (origin,), fetchone=True)

    # Überprüfen, ob das Abfrageergebnis vorhanden ist
    if query_result is None:
        print(f"No device found for origin '{origin}'")
        return jsonify({'error': 'No device found for the given origin.'}), 404

    origin_mac = query_result['mac']
    print(f"Found MAC: {origin_mac} for origin: '{origin}'. Updating status...")

    try:
        # Aktualisieren des Status in der Datenbank
        connection = pool.get_conn()
        with connection.cursor() as cursor:
            cursor.execute("UPDATE status SET status = 'change', new_mac = %s WHERE anfrage_mac = %s", (origin_mac, waiting_mac))
            connection.commit()  # Commit der Transaktion
        print(f"Status updated for waiting MAC {waiting_mac}")
        return jsonify({'status': 'success', 'message': 'Device status updated successfully.'}), 200
    except Exception as e:
        print(f"Error in updating status: {e}")
        return jsonify({'error': 'Failed to update the status of the waiting device.'}), 500
    finally:
        pool.release(connection)



@app.route('/devices/<int:device_id>', methods=['PUT'])
@auth.login_required
def edit_device(device_id):
    data = request.get_json()
    mac = data['mac']
    origin = data['origin']
    query_database("UPDATE device SET mac = %s, origin = %s WHERE id = %s", (mac, origin, device_id), fetchone=False)
    return jsonify({'status': 'success', 'message': 'Device updated'}), 200

@app.route('/devices/<int:device_id>', methods=['DELETE'])
@auth.login_required
def delete_device(device_id):
    connection = pool.get_conn()
    try:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM device WHERE id = %s", (device_id,))
            connection.commit()  # Commit der Transaktion
        return jsonify({'status': 'success', 'message': 'Device deleted'}), 200
    except pymysql.MySQLError as e:
        connection.rollback()  # Rollback im Fehlerfall
        return jsonify({'error': str(e)}), 500
    finally:
        pool.release(connection)


def modify_xml_config(origin_value, config_path='path_to_config.xml'):
    try:
        tree = ET.parse(config_path)
        root = tree.getroot()
        for elem in root.iter('string'):
            if elem.get('name') == 'origin':
                elem.text = origin_value  # Aktualisieren des Textinhalts des Elements
                break

        with tempfile.NamedTemporaryFile(delete=False, mode='w', encoding='utf-8', suffix='.xml') as temp_file:
            tree.write(temp_file, encoding='unicode', xml_declaration=True)
            modified_config_path = temp_file.name

        return modified_config_path

    except ET.ParseError as e:
        print(f"Error parsing XML: {e}")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None




@app.route('/vm_conf', methods=['GET'])
@auth.login_required
def get_vm_config():
    mac = request.args.get('mac')
    if not mac:
        return jsonify({'error': 'MAC address is required'}), 400

    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT origin FROM device WHERE mac = %s", (mac,))
            result = cursor.fetchone()
        if result:
            origin = result[0]
            config_file_path = modify_xml_config(origin, CONFIG_FILE_PATH)
            if config_file_path:
                return send_file(config_file_path, mimetype='text/xml')
            else:
                return jsonify({'error': 'Failed to modify XML config'}), 500
        else:
            return jsonify({'error': 'MAC address not found'}), 404
    except pymysql.MySQLError as error:
        return jsonify({'error': f'Failed to query database: {error}'}), 500
    except Exception as e:
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500
    finally:
        if conn:
            conn.close()

@app.route('/vm_conf', methods=['POST'])
@auth.login_required
def send_vm_config():
    current_mac = request.form.get('mac')
    if not current_mac:
        return jsonify({'error': 'MAC is required'}), 400
    origin = query_database("SELECT origin FROM device WHERE mac = %s", (current_mac,))
    if origin:
        config_file_path = modify_xml_config(origin[0])
        if config_file_path:
            return send_file(config_file_path, mimetype='text/xml')
        else:
            return jsonify({'error': 'Failed to modify XML config'}), 500
    else:
        return jsonify({'error': 'MAC not found'}), 404

# Flask-Route zum Anzeigen und Bearbeiten der Werte

@app.route('/edit_vm_conf', methods=['GET', 'POST'])
@auth.login_required
def edit_vm_conf():
    if request.method == 'GET':
        # Lade die XML-Werte in ein Dictionary
        config_values = load_config_values()
        return render_template('index.html', config_values=config_values)
    elif request.method == 'POST':
        # Verarbeite die gesendeten Änderungen und aktualisiere die XML-Datei
        updated_values = request.form.to_dict()
        updated_values['useRawForInstructions'] = 'true' if 'useRawForInstructions' in updated_values else 'false'
        try:
            config_path = CONFIG_FILE_PATH
            tree = ET.parse(config_path)
            root = tree.getroot()
            for elem in root.iter('string'):
                name = elem.get('name')
                if name in updated_values:
                    elem.text = updated_values[name]
            with open(config_path, 'w', encoding='utf-8') as config_file:
                tree.write(config_file, encoding='unicode', xml_declaration=True)
            return jsonify({'message': 'Changes saved successfully'}), 200
        except ET.ParseError as e:
            print(f"Error parsing XML: {e}")
            return jsonify({'error': 'Failed to update XML config'}), 500
        except Exception as e:
            print(f"An error occurred: {e}")
            return jsonify({'error': 'Internal server error'}), 500

def load_config_values(config_path=CONFIG_FILE_PATH):
    try:
        tree = ET.parse(config_path)
        root = tree.getroot()
        config_values = {}
        for elem in root:
            name = elem.get('name')
            if elem.tag == 'string':
                value = elem.text
            elif elem.tag == 'boolean':
                value = elem.get('value')
            elif elem.tag == 'int':
                value = elem.get('value')
            else:
                continue  # Ignoriert andere Tags
            config_values[name] = value
        print(config_values)  # Debug-Ausgabe
        return config_values
    except ET.ParseError as e:
        print(f"Error parsing XML: {e}")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None

@app.route('/autoconfig/mymac', methods=['POST'])
@auth.login_required
def handle_mac_request():
    try:
        current_mac_bytes = request.data
        current_mac = current_mac_bytes.decode('utf-8').strip()

        logger.info(f"Empfangene MAC-Anfrage: {current_mac}")

        if not current_mac:
            logger.warning("Keine MAC-Adresse in der Anfrage")
            return jsonify({'error': 'MAC is required'}), 400

        device = query_database("SELECT origin FROM device WHERE mac = %s", (current_mac,))
        if device:
            logger.info(f"Bestätigte MAC-Adresse: {current_mac}")
            return jsonify({'response': 'confirmed'})

        status_entry = query_database("SELECT status, new_mac FROM status WHERE anfrage_mac = %s", (current_mac,))
        if status_entry and isinstance(status_entry, (list, tuple)) and len(status_entry) > 0:
            status, new_mac = status_entry[0]
            logger.info(f"Status-Eintrag gefunden: {status}, {new_mac}")
            if status == 'change' and new_mac:
                return jsonify({'response': 'change', 'new_mac': new_mac})
            else:
                return jsonify({'response': status})

        existing_entry = query_database("SELECT * FROM status WHERE anfrage_mac = %s", (current_mac,))
        if existing_entry:
            logger.info(f"Eintrag für {current_mac} existiert bereits. Keine Aktion erforderlich.")
            return jsonify({'response': 'existing'})

        logger.info(f"Unbekannte MAC-Adresse: {current_mac}, Status auf 'waiting' gesetzt")
        query_database("INSERT INTO status (anfrage_mac, status) VALUES (%s, 'waiting')", (current_mac,))
        logger.debug(f"Status für {current_mac} in die Datenbank eingefügt")

        return jsonify({'response': 'waiting'})

    except Exception as e:
        logger.error(f"Ein Fehler ist aufgetreten: {e}")
        return jsonify({'error': 'Internal server error'}), 500


def get_apk_version(apk_path):
    try:
        apk = APK(apk_path)
        version = apk.get_manifest()['@android:versionName']
        logging.info(f"APK-Version gefunden: {version}")
        return version
    except Exception as e:
        logging.error(f"Fehler beim Auslesen der APK-Version: {e}")
        return None

def update_versions_file(apk_name, version, versions_file_path):
    try:
        # Überprüfen, ob die JSON-Datei existiert und nicht leer ist.
        if not os.path.exists(versions_file_path) or os.stat(versions_file_path).st_size == 0:
            with open(versions_file_path, 'w') as file:
                json.dump({}, file)

        # Lesen der existierenden Daten aus der JSON-Datei
        with open(versions_file_path, 'r') as file:
            try:
                versions = json.load(file)
            except json.JSONDecodeError:
                # Wenn die Datei nicht im JSON-Format ist, initialisieren Sie sie mit einem leeren Objekt
                versions = {}

        # Aktualisieren der Daten
        versions[apk_name] = version

        # Zurückschreiben der aktualisierten Daten in die JSON-Datei
        with open(versions_file_path, 'w') as file:
            json.dump(versions, file, indent=4)
        logging.info(f"Versionsdatei aktualisiert: {versions_file_path}")
    except Exception as e:
        logging.error(f"Fehler beim Aktualisieren der Versionsdatei: {e}")

@app.route('/get_apk_versions_info')
def get_apk_versions_info():
    with open('apks/versions.json', 'r') as file:
        versions = json.load(file)
    return jsonify(versions)

@app.route('/upload_apk', methods=['POST'])
@auth.login_required
def upload_apk():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    # Überprüfen des Dateinamens und entsprechendes Umbenennen
    filename = secure_filename(file.filename).lower()
    if 'pokemon' in filename:
        save_path = os.path.join(APK_BASE_PATH, 'pogo.apk')
    elif 'vmapperd' in filename:
        save_path = os.path.join(APK_BASE_PATH, 'vmapperd.apk')
    else:
        return jsonify({'error': 'Invalid file name'}), 400

    # Speichern der Datei
    file.save(save_path)

    # APK-Version auslesen und in JSON-Datei speichern
    apk_version = get_apk_version(save_path)
    if apk_version:
        update_versions_file(os.path.basename(save_path), apk_version, VERSIONS_FILE)
        return jsonify({'message': f'File saved as {os.path.basename(save_path)}, version {apk_version}'}), 200
    else:
        return jsonify({'error': 'Failed to read APK version'}), 500


@app.route('/apk/<apk_name>/download', methods=['GET'])
@auth.login_required
def download_apk(apk_name):
    file_path = os.path.join(APK_BASE_PATH, f'{apk_name}.apk')
    try:
        return send_file(file_path, as_attachment=True)
    except FileNotFoundError:
        return jsonify({'error': 'APK not found'}), 404

if __name__ == '__main__':
    create_tables() 
    app.run(debug=True, host=SERVER_IP, port=SERVER_PORT)
